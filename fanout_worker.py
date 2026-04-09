"""
workers/fanout_worker.py
------------------------
Reads from the Redis Stream using a consumer group.
For each message:
  1. Resolve target (topic → set of emails, or single user email)
  2. For each subscriber email → fetch their push subscription from Redis
  3. Dispatch one delivery job per subscriber (written to a delivery queue stream)
  4. XACK the message on success

Run:
    python -m workers.fanout_worker

Consumer group guarantees at-least-once delivery — if this worker crashes
mid-batch, Redis re-delivers unACKed messages to the next available consumer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket

import redis.asyncio as aioredis

# Allow running as `python workers/fanout_worker.py` or `python -m workers.fanout_worker`
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings
from redis_client import (
    ensure_consumer_group,
    get_redis,
    get_subscription,
    get_topic_members,
)

logger = logging.getLogger("fanout_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [fanout] %(levelname)s %(message)s")

DELIVERY_STREAM = "delivery:stream"
CONSUMER_NAME = f"fanout-{socket.gethostname()}-{os.getpid()}"
BATCH_SIZE = 10
BLOCK_MS = 5000  # block-wait up to 5 s for new messages


async def process_message(r: aioredis.Redis, msg_id: str, fields: dict) -> None:
    event_id = fields.get("event_id", "unknown")
    target_type = fields.get("target_type", "topic")
    target_value = fields.get("target_value", "")
    notification_raw = fields.get("notification", "{}")
    ttl = fields.get("ttl", "3600")
    urgency = fields.get("urgency", "normal")

    logger.info("Processing event_id=%s target=%s:%s", event_id, target_type, target_value)

    # 1. Resolve subscribers
    if target_type == "topic":
        emails = await get_topic_members(r, target_value)
    else:
        # Direct user target
        sub = await get_subscription(r, target_value)
        emails = [target_value] if sub else []

    if not emails:
        logger.warning("event_id=%s — no subscribers found, acking and skipping", event_id)
        await r.xack(settings.stream_key, settings.stream_group, msg_id)
        return

    # 2. For each subscriber, write a delivery task to the delivery stream
    pipe = r.pipeline()
    dispatched = 0
    for email in emails:
        sub = await get_subscription(r, email)
        if not sub or sub.get("status") != "active":
            logger.debug("Skipping %s — no active subscription", email)
            # Count as failed immediately
            pipe.hincrby(f"delivery:{event_id}", "failed", 1)
            continue

        pipe.xadd(
            DELIVERY_STREAM,
            {
                "event_id": event_id,
                "email": email,
                "endpoint": sub["endpoint"],
                "p256dh": sub["p256dh"],
                "auth": sub["auth"],
                "notification": notification_raw,
                "ttl": ttl,
                "urgency": urgency,
            },
        )
        dispatched += 1

    await pipe.execute()
    logger.info("event_id=%s — dispatched %d delivery tasks for %d subscribers", event_id, dispatched, len(emails))

    # 3. ACK the stream message
    await r.xack(settings.stream_key, settings.stream_group, msg_id)


async def run() -> None:
    r = get_redis()
    await ensure_consumer_group(r)

    # Also ensure delivery stream consumer group exists
    try:
        await r.xgroup_create(DELIVERY_STREAM, "delivery_workers", id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    logger.info("Fan-out worker started. consumer=%s stream=%s group=%s",
                CONSUMER_NAME, settings.stream_key, settings.stream_group)

    shutdown = asyncio.Event()

    def _handle_signal(sig, frame):
        logger.info("Received %s, shutting down…", sig)
        shutdown.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not shutdown.is_set():
        try:
            results = await r.xreadgroup(
                groupname=settings.stream_group,
                consumername=CONSUMER_NAME,
                streams={settings.stream_key: ">"},
                count=BATCH_SIZE,
                block=BLOCK_MS,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Stream read error: %s — retrying in 2s", exc)
            await asyncio.sleep(2)
            continue

        if not results:
            continue  # timeout, loop

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    await process_message(r, msg_id, fields)
                except Exception as exc:
                    logger.exception("Failed to process msg_id=%s event_id=%s: %s",
                                     msg_id, fields.get("event_id"), exc)
                    # Don't ACK — Redis will re-deliver after PEL timeout

    logger.info("Fan-out worker stopped.")


if __name__ == "__main__":
    asyncio.run(run())
