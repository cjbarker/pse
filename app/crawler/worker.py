"""The crawl frontier loop.

Workers claim pending rows from `crawl_queue` using ``FOR UPDATE SKIP LOCKED`` so
several async tasks (and the dedicated `worker` container) can run concurrently
without fetching the same URL twice. Each fetched page is parsed, indexed, and its
in-scope outbound links are enqueued (subject to per-seed depth limits).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawler.fetcher import Fetcher
from app.crawler.parser import parse_html
from app.crawler.robots import RobotsCache
from app.crawler.scope import ScopeRule, match_scope, rule_from_seed
from app.db import SessionLocal
from app.index.indexer import replace_links, upsert_page
from app.models import CrawlJob, CrawlQueue, CrawlStatus, JobStatus, Seed
from app.urls import host_of, normalize_url

log = logging.getLogger("pse.crawler")


async def load_scope_rules(session: AsyncSession) -> list[ScopeRule]:
    seeds = (await session.execute(select(Seed).where(Seed.enabled.is_(True)))).scalars().all()
    return [rule_from_seed(s) for s in seeds]


async def enqueue_seeds(session: AsyncSession) -> int:
    """Seed the frontier with the canonical URL of every enabled seed."""
    seeds = (await session.execute(select(Seed).where(Seed.enabled.is_(True)))).scalars().all()
    added = 0
    for seed in seeds:
        value = seed.value.strip()
        url = normalize_url(value if "://" in value else f"https://{value}")
        if url is None:
            continue
        added += await enqueue_url(session, url, seed_id=seed.id, depth=0)
    await session.commit()
    return added


async def enqueue_url(session: AsyncSession, url: str, *, seed_id: int | None, depth: int) -> int:
    """Add a URL to the frontier if not already present. Returns 1 if inserted."""
    stmt = (
        pg_insert(CrawlQueue)
        .values(url=url, seed_id=seed_id, depth=depth, status=CrawlStatus.pending)
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(CrawlQueue.id)
    )
    result = await session.execute(stmt)
    return 1 if result.first() else 0


async def _claim_one(session: AsyncSession) -> CrawlQueue | None:
    """Atomically claim the next pending frontier row (lowest depth first)."""
    row = (
        await session.execute(
            select(CrawlQueue)
            .where(CrawlQueue.status == CrawlStatus.pending)
            .order_by(CrawlQueue.priority.desc(), CrawlQueue.depth.asc(), CrawlQueue.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = CrawlStatus.in_progress
    await session.flush()
    return row


async def _process_row(
    session: AsyncSession,
    row: CrawlQueue,
    fetcher: Fetcher,
    robots: RobotsCache,
    rules: list[ScopeRule],
) -> bool:
    """Fetch, index, and expand one frontier URL. Returns True on a successful crawl."""
    host = host_of(row.url)
    if not await robots.allowed(row.url):
        row.status = CrawlStatus.done
        row.error = "blocked by robots.txt"
        return False

    result = await fetcher.fetch(row.url, host)
    if result.html is None:
        row.status = CrawlStatus.error
        row.error = result.error or f"status {result.status}"
        return False

    parsed = parse_html(result.html, row.url)
    page_id = await upsert_page(
        session,
        url=row.url,
        parsed=parsed,
        http_status=result.status,
        depth=row.depth,
        seed_id=row.seed_id,
    )
    await replace_links(session, page_id, parsed.links)

    # Expand: enqueue in-scope links one level deeper, honoring the seed's max depth.
    for link in parsed.links:
        matched = match_scope(link, rules)
        if matched is None:
            continue
        if row.depth + 1 > matched.max_depth:
            continue
        await enqueue_url(session, link, seed_id=matched.seed_id, depth=row.depth + 1)

    row.status = CrawlStatus.done
    row.error = None
    return True


async def run_crawl(max_pages: int | None = None) -> dict:
    """Drain the frontier (up to ``max_pages``) and record a CrawlJob.

    Returns a summary dict suitable for the admin dashboard / CLI output.
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.pse_user_agent}, follow_redirects=True
    ) as client:
        fetcher = Fetcher(client)
        robots = RobotsCache(client)

        async with SessionLocal() as session:
            await enqueue_seeds(session)
            rules = await load_scope_rules(session)
            job = CrawlJob(status=JobStatus.running)
            session.add(job)
            await session.commit()
            job_id = job.id

        crawled = 0
        errors = 0
        sem = asyncio.Semaphore(settings.crawl_concurrency)

        async def worker_task() -> None:
            nonlocal crawled, errors
            while True:
                if max_pages is not None and crawled >= max_pages:
                    return
                async with sem, SessionLocal() as session:
                    row = await _claim_one(session)
                    if row is None:
                        await session.commit()
                        return
                    try:
                        ok = await _process_row(session, row, fetcher, robots, rules)
                        await session.commit()
                    except Exception as exc:  # noqa: BLE001 - record and keep going
                        await session.rollback()
                        log.exception("crawl error for %s", row.url)
                        await session.execute(
                            update(CrawlQueue)
                            .where(CrawlQueue.id == row.id)
                            .values(status=CrawlStatus.error, error=str(exc)[:500])
                        )
                        await session.commit()
                        ok = False
                if ok:
                    crawled += 1
                else:
                    errors += 1

        await asyncio.gather(*(worker_task() for _ in range(settings.crawl_concurrency)))

        async with SessionLocal() as session:
            await session.execute(
                update(CrawlJob)
                .where(CrawlJob.id == job_id)
                .values(
                    status=JobStatus.finished,
                    finished_at=func.now(),
                    pages_crawled=crawled,
                    errors=errors,
                )
            )
            await session.commit()
            pending = (
                await session.execute(
                    select(func.count())
                    .select_from(CrawlQueue)
                    .where(CrawlQueue.status == CrawlStatus.pending)
                )
            ).scalar_one()

    return {"job_id": job_id, "pages_crawled": crawled, "errors": errors, "pending": pending}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = asyncio.run(run_crawl())
    log.info("crawl complete: %s", summary)


if __name__ == "__main__":
    main()
