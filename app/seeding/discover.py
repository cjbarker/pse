"""Self-seeding: harvest outbound domains from clicked search results.

When a user clicks a result (via the tracked ``/go`` redirect), the domains that
the clicked page links *out* to are candidates for expanding the index along the
user's demonstrated interests. Out-of-scope domains land in the
`discovered_domains` review queue (or become seeds directly when
``SELF_SEED_AUTO_ADD`` is on). In-scope domains are ignored (already covered).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawler.scope import match_scope, rule_from_seed
from app.models import DiscoveredDomain, DiscoveryStatus, Link, Seed
from app.seeding.importers import add_domains_as_seeds
from app.urls import registrable_domain


async def record_click(session: AsyncSession, page_id: int) -> int:
    """Process a click on ``page_id``; return how many new domains were queued/seeded."""
    seeds = (await session.execute(select(Seed).where(Seed.enabled.is_(True)))).scalars().all()
    rules = [rule_from_seed(s) for s in seeds]
    seed_domains = {r.domain for r in rules if r.domain}

    dst_urls = (
        (await session.execute(select(Link.dst_url).where(Link.src_page_id == page_id)))
        .scalars()
        .all()
    )

    candidates: set[str] = set()
    for url in dst_urls:
        if match_scope(url, rules) is not None:
            continue  # already in scope
        dom = registrable_domain(url)
        if dom and dom not in seed_domains:
            candidates.add(dom)

    if not candidates:
        return 0

    if settings.self_seed_auto_add:
        return await add_domains_as_seeds(session, sorted(candidates))

    # Otherwise queue for manual review, bumping times_seen on repeats.
    added = 0
    for dom in sorted(candidates):
        stmt = (
            pg_insert(DiscoveredDomain)
            .values(
                domain=dom,
                source_page_id=page_id,
                times_seen=1,
                status=DiscoveryStatus.pending,
            )
            .on_conflict_do_update(
                index_elements=["domain"],
                set_={"times_seen": DiscoveredDomain.times_seen + 1},
            )
            .returning(DiscoveredDomain.id)
        )
        await session.execute(stmt)
        added += 1
    await session.commit()
    return added
