"""
GitHub webhook adapter.

GitHub's payload shape and signature convention differ from our native
{"event_id", "event_type", "payload"} format, so this route normalizes
GitHub's conventions into the exact same ingest_event() /
process_webhook_event() pipeline used everywhere else in the app. This is
what proves the engine works against a real, independent, un-simulated
webhook sender -- not just our own test scripts.

GitHub specifics:
- Signature header: X-Hub-Signature-256, formatted as "sha256=<hex-hmac>",
  computed with the SAME secret you configure in the GitHub webhook UI.
- X-GitHub-Delivery: a GUID unique per delivery attempt. This is a perfect
  idempotency key -- we use it directly as our event_id.
- X-GitHub-Event: e.g. "push", "ping", "issues". Used as event_type.
- Body: whatever GitHub sends for that event type; stored as-is in payload.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import verify_github_signature
from app.db.database import get_db
from app.schemas.webhook import WebhookIngestResponse
from app.services import webhook_service
from app.workers.webhook_tasks import process_webhook_event

router = APIRouter(prefix="/webhooks/github", tags=["github"])
logger = get_logger(__name__)
settings = get_settings()


@router.post("", response_model=WebhookIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_github_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    provided_signature = request.headers.get("X-Hub-Signature-256")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    github_event_type = request.headers.get("X-GitHub-Event", "unknown")

    if not verify_github_signature(settings.WEBHOOK_SECRET, raw_body, provided_signature):
        logger.warning("github_webhook_invalid_signature", extra={"delivery_id": delivery_id})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    if not delivery_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="missing X-GitHub-Delivery header"
        )

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"raw": raw_body.decode("utf-8", errors="replace")}

    result = webhook_service.ingest_event(
        db,
        event_id=delivery_id,
        event_type=f"github.{github_event_type}",
        payload=payload,
        max_retries=settings.DEFAULT_MAX_RETRIES,
    )

    if result.created:
        process_webhook_event.delay(result.event.id)
        message = "github event accepted and queued for processing"
    else:
        message = "duplicate delivery_id; original processing left untouched"

    return WebhookIngestResponse(event_id=result.event.event_id, status=result.event.status, message=message)
