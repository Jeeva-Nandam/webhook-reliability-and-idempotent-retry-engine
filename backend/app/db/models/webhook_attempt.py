"""
The webhook_attempts table: an append-only log of every processing attempt.

We keep this separate from webhook_events (rather than just overwriting
`error_message` each time) because a mutable "last error" field destroys
history. With a separate table we can answer "show me the full timeline of
attempt 1 -> 500, attempt 2 -> 500, attempt 3 -> 200" which is exactly what
the dashboard's retry-history view needs, and it's how real-world payment
platforms let you debug one event end-to-end.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# See webhook_event.py for why this variant is needed (SQLite test suite).
BigIntegerPK = BigInteger().with_variant(Integer(), "sqlite")
BigIntegerFK = BigInteger().with_variant(Integer(), "sqlite")


class WebhookAttempt(Base):
    __tablename__ = "webhook_attempts"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    webhook_event_id: Mapped[int] = mapped_column(
        BigIntegerFK, ForeignKey("webhook_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WebhookAttempt event_id={self.webhook_event_id} "
            f"attempt={self.attempt_number} status={self.status}>"
        )
