"""Shared test fixtures.

Unit tests need no database. Integration tests need a reachable Postgres; they are
skipped automatically if one isn't available. Point them at a throwaway database via
``TEST_DATABASE_URL`` (async URL), e.g.:

    TEST_DATABASE_URL=postgresql+asyncpg://pse:pse@localhost:5432/pse pytest
"""

from __future__ import annotations

import os

# Make the app bind to the test database BEFORE any app module is imported.
_TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://pse:pse@localhost:5432/pse")
os.environ["DATABASE_URL"] = _TEST_URL
os.environ["PSE_TEST_MODE"] = "1"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: F401,E402  (register tables on Base.metadata)
from app.db import Base, SessionLocal, engine  # noqa: E402

_TABLES = "pages, links, crawl_queue, crawl_jobs, discovered_domains, peers, seeds"


@pytest_asyncio.fixture
async def session():
    """A clean session with the schema created and all tables truncated.

    Skips the test if no Postgres is reachable. Schema creation is idempotent, so
    keeping this function-scoped avoids pytest-asyncio loop-scope mismatches.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        pytest.skip("No Postgres reachable for integration tests")

    async with SessionLocal() as s:
        await s.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        await s.commit()
        yield s
