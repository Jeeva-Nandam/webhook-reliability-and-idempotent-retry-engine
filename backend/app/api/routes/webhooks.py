"""
Webhook ingestion + query API.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import verify_signature
from app.db.database import get_db
from app.db.models import WebhookAttempt, WebhookStatus
from app.schemas.webhook import (
    PaginatedWebhookEvents,
    WebhookAttemptResponse,
    WebhookEventDetailResponse,
    WebhookEventResponse,
    WebhookIngestRequest,
    WebhookIngestResponse,
)
from app.services import webhook_service
from app.workers.webhook_tasks import process_webhook_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)
settings = get_settings()


@router.post("", response_model=WebhookIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_webhook(
    request: Request,
    payload_in: WebhookIngestRequest,
    db: Session = Depends(get_db),
):
    # Signature is verified over the RAW body, not the parsed/re-serialized
    # JSON (re-serialization can change byte-for-byte content).
    raw_body = await request.body()
    provided_signature = request.headers.get(settings.SIGNATURE_HEADER)

    if not verify_signature(settings.WEBHOOK_SECRET, raw_body, provided_signature):
        logger.warning("webhook_invalid_signature", extra={"event_id": payload_in.event_id})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    result = webhook_service.ingest_event(
        db,
        event_id=payload_in.event_id,
        event_type=payload_in.event_type,
        payload=payload_in.payload,
        max_retries=settings.DEFAULT_MAX_RETRIES,
    )

    if result.created:
        # Only enqueue a task for genuinely new events. A duplicate delivery
        # of an event we already have must NOT enqueue a second task.
        process_webhook_event.delay(result.event.id)
        message = "event accepted and queued for processing"
    else:
        message = "duplicate event_id; original processing left untouched"

    return WebhookIngestResponse(event_id=result.event.event_id, status=result.event.status, message=message)


@router.get("", response_model=PaginatedWebhookEvents)
def list_webhooks(
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    settings_ = get_settings()
    page = max(page, 1)
    page_size = min(max(page_size, 1), settings_.MAX_PAGE_SIZE)

    if status_filter and status_filter not in {s.value for s in WebhookStatus}:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status_filter}")

    items, total = webhook_service.list_events(db, status_filter, page, page_size)
    return PaginatedWebhookEvents(
        items=[WebhookEventResponse.model_validate(e) for e in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{event_id}", response_model=WebhookEventDetailResponse)
def get_webhook(event_id: str, db: Session = Depends(get_db)):
    event = webhook_service.get_event_by_event_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    # Attempts are loaded explicitly with their own query (rather than an
    # ORM relationship + lazy loading) to keep the query obvious.
    attempt_rows = (
        db.execute(
            select(WebhookAttempt)
            .where(WebhookAttempt.webhook_event_id == event.id)
            .order_by(WebhookAttempt.attempt_number.asc())
        )
        .scalars()
        .all()
    )

    response = WebhookEventDetailResponse.model_validate(event)
    response.attempts = [WebhookAttemptResponse.model_validate(a) for a in attempt_rows]
    return response


@router.post("/{event_id}/retry", response_model=WebhookEventResponse)
def retry_webhook(event_id: str, db: Session = Depends(get_db)):
    event = webhook_service.get_event_by_event_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    if event.status != WebhookStatus.DEAD.value:
        raise HTTPException(
            status_code=409,
            detail=f"manual retry only allowed for DEAD events (current status: {event.status})",
        )

    updated = webhook_service.manual_retry(db, event)
    process_webhook_event.delay(updated.id)
    logger.info("webhook_manual_retry_triggered", extra={"event_id": event_id})
    return WebhookEventResponse.model_validate(updated)
