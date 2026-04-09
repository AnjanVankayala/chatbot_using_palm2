"""
main.py — Push Notification Service
------------------------------------
Standalone FastAPI application. Completely independent of the email service.

Endpoints
---------
GET  /v1/vapid-key              Return VAPID public key (no auth)
POST /v1/subscribe              Register a browser subscription
DEL  /v1/unsubscribe            Remove a subscription
POST /v1/publish                Queue a push notification (202 Accepted)
GET  /v1/status/{event_id}      Poll delivery status

Static files served at:
  /           → static/index.html   (subscription + test UI)
  /sw.js      → static/sw.js        (service worker, must be at root scope)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from redis_client import close_redis, ensure_consumer_group, get_redis
from routers.push import router as push_router

logger = logging.getLogger("push_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to Redis and create consumer group
    r = get_redis()
    await ensure_consumer_group(r)
    logger.info("Redis connected. Stream consumer group ready.")
    yield
    # Shutdown
    await close_redis()
    logger.info("Redis connection closed.")


app = FastAPI(
    title="Push Notification Service",
    version="1.0.0",
    description="Web Push (VAPID) notification service. Completely independent of the email service.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(push_router)

# Serve sw.js at the root path (required — service workers must be served from the scope they control)
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")

# Serve the test UI
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    r = get_redis()
    try:
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok" if redis_ok else "degraded", "redis": redis_ok}
