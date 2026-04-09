# Push Notification Service

Standalone Web Push notification service. **Completely independent of the email service.**
Uses VAPID + Web Push Protocol to deliver notifications to any browser that has subscribed.

## Architecture

```
Browser
  └─ /v1/subscribe ──────────────────► FastAPI (main.py)
                                            │
                                     Redis HSET sub:<email>
                                     Redis SADD topic:<name>
                                            │
  Frontend /v1/publish ─────────────► FastAPI
                                       XADD notifications:stream
                                            │
                                     fanout_worker.py (reads stream)
                                       SMEMBERS topic:<name>
                                       HGETALL sub:<email>
                                       XADD delivery:stream
                                            │
                                     delivery_worker.py
                                       pywebpush → FCM/Mozilla/Safari
                                            │
                                     Browser Push Service
                                            │
                                     Service Worker (sw.js)
                                       showNotification()
```

## Quick start

### 1. Generate VAPID keys (one-time)

```bash
pip install pywebpush cryptography
python generate_vapid.py
```

Copy the output into `.env`:

```bash
cp .env.example .env
# paste VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY
```

### 2. Start with Docker Compose

```bash
docker-compose up -d
```

This starts: Redis, FastAPI (port 8000), fanout worker, delivery worker.

### 3. Or run locally (no Docker)

```bash
# Terminal 1 — Redis (via Docker)
docker run -d -p 6379:6379 redis:7-alpine

# Terminal 2 — API
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Terminal 3 — Fan-out worker
python -m workers.fanout_worker

# Terminal 4 — Delivery worker
python -m workers.delivery_worker
```

### 4. Open the test UI

```
http://localhost:8000
```

1. Enter your email, bearer token (`dev-token`), and topics
2. Click **Subscribe** — browser asks for notification permission
3. Click **Send notification** to publish a test push
4. Watch the notification arrive in your browser

## API reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/vapid-key` | none | VAPID public key for frontend |
| POST | `/v1/subscribe` | Bearer | Register browser subscription |
| DELETE | `/v1/unsubscribe` | Bearer | Remove subscription |
| POST | `/v1/publish` | Bearer | Queue a push notification (202) |
| GET | `/v1/status/{event_id}` | Bearer | Poll delivery status |
| GET | `/health` | none | Redis health check |

Full interactive docs: `http://localhost:8000/docs`

## Testing on localhost (no deployment needed)

Push notifications work on `http://localhost` in Chrome and Firefox without HTTPS.
You do not need to deploy or use ngrok just to test locally on your Ubuntu VM.

If testing from a different machine on your LAN, use the VM's local IP:
`http://192.168.x.x:8000` — this also works without HTTPS on most browsers when the
page origin is a private IP range.

## Key design decisions

- **Email as user_id**: `sub:<email>` is the Redis key for each subscriber's push credentials.
- **Topics as Redis Sets**: `SADD topic:alerts user@example.com` — fan-out is a single `SMEMBERS`.
- **Two-stream pipeline**: `notifications:stream` (API → fanout) and `delivery:stream` (fanout → delivery). Decouples fan-out from actual HTTP delivery.
- **Idempotency**: `idempotency:<key>` → event_id in Redis (24h TTL). Duplicate publishes return the same event_id.
- **410 Gone cleanup**: delivery worker automatically deletes expired subscriptions from Redis.
- **At-least-once delivery**: Redis consumer groups + unACKed message redelivery.
