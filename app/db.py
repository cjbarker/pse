"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

# Under the test harness we use NullPool so no asyncpg connection outlives the
# (per-test) event loop that created it, avoiding "event loop is closed" on teardown.
_engine_kwargs: dict = {"pool_pre_ping": True, "future": True}
if os.environ.get("PSE_TEST_MODE") == "1":
    _engine_kwargs = {"poolclass": NullPool, "future": True}

engine = create_async_engine(settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a transactional session."""
    async with SessionLocal() as session:
        yield session
