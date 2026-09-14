"""
Shared pytest fixtures.

Unit/integration tests here run against an in-memory SQLite database instead
of PostgreSQL so the suite is self-contained and fast. The docker-compose
CI setup additionally runs a `docker compose up` smoke path against real
Postgres/Redis -- see .github/workflows/ci.yml.

Note: SQLite doesn't enforce true row-level locking the way Postgres does,
so the concurrency test in test_idempotency.py demonstrates the *logical*
race (two callers, one INSERT succeeds) rather than true multi-connection
locking behavior, which is validated for real in the docker-compose
integration environment against Postgres.
"""

import os
from unittest.mock import MagicMock, patch

import pytest  # type: ignore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.db.database import Base  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def no_celery():
    """Prevents tests from needing a real Redis broker by mocking .delay()."""
    with patch("app.workers.webhook_tasks.process_webhook_event.delay", MagicMock()) as mock_delay:
        yield mock_delay
