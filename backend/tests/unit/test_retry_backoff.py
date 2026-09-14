"""Unit tests for exponential backoff math."""

from datetime import UTC, datetime

from app.services.retry_service import (
    compute_backoff_seconds,
    compute_next_retry_at,
    has_retries_remaining,
)


def test_backoff_grows_exponentially_before_capping():
    d0 = compute_backoff_seconds(0)
    d1 = compute_backoff_seconds(1)
    d2 = compute_backoff_seconds(2)
    # jitter adds a small random amount, so compare floors ignoring jitter range
    assert d0 < d1 < d2


def test_backoff_is_capped_at_max_delay():
    from app.core.config import get_settings

    settings = get_settings()
    huge_retry_count = 30
    delay = compute_backoff_seconds(huge_retry_count)
    assert delay <= settings.RETRY_MAX_DELAY_SECONDS + settings.RETRY_JITTER_SECONDS


def test_compute_next_retry_at_is_in_the_future():
    now = datetime.now(UTC)
    next_retry = compute_next_retry_at(retry_count=0, now=now)
    assert next_retry > now


def test_has_retries_remaining():
    assert has_retries_remaining(retry_count=2, max_retries=5) is True
    assert has_retries_remaining(retry_count=5, max_retries=5) is False
    assert has_retries_remaining(retry_count=6, max_retries=5) is False
