"""
Retry / exponential backoff math.

INTERVIEW CONCEPT: exponential backoff with jitter.

Formula: delay = min(base_delay * 2^retry_count, max_delay)

Why exponential and not fixed-interval retries? If a downstream service is
overloaded or down, retrying every second from thousands of events just
adds more load to a system that's already failing -- it can turn a brief
blip into a prolonged outage ("retry storm"). Exponential backoff quickly
backs off to a slow, sustainable retry rate while the downstream service
recovers.

Why jitter? If 10,000 events all failed at the same instant (e.g. a 30
second outage), pure exponential backoff means all 10,000 wake up and
retry at the exact same instant, which recreates the thundering herd we
were trying to avoid. Adding a small random offset spreads those retries
out over a window instead of a single instant.
"""

import random
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings

settings = get_settings()


def compute_backoff_seconds(retry_count: int) -> float:
    base = settings.RETRY_BASE_DELAY_SECONDS
    max_delay = settings.RETRY_MAX_DELAY_SECONDS
    jitter_range = settings.RETRY_JITTER_SECONDS

    delay = min(base * (2**retry_count), max_delay)
    jitter = random.uniform(0, jitter_range)
    return delay + jitter


def compute_next_retry_at(retry_count: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    delay_seconds = compute_backoff_seconds(retry_count)
    return now + timedelta(seconds=delay_seconds)


def has_retries_remaining(retry_count: int, max_retries: int) -> bool:
    return retry_count < max_retries
