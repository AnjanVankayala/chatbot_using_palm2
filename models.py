from __future__ import annotations
 
from datetime import datetime
from typing import Any, Literal
 
from pydantic import BaseModel, EmailStr, Field
 
 
# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------
 
class PushKeys(BaseModel):
    p256dh: str
    auth: str
 
 
class PushSubscriptionObject(BaseModel):
    endpoint: str
    keys: PushKeys
 
 
class SubscribeRequest(BaseModel):
    user_id: EmailStr = Field(..., description="Subscriber's email address used as unique user ID")
    subscription: PushSubscriptionObject
    topics: list[str] = Field(default_factory=list, description="Topic names to subscribe to")
 
 
class SubscribeResponse(BaseModel):
    subscription_id: str
    user_id: str
    topics: list[str]
    status: Literal["active"] = "active"
    created_at: datetime
 
 
# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
 
class NotificationPayload(BaseModel):
    title: str
    body: str
    icon: str = "/icons/notification.png"
    badge: str = "/icons/badge.png"
    data: dict[str, Any] = Field(default_factory=dict)
 
 
class PublishTarget(BaseModel):
    type: Literal["topic", "user"]
    value: str = Field(..., description="Topic name or user email")
 
 
class PublishOptions(BaseModel):
    ttl: int = Field(default=3600, ge=0, description="Seconds before push expires")
    urgency: Literal["very-low", "low", "normal", "high"] = "normal"
 
 
class PublishRequest(BaseModel):
    target: PublishTarget
    notification: NotificationPayload
    options: PublishOptions = Field(default_factory=PublishOptions)
    idempotency_key: str = Field(..., description="Unique key — duplicate calls return the same event_id")
 
 
class PublishResponse(BaseModel):
    event_id: str
    status: Literal["queued", "duplicate"] = "queued"
    target_count: int
    queued_at: datetime
 
 
# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
 
class DeliverySummary(BaseModel):
    targeted: int
    delivered: int
    failed: int
    pending: int
 
 
class StatusResponse(BaseModel):
    event_id: str
    status: Literal["queued", "in_progress", "completed"]
    summary: DeliverySummary
    queued_at: datetime | None = None
    completed_at: datetime | None = None
 
 
# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------
 
class UnsubscribeRequest(BaseModel):
    user_id: EmailStr
    endpoint: str = Field(..., description="Exact FCM/push endpoint to remove")
