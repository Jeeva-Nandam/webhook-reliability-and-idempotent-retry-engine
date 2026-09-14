"""initial schema: webhook_events, webhook_attempts

Revision ID: 0001
Revises:
Create Date: 2026-01-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_webhook_events_event_id"),
    )
    op.create_index("ix_webhook_events_event_id", "webhook_events", ["event_id"])
    op.create_index("ix_webhook_events_status", "webhook_events", ["status"])
    op.create_index("ix_webhook_events_status_next_retry", "webhook_events", ["status", "next_retry_at"])

    op.create_table(
        "webhook_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "webhook_event_id",
            sa.BigInteger(),
            sa.ForeignKey("webhook_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_webhook_attempts_webhook_event_id", "webhook_attempts", ["webhook_event_id"])


def downgrade() -> None:
    op.drop_table("webhook_attempts")
    op.drop_index("ix_webhook_events_status_next_retry", table_name="webhook_events")
    op.drop_index("ix_webhook_events_status", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_id", table_name="webhook_events")
    op.drop_table("webhook_events")
