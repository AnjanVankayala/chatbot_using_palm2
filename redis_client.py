"""
redis_client.py
---------------
Single Redis connection pool + all low-level key helpers.
 
Key schema
----------
sub:<email>                  HASH   endpoint, p256dh, auth, created_at
topic:<topic>                SET    of email addresses
delivery:<event_id>          HASH   targeted, delivered, failed, queued_at, completed_at
idempotency:<idempotency_key> STRING  event_id  (EX 86400)
notifications:stream         STREAM push event messages
"""
 
from __future__ import annotations
 
import json
import redis.asyncio as aioredis
from config import settings
 
# ---------------------------------------------------------------------------
# Connection pool (one per process)
# ---------------------------------------------------------------------------
 
_pool: aioredis.Redis | None = None
 
 
def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _pool
 
 
async def close_redis() -> None:
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
 
 
# ---------------------------------------------------------------------------
# Subscription helpers
# ---------------------------------------------------------------------------
 
def sub_key(email: str) -> str:
    return f"{settings.sub_key_prefix}{email}"
 
 
def topic_key(topic: str) -> str:
    return f"{settings.topic_key_prefix}{topic}"
 
 
async def save_subscription(
    r: aioredis.Redis,
    email: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    topics: list[str],
    created_at: str,
) -> None:
    pipe = r.pipeline()
    pipe.hset(
        sub_key(email),
        mapping={
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "created_at": created_at,
            "status": "active",
        },
    )
    for topic in topics:
        pipe.sadd(topic_key(topic), email)
    await pipe.execute()
 
 
async def get_subscription(r: aioredis.Redis, email: str) -> dict | None:
    data = await r.hgetall(sub_key(email))
    return data if data else None
 
 
async def delete_subscription(r: aioredis.Redis, email: str, endpoint: str) -> bool:
    """Remove a single endpoint. If it matches stored endpoint, delete the whole sub key."""
    stored = await r.hget(sub_key(email), "endpoint")
    if stored != endpoint:
        return False
    await r.delete(sub_key(email))
    return True
 
 
async def get_topic_members(r: aioredis.Redis, topic: str) -> list[str]:
    return list(await r.smembers(topic_key(topic)))
 
 
async def remove_from_all_topics(r: aioredis.Redis, email: str) -> None:
    """Clean up a user from every topic set (called after 410 Gone from push service)."""
    pattern = f"{settings.topic_key_prefix}*"
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor, match=pattern, count=100)
        for key in keys:
            await r.srem(key, email)
        if cursor == 0:
            break
 
 
# ---------------------------------------------------------------------------
# Delivery tracking helpers
# ---------------------------------------------------------------------------
 
def delivery_key(event_id: str) -> str:
    return f"{settings.delivery_key_prefix}{event_id}"
 
 
async def init_delivery(
    r: aioredis.Redis,
    event_id: str,
    targeted: int,
    queued_at: str,
) -> None:
    await r.hset(
        delivery_key(event_id),
        mapping={
            "targeted": targeted,
            "delivered": 0,
            "failed": 0,
            "queued_at": queued_at,
            "completed_at": "",
        },
    )
 
 
async def increment_delivery(
    r: aioredis.Redis, event_id: str, field: str  # "delivered" or "failed"
) -> None:
    pipe = r.pipeline()
    pipe.hincrby(delivery_key(event_id), field, 1)
    await pipe.execute()
 
    # Check if all resolved → mark completed
    data = await r.hgetall(delivery_key(event_id))
    targeted = int(data.get("targeted", 0))
    delivered = int(data.get("delivered", 0))
    failed = int(data.get("failed", 0))
    if delivered + failed >= targeted and not data.get("completed_at"):
        from datetime import datetime, timezone
        await r.hset(
            delivery_key(event_id),
            "completed_at",
            datetime.now(timezone.utc).isoformat(),
        )
 
 
async def get_delivery_status(r: aioredis.Redis, event_id: str) -> dict | None:
    data = await r.hgetall(delivery_key(event_id))
    return data if data else None
 
 
# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------
 
async def check_idempotency(r: aioredis.Redis, key: str) -> str | None:
    """Return existing event_id if this key was already used, else None."""
    return await r.get(f"idempotency:{key}")
 
 
async def set_idempotency(r: aioredis.Redis, key: str, event_id: str, ttl: int = 86400) -> None:
    await r.set(f"idempotency:{key}", event_id, ex=ttl)
 
 
# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------
 
async def publish_to_stream(r: aioredis.Redis, fields: dict) -> str:
    """Write a message to the notifications stream, return the message ID."""
    # Serialize nested dicts to JSON strings for Redis
    safe = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}
    msg_id = await r.xadd(settings.stream_key, safe)
    return msg_id
 
 
async def ensure_consumer_group(r: aioredis.Redis) -> None:
    """Create the consumer group if it doesn't exist. Safe to call multiple times."""
    try:
        await r.xgroup_create(settings.stream_key, settings.stream_group, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

