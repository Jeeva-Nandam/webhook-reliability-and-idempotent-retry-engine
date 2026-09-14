"""
Integration tests for idempotent ingestion and the state machine as used
through the service layer (not just in isolation).
"""

from app.db.models import WebhookStatus
from app.services import webhook_service


def test_first_ingestion_creates_event(db_session):
    result = webhook_service.ingest_event(
        db_session, event_id="evt_1", event_type="payment.succeeded", payload={"amount": 500}, max_retries=5
    )
    assert result.created is True
    assert result.event.status == WebhookStatus.PENDING.value


def test_duplicate_event_id_does_not_create_second_row(db_session):
    webhook_service.ingest_event(db_session, "evt_dup", "payment.succeeded", {"amount": 1}, max_retries=5)
    result_2 = webhook_service.ingest_event(
        db_session, "evt_dup", "payment.succeeded", {"amount": 1}, max_retries=5
    )

    assert result_2.created is False

    from sqlalchemy import func, select

    from app.db.models import WebhookEvent

    count = db_session.execute(
        select(func.count()).select_from(WebhookEvent).where(WebhookEvent.event_id == "evt_dup")
    ).scalar_one()
    assert count == 1


def test_claim_for_processing_is_exclusive(db_session):
    result = webhook_service.ingest_event(db_session, "evt_claim", "t", {}, max_retries=5)
    event_id = result.event.id

    first_claim = webhook_service.claim_event_for_processing(db_session, event_id)
    second_claim = webhook_service.claim_event_for_processing(db_session, event_id)

    assert first_claim is not None
    assert first_claim.status == WebhookStatus.PROCESSING.value
    # Second caller should NOT be able to claim an already-PROCESSING event.
    assert second_claim is None


def test_manual_retry_only_allowed_from_dead(db_session):
    result = webhook_service.ingest_event(db_session, "evt_manual", "t", {}, max_retries=5)
    event = result.event

    import pytest

    with pytest.raises(ValueError):
        webhook_service.manual_retry(db_session, event)  # still PENDING, not DEAD

    event.status = WebhookStatus.DEAD.value
    db_session.add(event)
    db_session.commit()

    revived = webhook_service.manual_retry(db_session, event)
    assert revived.status == WebhookStatus.PENDING.value
    assert revived.retry_count == 0
