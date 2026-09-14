"""
Integration tests for the failure -> retry -> success/dead lifecycle,
exercised entirely through the service layer functions that the Celery task
also calls (see app/workers/webhook_tasks.py).
"""

from datetime import UTC, datetime, timedelta

from app.db.models import WebhookStatus
from app.services import retry_service, webhook_service


def _make_processing_event(db_session, event_id="evt_retry", max_retries=3):
    result = webhook_service.ingest_event(db_session, event_id, "t", {}, max_retries=max_retries)
    return webhook_service.claim_event_for_processing(db_session, result.event.id)


def test_failure_schedules_retry_when_budget_remains(db_session):
    event = _make_processing_event(db_session, max_retries=3)
    next_retry_at = retry_service.compute_next_retry_at(event.retry_count)

    updated = webhook_service.mark_failed_and_schedule_retry(db_session, event, "boom", next_retry_at)

    assert updated.status == WebhookStatus.FAILED.value
    assert updated.retry_count == 1
    assert updated.next_retry_at is not None


def test_exhausting_retries_moves_to_dead(db_session):
    event = _make_processing_event(db_session, event_id="evt_dead", max_retries=2)

    # attempt 1 fails, retry scheduled
    event = webhook_service.claim_event_for_processing(db_session, event.id) or event
    webhook_service.mark_failed_and_schedule_retry(
        db_session, event, "boom", retry_service.compute_next_retry_at(event.retry_count)
    )

    # attempt 2 (retry_count now 1) fails -> exhausts max_retries=2 -> DEAD
    reclaimed = webhook_service.claim_event_for_processing(db_session, event.id)
    final = webhook_service.mark_failed_and_schedule_retry(
        db_session, reclaimed, "boom again", next_retry_at=None
    )

    assert final.status == WebhookStatus.DEAD.value
    assert final.retry_count == 2


def test_retry_then_success(db_session):
    event = _make_processing_event(db_session, event_id="evt_recovers", max_retries=5)
    webhook_service.mark_failed_and_schedule_retry(
        db_session, event, "transient", retry_service.compute_next_retry_at(event.retry_count)
    )

    reclaimed = webhook_service.claim_event_for_processing(db_session, event.id)
    assert reclaimed.status == WebhookStatus.PROCESSING.value

    succeeded = webhook_service.mark_success(db_session, reclaimed)
    assert succeeded.status == WebhookStatus.SUCCESS.value
    assert succeeded.processed_at is not None


def test_due_retries_query_only_returns_events_past_next_retry_at(db_session):
    event = _make_processing_event(db_session, event_id="evt_due", max_retries=5)
    future_retry = datetime.now(UTC) + timedelta(hours=1)
    webhook_service.mark_failed_and_schedule_retry(db_session, event, "boom", future_retry)

    due_now = webhook_service.get_due_retries(db_session, now=datetime.now(UTC))
    assert all(e.event_id != "evt_due" for e in due_now)

    due_later = webhook_service.get_due_retries(db_session, now=future_retry + timedelta(minutes=1))
    assert any(e.event_id == "evt_due" for e in due_later)
