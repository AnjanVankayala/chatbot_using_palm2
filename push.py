"""
routers/push.py
---------------
POST   /v1/subscribe          Register a browser push subscription
DELETE /v1/unsubscribe        Remove a subscription
POST   /v1/publish            Queue a push notification
GET    /v1/status/{event_id}  Poll delivery status
GET    /v1/vapid-key          Return the VAPID public key (for frontend)
"""
 
from __future__ import annotations
 
import json
from datetime import datetime, timezone
 
from fastapi import APIRouter, Depends, HTTPException, status
 
from auth import require_auth
from config import settings
from models import (
    PublishRequest,
    PublishResponse,
    StatusResponse,
    DeliverySummary,
    SubscribeRequest,
    SubscribeResponse,
    UnsubscribeRequest,
)
from redis_client import (
    check_idempotency,
    delete_subscription,
    get_delivery_status,
    get_redis,
    get_subscription,
    get_topic_members,
    init_delivery,
    publish_to_stream,
    remove_from_all_topics,
    save_subscription,
    set_idempotency,
)
 
router = APIRouter(prefix="/v1")
 
 
# ---------------------------------------------------------------------------
# GET /v1/vapid-key  — public, no auth
# ---------------------------------------------------------------------------
 
@router.get("/vapid-key")
async def vapid_key():
    """Return the VAPID public key so the frontend can call pushManager.subscribe()."""
    if not settings.vapid_public_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VAPID keys not configured. Set VAPID_PUBLIC_KEY in .env",
        )
    return {"public_key": settings.vapid_public_key}
 
 
# ---------------------------------------------------------------------------
# POST /v1/subscribe
# ---------------------------------------------------------------------------
 
@router.post("/subscribe", response_model=SubscribeResponse, status_code=status.HTTP_201_CREATED)
async def subscribe(
    body: SubscribeRequest,
    _: str = Depends(require_auth),
):
    r = get_redis()
    now = datetime.now(timezone.utc)
 
    await save_subscription(
        r,
        email=str(body.user_id),
        endpoint=body.subscription.endpoint,
        p256dh=body.subscription.keys.p256dh,
        auth=body.subscription.keys.auth,
        topics=body.topics,
        created_at=now.isoformat(),
    )
 
    subscription_id = f"sub_{str(body.user_id).replace('@', '_').replace('.', '_')}"
 
    return SubscribeResponse(
        subscription_id=subscription_id,
        user_id=str(body.user_id),
        topics=body.topics,
        created_at=now,
    )
 
 
# ---------------------------------------------------------------------------
# DELETE /v1/unsubscribe
# ---------------------------------------------------------------------------
 
@router.delete("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    body: UnsubscribeRequest,
    _: str = Depends(require_auth),
):
    r = get_redis()
    email = str(body.user_id)
 
    removed = await delete_subscription(r, email, body.endpoint)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found or endpoint mismatch",
        )
 
    await remove_from_all_topics(r, email)
    return {"status": "unsubscribed", "user_id": email}
 
 
# ---------------------------------------------------------------------------
# POST /v1/publish
# ---------------------------------------------------------------------------
 
@router.post("/publish", response_model=PublishResponse, status_code=status.HTTP_202_ACCEPTED)
async def publish(
    body: PublishRequest,
    _: str = Depends(require_auth),
):
    r = get_redis()
    now = datetime.now(timezone.utc)
 
    # Idempotency check
    existing = await check_idempotency(r, body.idempotency_key)
    if existing:
        # Return existing event info without re-queueing
        delivery = await get_delivery_status(r, existing)
        targeted = int(delivery.get("targeted", 0)) if delivery else 0
        return PublishResponse(
            event_id=existing,
            status="duplicate",
            target_count=targeted,
            queued_at=datetime.fromisoformat(delivery["queued_at"]) if delivery else now,
        )
 
    # Resolve target count (so we can return it immediately in the 202)
    if body.target.type == "topic":
        members = await get_topic_members(r, body.target.value)
    else:
        # Single user target
        sub = await get_subscription(r, body.target.value)
        members = [body.target.value] if sub else []
 
    target_count = len(members)
    event_id = body.idempotency_key  # use the caller-supplied idempotency key as event_id
 
    # Initialize delivery tracking in Redis
    await init_delivery(r, event_id, target_count, now.isoformat())
 
    # Write to Redis Stream — fan-out worker will pick this up
    await publish_to_stream(
        r,
        {
            "event_id": event_id,
            "target_type": body.target.type,
            "target_value": body.target.value,
            "notification": json.dumps(body.notification.model_dump()),
            "ttl": str(body.options.ttl),
            "urgency": body.options.urgency,
            "queued_at": now.isoformat(),
        },
    )
 
    # Store idempotency key (24h TTL)
    await set_idempotency(r, body.idempotency_key, event_id)
 
    return PublishResponse(
        event_id=event_id,
        status="queued",
        target_count=target_count,
        queued_at=now,
    )
 
 
# ---------------------------------------------------------------------------
# GET /v1/status/{event_id}
# ---------------------------------------------------------------------------
 
@router.get("/status/{event_id}", response_model=StatusResponse)
async def get_status(
    event_id: str,
    _: str = Depends(require_auth),
):
    r = get_redis()
    data = await get_delivery_status(r, event_id)
 
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No delivery record found for event_id '{event_id}'",
        )
 
    targeted = int(data.get("targeted", 0))
    delivered = int(data.get("delivered", 0))
    failed = int(data.get("failed", 0))
    pending = max(0, targeted - delivered - failed)
    completed_at_raw = data.get("completed_at", "")
 
    if completed_at_raw:
        overall_status = "completed"
        completed_at = datetime.fromisoformat(completed_at_raw)
    elif delivered + failed > 0:
        overall_status = "in_progress"
        completed_at = None
    else:
        overall_status = "queued"
        completed_at = None
 
    return StatusResponse(
        event_id=event_id,
        status=overall_status,
        summary=DeliverySummary(
            targeted=targeted,
            delivered=delivered,
            failed=failed,
            pending=pending,
        ),
        queued_at=datetime.fromisoformat(data["queued_at"]) if data.get("queued_at") else None,
        completed_at=completed_at,
    )
