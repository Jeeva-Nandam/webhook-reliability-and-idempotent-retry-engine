"""Unit tests for the event state machine transition rules."""

from app.db.models import WebhookStatus, can_transition


def test_pending_can_go_to_processing():
    assert can_transition(WebhookStatus.PENDING, WebhookStatus.PROCESSING) is True


def test_pending_cannot_go_directly_to_success():
    assert can_transition(WebhookStatus.PENDING, WebhookStatus.SUCCESS) is False


def test_processing_can_go_to_success_or_failed():
    assert can_transition(WebhookStatus.PROCESSING, WebhookStatus.SUCCESS) is True
    assert can_transition(WebhookStatus.PROCESSING, WebhookStatus.FAILED) is True


def test_success_is_terminal():
    for target in WebhookStatus:
        assert can_transition(WebhookStatus.SUCCESS, target) is False


def test_failed_can_retry_or_die():
    assert can_transition(WebhookStatus.FAILED, WebhookStatus.PROCESSING) is True
    assert can_transition(WebhookStatus.FAILED, WebhookStatus.DEAD) is True


def test_dead_can_only_go_to_pending_via_manual_retry():
    assert can_transition(WebhookStatus.DEAD, WebhookStatus.PENDING) is True
    assert can_transition(WebhookStatus.DEAD, WebhookStatus.PROCESSING) is False
