"""MCP (Model Context Protocol) server for PSE.

Exposes the personal search engine to MCP clients (e.g. Claude Desktop/Code) over
**stdio**. Every tool is a thin wrapper over functions that already exist and are
tested; the server adds no new crawl/index logic and talks to the same PostgreSQL
database as the web app and worker.

Two tool groups:
  * Retrieval (always registered): ``search``, ``get_page``, ``stats``.
  * Administration (registered only when ``PSE_MCP_ADMIN`` is true): seed management
    and import, crawl control + status, PageRank/reindex, and the self-seeding
    discovered-domains queue + federation peers.

Run with the ``pse-mcp`` console script (``uv run pse-mcp``). It reads the same
environment as the rest of PSE (notably ``DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from sqlalchemy import delete, select, update

from app.config import settings
from app.crawler.worker import run_crawl
from app.db import SessionLocal
from app.federation.client import federated_hits
from app.index.indexer import reindex_pages
from app.index.search import SearchHit
from app.index.search import search as _search
from app.models import (
    CrawlJob,
    DiscoveredDomain,
    DiscoveryStatus,
    Page,
    Peer,
    ScopeMode,
    Seed,
)
from app.ranking.pagerank import run as run_pagerank
from app.seeding.importers import add_domains_as_seeds, domains_from_text, parse_upload
from app.stats import gather_stats

log = logging.getLogger("pse.mcp")

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #
def _hit_dict(h: SearchHit) -> dict:
    return {
        "url": h.url,
        "title": h.title,
        "snippet": h.snippet,
        "domain": h.domain,
        "score": round(h.score, 6),
        "pagerank": round(h.pagerank, 6),
        "text_rank": round(h.text_rank, 6),
        "source": h.source,
    }


def _seed_dict(s: Seed) -> dict:
    return {
        "id": s.id,
        "value": s.value,
        "scope_mode": s.scope_mode.value,
        "max_depth": s.max_depth,
        "enabled": s.enabled,
    }


def _job_dict(j: CrawlJob) -> dict:
    return {
        "id": j.id,
        "status": j.status.value,
        "pages_crawled": j.pages_crawled,
        "errors": j.errors,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


def _discovered_dict(d: DiscoveredDomain) -> dict:
    return {
        "id": d.id,
        "domain": d.domain,
        "times_seen": d.times_seen,
        "status": d.status.value,
        "discovered_at": d.discovered_at.isoformat() if d.discovered_at else None,
    }


def _peer_dict(p: Peer) -> dict:
    return {"id": p.id, "name": p.name, "base_url": p.base_url, "enabled": p.enabled}


# --------------------------------------------------------------------------- #
# Retrieval tools (always registered)
# --------------------------------------------------------------------------- #
async def search(query: str, page: int = 1, page_size: int = 10, federated: bool = False) -> dict:
    """Search the personal index, ranked by full-text relevance blended with PageRank.

    Returns ranked hits with highlighted snippets. Set ``federated`` to also include
    results from trusted peer PSE nodes (appended after local hits).
    """
    async with SessionLocal() as session:
        results = await _search(session, query, page=page, page_size=page_size)
        hits = list(results.hits)
        if federated and query.strip() and page == 1:
            seen = {h.url for h in hits}
            remote = await federated_hits(session, query, limit=page_size)
            hits.extend(h for h in remote if h.url not in seen)
    return {
        "query": results.query,
        "total": results.total,
        "page": results.page,
        "page_size": results.page_size,
        "results": [_hit_dict(h) for h in hits],
    }


async def get_page(url: str) -> dict:
    """Retrieve a single crawled page's full stored text and metadata by URL.

    Use this to fetch the complete document behind a search hit (the search snippet
    is only a short excerpt).
    """
    async with SessionLocal() as session:
        page = (await session.execute(select(Page).where(Page.url == url))).scalar_one_or_none()
    if page is None:
        return {"found": False, "url": url}
    return {
        "found": True,
        "url": page.url,
        "title": page.title,
        "domain": page.domain,
        "lang": page.lang,
        "http_status": page.http_status,
        "depth": page.depth,
        "pagerank": round(page.pagerank, 6),
        "fetched_at": page.fetched_at.isoformat() if page.fetched_at else None,
        "content_text": page.content_text or "",
    }


async def stats() -> dict:
    """Return index and crawl statistics (page/link counts, queue, last job, top domains)."""
    async with SessionLocal() as session:
        return await gather_stats(session)


# --------------------------------------------------------------------------- #
# Admin: seeds & import
# --------------------------------------------------------------------------- #
async def list_seeds(enabled_only: bool = False) -> dict:
    """List the configured crawl seeds."""
    async with SessionLocal() as session:
        stmt = select(Seed).order_by(Seed.created_at.desc())
        if enabled_only:
            stmt = stmt.where(Seed.enabled.is_(True))
        seeds = (await session.execute(stmt)).scalars().all()
    return {"count": len(seeds), "seeds": [_seed_dict(s) for s in seeds]}


async def add_seed(value: str, scope_mode: str = "domain", max_depth: int | None = None) -> dict:
    """Add a crawl seed (a bare domain or a fully-qualified URL).

    ``scope_mode`` is one of "domain", "prefix", or "exact".
    """
    value = value.strip()
    try:
        mode = ScopeMode(scope_mode)
    except ValueError as exc:
        raise ValueError(
            f"invalid scope_mode {scope_mode!r}; expected one of {[m.value for m in ScopeMode]}"
        ) from exc
    depth = settings.crawl_default_max_depth if max_depth is None else max_depth
    async with SessionLocal() as session:
        exists = (
            await session.execute(select(Seed).where(Seed.value == value))
        ).scalar_one_or_none()
        if exists is not None:
            return {"created": False, "reason": "already exists", "seed": _seed_dict(exists)}
        seed = Seed(value=value, scope_mode=mode, max_depth=depth, enabled=True)
        session.add(seed)
        await session.commit()
        await session.refresh(seed)
        return {"created": True, "seed": _seed_dict(seed)}


async def set_seed_enabled(seed_id: int, enabled: bool) -> dict:
    """Enable or disable a seed by id."""
    async with SessionLocal() as session:
        seed = (await session.execute(select(Seed).where(Seed.id == seed_id))).scalar_one_or_none()
        if seed is None:
            return {"found": False, "seed_id": seed_id}
        seed.enabled = enabled
        await session.commit()
        return {"found": True, "seed": _seed_dict(seed)}


async def remove_seed(seed_id: int) -> dict:
    """Delete a seed by id."""
    async with SessionLocal() as session:
        result = await session.execute(delete(Seed).where(Seed.id == seed_id))
        await session.commit()
    return {"removed": bool(result.rowcount), "seed_id": seed_id}


async def import_seeds(content: str, filename: str | None = None) -> dict:
    """Bulk-add seeds from text.

    With no ``filename``, ``content`` is treated as a newline/comma-separated list of
    domains or URLs. Pass a ``filename`` ending in .opml/.xml/.rss/.atom or .html/.htm
    to import an OPML/RSS subscription list or a browser bookmarks export.
    """
    domains = parse_upload(filename, content) if filename else domains_from_text(content)
    async with SessionLocal() as session:
        added = await add_domains_as_seeds(session, domains)
    return {"added": added, "candidates": len(domains), "domains": domains}


# --------------------------------------------------------------------------- #
# Admin: crawl control + status
# --------------------------------------------------------------------------- #
_bg_tasks: set[asyncio.Task] = set()


async def _crawl_task(max_pages: int | None) -> None:
    try:
        summary = await run_crawl(max_pages)
        log.info("MCP background crawl finished: %s", summary)
    except Exception:  # noqa: BLE001 - log and swallow in the background
        log.exception("MCP background crawl failed")


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def start_crawl(max_pages: int | None = None) -> dict:
    """Start a crawl of all enabled seeds in the background, returning immediately.

    Optionally cap the number of pages with ``max_pages``. Poll ``crawl_status`` or
    ``list_crawl_jobs`` for progress.
    """
    _spawn(_crawl_task(max_pages))
    return {
        "started": True,
        "max_pages": max_pages,
        "note": "Crawl running in the background; poll crawl_status() for progress.",
    }


async def crawl_status() -> dict:
    """Report current crawl progress: queue counts and the most recent job."""
    async with SessionLocal() as session:
        s = await gather_stats(session)
    return {
        "pages": s["pages"],
        "queue_pending": s["queue_pending"],
        "queue_in_progress": s["queue_in_progress"],
        "queue_error": s["queue_error"],
        "last_job": s["last_job"],
    }


async def list_crawl_jobs(limit: int = 10) -> dict:
    """List the most recent crawl jobs (newest first)."""
    async with SessionLocal() as session:
        jobs = (
            (await session.execute(select(CrawlJob).order_by(CrawlJob.id.desc()).limit(limit)))
            .scalars()
            .all()
        )
    return {"jobs": [_job_dict(j) for j in jobs]}


# --------------------------------------------------------------------------- #
# Admin: ranking & reindex
# --------------------------------------------------------------------------- #
async def recompute_pagerank() -> dict:
    """Recompute PageRank over the crawled link graph and persist the scores."""
    return await run_pagerank()


async def reindex() -> dict:
    """Rebuild the full-text search vectors for every page from stored title/content."""
    async with SessionLocal() as session:
        n = await reindex_pages(session)
    return {"reindexed_pages": n}


# --------------------------------------------------------------------------- #
# Admin: discovered domains (self-seeding queue) & peers (federation)
# --------------------------------------------------------------------------- #
async def list_discovered_domains(status: str = "pending", limit: int = 100) -> dict:
    """List self-seeding discovered domains, filtered by status (pending/approved/rejected)."""
    try:
        st = DiscoveryStatus(status)
    except ValueError as exc:
        raise ValueError(
            f"invalid status {status!r}; expected one of {[s.value for s in DiscoveryStatus]}"
        ) from exc
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(DiscoveredDomain)
                    .where(DiscoveredDomain.status == st)
                    .order_by(DiscoveredDomain.times_seen.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return {"count": len(rows), "discovered": [_discovered_dict(d) for d in rows]}


async def approve_discovered_domain(discovered_id: int) -> dict:
    """Approve a discovered domain: promote it to a seed and mark it approved."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(DiscoveredDomain).where(DiscoveredDomain.id == discovered_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return {"found": False, "id": discovered_id}
        added = await add_domains_as_seeds(session, [row.domain])
        row.status = DiscoveryStatus.approved
        await session.commit()
    return {"found": True, "domain": row.domain, "seeded": bool(added)}


async def reject_discovered_domain(discovered_id: int) -> dict:
    """Reject a discovered domain so it is not suggested again."""
    async with SessionLocal() as session:
        result = await session.execute(
            update(DiscoveredDomain)
            .where(DiscoveredDomain.id == discovered_id)
            .values(status=DiscoveryStatus.rejected)
        )
        await session.commit()
    return {"updated": bool(result.rowcount), "id": discovered_id}


async def list_peers() -> dict:
    """List trusted federation peer PSE nodes."""
    async with SessionLocal() as session:
        peers = (
            (await session.execute(select(Peer).order_by(Peer.created_at.desc()))).scalars().all()
        )
    return {"count": len(peers), "peers": [_peer_dict(p) for p in peers]}


async def add_peer(name: str, base_url: str) -> dict:
    """Add a trusted federation peer (its base URL, e.g. https://pse.friend.example)."""
    name, base_url = name.strip(), base_url.strip().rstrip("/")
    async with SessionLocal() as session:
        exists = (
            await session.execute(select(Peer).where(Peer.base_url == base_url))
        ).scalar_one_or_none()
        if exists is not None:
            return {"created": False, "reason": "already exists", "peer": _peer_dict(exists)}
        peer = Peer(name=name, base_url=base_url, enabled=True)
        session.add(peer)
        await session.commit()
        await session.refresh(peer)
        return {"created": True, "peer": _peer_dict(peer)}


async def set_peer_enabled(peer_id: int, enabled: bool) -> dict:
    """Enable or disable a federation peer by id."""
    async with SessionLocal() as session:
        peer = (await session.execute(select(Peer).where(Peer.id == peer_id))).scalar_one_or_none()
        if peer is None:
            return {"found": False, "peer_id": peer_id}
        peer.enabled = enabled
        await session.commit()
        return {"found": True, "peer": _peer_dict(peer)}


async def remove_peer(peer_id: int) -> dict:
    """Delete a federation peer by id."""
    async with SessionLocal() as session:
        result = await session.execute(delete(Peer).where(Peer.id == peer_id))
        await session.commit()
    return {"removed": bool(result.rowcount), "peer_id": peer_id}


# --------------------------------------------------------------------------- #
# Server assembly
# --------------------------------------------------------------------------- #
# (function, annotations) — read tools are always registered; admin tools only when
# PSE_MCP_ADMIN is true.
_READ_TOOLS = [
    (search, _READ_ONLY),
    (get_page, _READ_ONLY),
    (stats, _READ_ONLY),
]
_ADMIN_TOOLS = [
    (list_seeds, _READ_ONLY),
    (add_seed, None),
    (set_seed_enabled, None),
    (remove_seed, _DESTRUCTIVE),
    (import_seeds, None),
    (start_crawl, None),
    (crawl_status, _READ_ONLY),
    (list_crawl_jobs, _READ_ONLY),
    (recompute_pagerank, None),
    (reindex, None),
    (list_discovered_domains, _READ_ONLY),
    (approve_discovered_domain, None),
    (reject_discovered_domain, _DESTRUCTIVE),
    (list_peers, _READ_ONLY),
    (add_peer, None),
    (set_peer_enabled, None),
    (remove_peer, _DESTRUCTIVE),
]


def build_server(enable_admin: bool | None = None) -> FastMCP:
    """Build the FastMCP server, registering admin tools only when enabled."""
    if enable_admin is None:
        enable_admin = settings.mcp_enable_admin
    server = FastMCP("pse")
    tools = _READ_TOOLS + (_ADMIN_TOOLS if enable_admin else [])
    for fn, annots in tools:
        server.add_tool(fn, annotations=annots)
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_server().run()


if __name__ == "__main__":
    main()
