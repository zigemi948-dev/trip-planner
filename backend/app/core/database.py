"""Database engine and FastAPI session lifecycle helpers.

Schema creation is deliberately handled by Alembic migrations, never by the
application process.  Keeping engine creation lazy lets algorithm-only tests
run without needing a PostgreSQL driver or an available database server.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class shared by all persistent domain models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the configured PostgreSQL engine, creating it once per process."""
    global _engine
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL must be configured for database-backed persistence")
        connect_args: dict[str, Any] = {"connect_timeout": settings.db_connect_timeout_seconds}
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory bound to the configured engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that closes each request-scoped database session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def database_is_available() -> bool:
    """Run a tiny connectivity check without exposing configuration details."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
