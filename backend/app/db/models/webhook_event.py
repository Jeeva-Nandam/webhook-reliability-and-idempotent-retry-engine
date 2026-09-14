"""
The webhook_events table: the single source of truth for every event's
lifecycle.
"""

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Use native JSONB on PostgreSQL (production/CI), but fall back to generic
# JSON on other dialects (SQLite) so unit tests can run without a real
# Postgres instance. Only PostgreSQL is used in production.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

# SQLite only auto-increments a rowid-aliased INTEGER PRIMARY KEY, not
# BIGINT -- so plain BigInteger primary keys silently fail to autoincrement
# under SQLite (used only for the fast unit/integration test suite).
# BigInteger is still used in real deployments (PostgreSQL).
BigIntegerPK = BigInteger().with_variant(Integer(), "sqlite")


class WebhookStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DEAD = "DEAD"


# INTERVIEW CONCEPT: explicit state machine.
# Only these transitions are allowed. Anything else is a bug and should be
# rejected loudly rather than silently corrupting event state.
ALLOWED_TRANSITIONS: dict[WebhookStatus, set[WebhookStatus]] = {
    WebhookStatus.PENDING: {WebhookStatus.PROCESSING},
    WebhookStatus.PROCESSING: {WebhookStatus.SUCCESS, WebhookStatus.FAILED},
    WebhookStatus.FAILED: {WebhookStatus.PROCESSING, WebhookStatus.DEAD},
    WebhookStatus.SUCCESS: set(),  # terminal
    WebhookStatus.DEAD: {WebhookStatus.PENDING},  # only via manual retry
}


def can_transition(from_status: WebhookStatus, to_status: WebhookStatus) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        # Composite index for the retry scheduler's most common query:
        # "give me FAILED events whose next_retry_at has passed."
        Index("ix_webhook_events_status_next_retry", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)

    # The external idempotency key. UNIQUE is what actually prevents
    # duplicate processing -- see app/services/webhook_service.py.
    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WebhookStatus.PENDING.value, index=True
    )

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WebhookEvent id={self.id} event_id={self.event_id} status={self.status}>"
