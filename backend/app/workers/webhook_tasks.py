"""
Celery tasks: the actual asynchronous processing logic.
"""

from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.services import retry_service, webhook_service
from app.services.downstream_service import (
    DownstreamServerError,
    DownstreamTimeoutError,
    call_downstream,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
settings = get_settings()


@celery_app.task(name="app.workers.webhook_tasks.process_webhook_event", bind=True, max_retries=0)
def process_webhook_event(self, event_db_id: int, downstream_mode: str = "random") -> str:
    """
    Processes a single webhook event:
      1. Atomically claim it (PENDING/FAILED -> PROCESSING).
      2. Call the (simulated) downstream service.
      3. On success -> SUCCESS. On failure -> FAILED + schedule retry, or DEAD.

    Note: we do NOT use Celery's built-in automatic task retry here on
    purpose. This project implements retry scheduling manually (see
    retry_service.py) so the backoff math and state transitions are
    explicit and visible, rather than hidden inside a framework decorator.
    """
    db = SessionLocal()
    started_at = datetime.now(UTC)
    try:
        event = webhook_service.claim_event_for_processing(db, event_db_id)
        if event is None:
            # Another worker already claimed this event (or it moved on).
            logger.info("process_webhook_event_skip_already_claimed", extra={"event_db_id": event_db_id})
            return "skipped_already_claimed"

        attempt_number = event.retry_count + 1
        logger.info(
            "process_webhook_event_start",
            extra={"event_id": event.event_id, "attempt_number": attempt_number},
        )

        try:
            response_code = call_downstream(mode=downstream_mode)
        except (DownstreamServerError, DownstreamTimeoutError) as exc:
            completed_at = datetime.now(UTC)
            error_message = str(exc)
            response_code = getattr(exc, "status_code", None)

            webhook_service.record_attempt(
                db, event, attempt_number, "FAILED", started_at, completed_at, response_code, error_message
            )

            if retry_service.has_retries_remaining(event.retry_count, event.max_retries):
                next_retry_at = retry_service.compute_next_retry_at(event.retry_count)
                webhook_service.mark_failed_and_schedule_retry(db, event, error_message, next_retry_at)
                logger.warning(
                    "process_webhook_event_failed_will_retry",
                    extra={
                        "event_id": event.event_id,
                        "retry_count": event.retry_count,
                        "next_retry_at": next_retry_at.isoformat(),
                        "error": error_message,
                    },
                )
                return "failed_will_retry"
            else:
                webhook_service.mark_failed_and_schedule_retry(db, event, error_message, next_retry_at=None)
                logger.error(
                    "process_webhook_event_dead",
                    extra={
                        "event_id": event.event_id,
                        "retry_count": event.retry_count,
                        "error": error_message,
                    },
                )
                return "dead"

        # success path
        completed_at = datetime.now(UTC)
        webhook_service.record_attempt(
            db, event, attempt_number, "SUCCESS", started_at, completed_at, response_code, None
        )
        webhook_service.mark_success(db, event)
        duration_ms = (completed_at - started_at).total_seconds() * 1000
        logger.info(
            "process_webhook_event_success",
            extra={"event_id": event.event_id, "duration_ms": duration_ms},
        )
        return "success"
    finally:
        db.close()


@celery_app.task(name="app.workers.webhook_tasks.sweep_due_retries")
def sweep_due_retries() -> int:
    """
    Periodic task (via Celery Beat): finds FAILED events whose next_retry_at
    has passed and re-enqueues them for processing. This is the mechanism
    that actually turns "next_retry_at" into a real retry attempt.
    """
    db = SessionLocal()
    try:
        due = webhook_service.get_due_retries(db)
        for event in due:
            process_webhook_event.delay(event.id)
        if due:
            logger.info("sweep_due_retries_enqueued", extra={"count": len(due)})
        return len(due)
    finally:
        db.close()
