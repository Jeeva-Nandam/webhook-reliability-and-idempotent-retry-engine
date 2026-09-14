"""
SQLAlchemy engine + session management.

We use SQLAlchemy 2.x's typed ORM style. The engine holds a connection pool;
sessions are short-lived units of work borrowed from that pool per-request
or per-task. We never share a session across threads/requests.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# pool_pre_ping avoids handing out dead connections after e.g. a DB restart.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Base class all ORM models inherit from."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
