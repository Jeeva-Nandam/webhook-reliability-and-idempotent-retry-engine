"""
Core webhook business logic: idempotent ingestion, state transitions, and
queries used by the API layer. Kept independent of FastAPI so it's directly
unit-testable and reusable from Celery tasks.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import WebhookAttempt, WebhookEvent, WebhookStatus, can_transition

logger = get_logger(__name__)


class DuplicateEventReturn:
    """Marker wrapper so callers can tell 'newly created' apart from 'already existed'."""

    def __init__(self, event: WebhookEvent, created: bool):
        self.event = event
        self.created = created


def ingest_event(
    db: Session, event_id: str, event_type: str, payload: dict, max_retries: int
) -> DuplicateEventReturn:
    """
    Idempotent event ingestion.

    INTERVIEW CONCEPT: idempotency via database constraint, not
    check-then-act. See app/db/models/webhook_event.py for the UNIQUE
    constraint on event_id. Two concurrent requests for the same event_id
    can both reach this function; only one INSERT will succeed. We handle
    the resulting IntegrityError by fetching the row the other request
    created, so both callers get a consistent answer.
    """
    event = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        status=WebhookStatus.PENDING.value,
        max_retries=max_retries,
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
        logger.info("webhook_ingested", extra={"event_id": event_id, "is_new": True})
        return DuplicateEventReturn(event=event, created=True)
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id)).scalar_one()
        logger.info("webhook_duplicate_detected", extra={"event_id": event_id, "is_new": False})
        return DuplicateEventReturn(event=existing, created=False)


def claim_event_for_processing(db: Session, event_id: int) -> WebhookEvent | None:
    """
    Atomically claims an event for processing using a conditional UPDATE
    (compare-and-swap), so that if two workers race to process the same
    event, only one succeeds.

    INTERVIEW CONCEPT: optimistic concurrency via UPDATE ... WHERE status IN (...).
    The WHERE clause acts as the "compare"; the SET acts as the "swap." Both
    happen in a single atomic SQL statement, so there's no window between
    "check status" and "change status" for another worker to sneak in.
    """
    result = db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.id == event_id,
            WebhookEvent.status.in_([WebhookStatus.PENDING.value, WebhookStatus.FAILED.value]),
        )
        .values(status=WebhookStatus.PROCESSING.value, last_attempt_at=datetime.now(UTC))
        .returning(WebhookEvent.id)
    )
    claimed_id = result.scalar_one_or_none()
    db.commit()
    if claimed_id is None:
        logger.info("webhook_claim_failed_already_taken", extra={"event_id": event_id})
        return None
    return db.get(WebhookEvent, claimed_id)


def mark_success(db: Session, event: WebhookEvent) -> WebhookEvent:
    if not can_transition(WebhookStatus(event.status), WebhookStatus.SUCCESS):
        raise ValueError(f"invalid transition {event.status} -> SUCCESS")
    event.status = WebhookStatus.SUCCESS.value
    event.processed_at = datetime.now(UTC)
    event.error_message = None
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def mark_failed_and_schedule_retry(
    db: Session, event: WebhookEvent, error_message: str, next_retry_at: datetime | None
) -> WebhookEvent:
    if not can_transition(WebhookStatus(event.status), WebhookStatus.FAILED):
        raise ValueError(f"invalid transition {event.status} -> FAILED")

    event.status = WebhookStatus.FAILED.value
    event.retry_count += 1
    event.error_message = error_message
    event.next_retry_at = next_retry_at

    if next_retry_at is None:
        # exhausted retries -> dead-letter
        if not can_transition(WebhookStatus.FAILED, WebhookStatus.DEAD):
            raise ValueError("invalid transition FAILED -> DEAD")
        event.status = WebhookStatus.DEAD.value

    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def record_attempt(
    db: Session,
    event: WebhookEvent,
    attempt_number: int,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    response_code: int | None,
    error_message: str | None,
) -> WebhookAttempt:
    attempt = WebhookAttempt(
        webhook_event_id=event.id,
        attempt_number=attempt_number,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        response_code=response_code,
        error_message=error_message,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def manual_retry(db: Session, event: WebhookEvent) -> WebhookEvent:
    """
    Administrator-triggered retry of a DEAD event: DEAD -> PENDING, resets
    retry_count so it gets a fresh full retry budget, and clears next_retry_at
    so the scheduler/worker can pick it up immediately.
    """
    if not can_transition(WebhookStatus(event.status), WebhookStatus.PENDING):
        raise ValueError(
            f"invalid transition {event.status} -> PENDING (manual retry only allowed from DEAD)"
        )
    event.status = WebhookStatus.PENDING.value
    event.retry_count = 0
    event.next_retry_at = None
    event.error_message = None
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def get_event_by_event_id(db: Session, event_id: str) -> WebhookEvent | None:
    return db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id)).scalar_one_or_none()


def list_events(db: Session, status: str | None, page: int, page_size: int) -> tuple[list[WebhookEvent], int]:
    query = select(WebhookEvent)
    count_query = select(func.count()).select_from(WebhookEvent)
    if status:
        query = query.where(WebhookEvent.status == status)
        count_query = count_query.where(WebhookEvent.status == status)

    total = db.execute(count_query).scalar_one()
    items = (
        db.execute(
            query.order_by(WebhookEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(items), total


def get_due_retries(db: Session, now: datetime | None = None, limit: int = 100) -> list[WebhookEvent]:
    """Events the scheduler should re-enqueue: FAILED and past their next_retry_at."""
    now = now or datetime.now(UTC)
    return list(
        db.execute(
            select(WebhookEvent)
            .where(WebhookEvent.status == WebhookStatus.FAILED.value, WebhookEvent.next_retry_at <= now)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def get_metrics_summary(db: Session) -> dict:
    rows = db.execute(select(WebhookEvent.status, func.count()).group_by(WebhookEvent.status)).all()
    counts = {status: count for status, count in rows}
    total = sum(counts.values())
    success = counts.get(WebhookStatus.SUCCESS.value, 0)
    return {
        "total_events": total,
        "success_count": success,
        "failed_count": counts.get(WebhookStatus.FAILED.value, 0),
        "dead_count": counts.get(WebhookStatus.DEAD.value, 0),
        "processing_count": counts.get(WebhookStatus.PROCESSING.value, 0),
        "pending_count": counts.get(WebhookStatus.PENDING.value, 0),
        "success_rate": (success / total * 100.0) if total else 0.0,
    }
