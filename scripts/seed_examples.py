"""Load a few starter seeds so a fresh PSE has something to crawl.

Usage: ``python -m scripts.seed_examples``
"""

from __future__ import annotations

import asyncio

from app.db import SessionLocal
from app.seeding.importers import add_domains_as_seeds

# Small, specialized, link-rich sites that crawl politely — adjust to your interests.
EXAMPLE_DOMAINS = [
    "example.com",
    "httpbin.org",
]


async def main() -> None:
    async with SessionLocal() as session:
        added = await add_domains_as_seeds(session, EXAMPLE_DOMAINS)
    print(f"Added {added} new example seed(s): {EXAMPLE_DOMAINS}")


if __name__ == "__main__":
    asyncio.run(main())
