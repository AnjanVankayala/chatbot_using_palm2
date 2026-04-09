"""
workers/delivery_worker.py
--------------------------
Reads from the delivery:stream consumer group.
For each task:
  1. Build the Web Push payload (VAPID signed, AES-GCM encrypted via pywebpush)
  2. POST to the subscriber's push endpoint (FCM / Mozilla / Safari)
  3. On 201 Created  → XACK + increment delivery:delivered counter
  4. On 410 Gone     → XACK + delete subscription from Redis + increment failed
  5. On other errors → do NOT ack (Redis will redeliver after PEL timeout)

Run:
    python -m workers.delivery_worker
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from pywebpush import WebPusher, webpush, WebPushException

from config import settings
from redis_client import (
    close_redis,
    get_redis,
    increment_delivery,
    remove_from_all_topics,
)

logger = logging.getLogger("delivery_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [delivery] %(levelname)s %(message)s")

DELIVERY_STREAM = "delivery:stream"
DELIVERY_GROUP = "delivery_workers"
CONSUMER_NAME = f"delivery-{socket.gethostname()}-{os.getpid()}"
BATCH_SIZE = 20
BLOCK_MS = 5000


def send_web_push(endpoint: str, p256dh: str, auth: str, payload: dict, ttl: int, urgency: str) -> int:
    """
    Send a VAPID-signed encrypted push notification using pywebpush.
    Returns the HTTP status code from the push service.
    """
    subscription_info = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": p256dh,
            "auth": auth,
        },
    }

    try:
        resp = webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={
                "sub": settings.vapid_subject,
            },
            ttl=ttl,
            headers={"Urgency": urgency},
        )
        return resp.status_code
    except WebPushException as exc:
        # pywebpush raises WebPushException for non-2xx responses
        status_code = exc.response.status_code if exc.response else 500
        logger.warning("WebPushException status=%s: %s", status_code, exc)
        return status_code


async def process_delivery(r, msg_id: str, fields: dict) -> bool:
    """
    Returns True if message should be ACKed (success or permanent failure),
    False if it should be left for re-delivery.
    """
    event_id = fields.get("event_id", "")
    email = fields.get("email", "")
    endpoint = fields.get("endpoint", "")
    p256dh = fields.get("p256dh", "")
    auth_secret = fields.get("auth", "")
    notification_raw = fields.get("notification", "{}")
    ttl = int(fields.get("ttl", 3600))
    urgency = fields.get("urgency", "normal")

    try:
        notification = json.loads(notification_raw)
    except json.JSONDecodeError:
        logger.error("Invalid notification JSON for event_id=%s", event_id)
        await increment_delivery(r, event_id, "failed")
        return True  # permanent failure, ack it

    logger.info("Sending push event_id=%s → %s", event_id, email)

    # Run blocking pywebpush call in a thread pool (it uses requests under the hood)
    loop = asyncio.get_event_loop()
    try:
        status_code = await loop.run_in_executor(
            None,
            send_web_push,
            endpoint,
            p256dh,
            auth_secret,
            notification,
            ttl,
            urgency,
        )
    except Exception as exc:
        logger.exception("Unexpected error sending push to %s: %s", email, exc)
        # Transient error — do not ack, let Redis redeliver
        return False

    if status_code in (200, 201):
        logger.info("Push delivered event_id=%s → %s (status=%s)", event_id, email, status_code)
        await increment_delivery(r, event_id, "delivered")
        return True  # success → ack

    elif status_code == 410:
        # Subscription expired — clean up Redis and mark failed
        logger.warning("Subscription expired (410) for %s — removing from Redis", email)
        await r.delete(f"sub:{email}")
        await remove_from_all_topics(r, email)
        await increment_delivery(r, event_id, "failed")
        return True  # permanent failure → ack

    elif status_code == 429:
        logger.warning("Rate limited (429) for %s — will retry", email)
        return False  # transient → do not ack

    elif 400 <= status_code < 500:
        # Other 4xx — permanent failure
        logger.error("Permanent push failure (status=%s) for %s", status_code, email)
        await increment_delivery(r, event_id, "failed")
        return True

    else:
        # 5xx or unknown — transient, retry
        logger.warning("Transient push failure (status=%s) for %s — will retry", status_code, email)
        return False


async def run() -> None:
    r = get_redis()

    # Ensure consumer group exists
    try:
        await r.xgroup_create(DELIVERY_STREAM, DELIVERY_GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    logger.info("Delivery worker started. consumer=%s stream=%s", CONSUMER_NAME, DELIVERY_STREAM)

    shutdown = asyncio.Event()

    def _handle_signal(sig, frame):
        logger.info("Received %s, shutting down…", sig)
        shutdown.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not shutdown.is_set():
        try:
            results = await r.xreadgroup(
                groupname=DELIVERY_GROUP,
                consumername=CONSUMER_NAME,
                streams={DELIVERY_STREAM: ">"},
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
            continue

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    should_ack = await process_delivery(r, msg_id, fields)
                    if should_ack:
                        await r.xack(DELIVERY_STREAM, DELIVERY_GROUP, msg_id)
                except Exception as exc:
                    logger.exception("Unhandled error in delivery for msg_id=%s: %s", msg_id, exc)

    await close_redis()
    logger.info("Delivery worker stopped.")


if __name__ == "__main__":
    asyncio.run(run())
