"""Aggregate crawl/index statistics for the admin dashboard and the JSON API."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CrawlJob,
    CrawlQueue,
    CrawlStatus,
    DiscoveredDomain,
    DiscoveryStatus,
    Link,
    Page,
    Peer,
    Seed,
)


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar_one())


async def gather_stats(session: AsyncSession) -> dict:
    pages = await _count(session, select(func.count()).select_from(Page))
    links = await _count(session, select(func.count()).select_from(Link))
    seeds_total = await _count(session, select(func.count()).select_from(Seed))
    seeds_enabled = await _count(
        session, select(func.count()).select_from(Seed).where(Seed.enabled.is_(True))
    )

    queue_counts = {
        status: await _count(
            session,
            select(func.count()).select_from(CrawlQueue).where(CrawlQueue.status == status),
        )
        for status in (CrawlStatus.pending, CrawlStatus.in_progress, CrawlStatus.error)
    }

    discovered_pending = await _count(
        session,
        select(func.count())
        .select_from(DiscoveredDomain)
        .where(DiscoveredDomain.status == DiscoveryStatus.pending),
    )
    peers_enabled = await _count(
        session, select(func.count()).select_from(Peer).where(Peer.enabled.is_(True))
    )

    last_job_row = (
        await session.execute(select(CrawlJob).order_by(CrawlJob.id.desc()).limit(1))
    ).scalar_one_or_none()
    last_job = None
    if last_job_row is not None:
        last_job = {
            "id": last_job_row.id,
            "status": last_job_row.status.value,
            "pages_crawled": last_job_row.pages_crawled,
            "errors": last_job_row.errors,
            "started_at": last_job_row.started_at.isoformat() if last_job_row.started_at else None,
            "finished_at": last_job_row.finished_at.isoformat()
            if last_job_row.finished_at
            else None,
        }

    top_rows = (
        await session.execute(
            select(Page.domain, func.count().label("n"))
            .group_by(Page.domain)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    top_domains = [{"domain": d, "pages": int(n)} for d, n in top_rows]

    return {
        "pages": pages,
        "links": links,
        "seeds_enabled": seeds_enabled,
        "seeds_total": seeds_total,
        "queue_pending": queue_counts[CrawlStatus.pending],
        "queue_in_progress": queue_counts[CrawlStatus.in_progress],
        "queue_error": queue_counts[CrawlStatus.error],
        "discovered_pending": discovered_pending,
        "peers_enabled": peers_enabled,
        "last_job": last_job,
        "top_domains": top_domains,
    }
