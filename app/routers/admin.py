"""Admin UI: manage seeds, review discovered domains, manage peers, watch crawl
stats, and trigger crawl / PageRank / reindex jobs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crawler.worker import run_crawl
from app.db import get_session
from app.models import (
    DiscoveredDomain,
    DiscoveryStatus,
    Peer,
    ScopeMode,
    Seed,
)
from app.ranking.pagerank import run as run_pagerank
from app.seeding.importers import add_domains_as_seeds, parse_upload
from app.stats import gather_stats
from app.templating import templates

log = logging.getLogger("pse.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


# ---- Dashboard -----------------------------------------------------------------


@router.get("/")
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    stats = await gather_stats(session)
    return templates.TemplateResponse(request, "admin/dashboard.html", {"stats": stats})


@router.get("/stats-partial")
async def stats_partial(request: Request, session: AsyncSession = Depends(get_session)):
    """HTMX-polled fragment with live crawl counters."""
    stats = await gather_stats(session)
    return templates.TemplateResponse(request, "admin/_stats.html", {"stats": stats})


# ---- Seeds ---------------------------------------------------------------------


@router.get("/seeds")
async def seeds_page(request: Request, session: AsyncSession = Depends(get_session)):
    seeds = (await session.execute(select(Seed).order_by(Seed.created_at.desc()))).scalars().all()
    return templates.TemplateResponse(
        request,
        "admin/seeds.html",
        {"seeds": seeds, "scope_modes": list(ScopeMode)},
    )


@router.post("/seeds")
async def create_seed(
    value: str = Form(...),
    scope_mode: ScopeMode = Form(ScopeMode.domain),
    max_depth: int = Form(settings.crawl_default_max_depth),
    session: AsyncSession = Depends(get_session),
):
    value = value.strip()
    if value:
        exists = (await session.execute(select(Seed.id).where(Seed.value == value))).first()
        if not exists:
            session.add(Seed(value=value, scope_mode=scope_mode, max_depth=max_depth, enabled=True))
            await session.commit()
    return RedirectResponse(url="/admin/seeds", status_code=303)


@router.post("/seeds/{seed_id}/toggle")
async def toggle_seed(seed_id: int, session: AsyncSession = Depends(get_session)):
    seed = (await session.execute(select(Seed).where(Seed.id == seed_id))).scalar_one_or_none()
    if seed is not None:
        seed.enabled = not seed.enabled
        await session.commit()
    return RedirectResponse(url="/admin/seeds", status_code=303)


@router.post("/seeds/{seed_id}/delete")
async def delete_seed(seed_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Seed).where(Seed.id == seed_id))
    await session.commit()
    return RedirectResponse(url="/admin/seeds", status_code=303)


@router.post("/seeds/import")
async def import_seeds(
    request: Request,
    paste: str = Form(""),
    upload: UploadFile | None = None,
    session: AsyncSession = Depends(get_session),
):
    domains: list[str] = []
    if upload is not None and upload.filename:
        content = (await upload.read()).decode("utf-8", "replace")
        domains.extend(parse_upload(upload.filename, content))
    if paste.strip():
        from app.seeding.importers import domains_from_text

        domains.extend(domains_from_text(paste))
    # De-dupe while preserving order.
    seen: set[str] = set()
    unique = [d for d in domains if not (d in seen or seen.add(d))]
    added = await add_domains_as_seeds(session, unique)
    log.info("imported %d new seeds (from %d candidates)", added, len(unique))
    return RedirectResponse(url="/admin/seeds", status_code=303)


# ---- Discovered domains (self-seeding review queue) ----------------------------


@router.get("/discovered")
async def discovered_page(request: Request, session: AsyncSession = Depends(get_session)):
    rows = (
        (
            await session.execute(
                select(DiscoveredDomain)
                .where(DiscoveredDomain.status == DiscoveryStatus.pending)
                .order_by(DiscoveredDomain.times_seen.desc(), DiscoveredDomain.discovered_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(request, "admin/discovered.html", {"discovered": rows})


@router.post("/discovered/{disc_id}/approve")
async def approve_discovered(disc_id: int, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(select(DiscoveredDomain).where(DiscoveredDomain.id == disc_id))
    ).scalar_one_or_none()
    if row is not None and row.status == DiscoveryStatus.pending:
        await add_domains_as_seeds(session, [row.domain])
        row.status = DiscoveryStatus.approved
        await session.commit()
    return RedirectResponse(url="/admin/discovered", status_code=303)


@router.post("/discovered/{disc_id}/reject")
async def reject_discovered(disc_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(
        update(DiscoveredDomain)
        .where(DiscoveredDomain.id == disc_id)
        .values(status=DiscoveryStatus.rejected)
    )
    await session.commit()
    return RedirectResponse(url="/admin/discovered", status_code=303)


# ---- Peers (federation) --------------------------------------------------------


@router.get("/peers")
async def peers_page(request: Request, session: AsyncSession = Depends(get_session)):
    peers = (await session.execute(select(Peer).order_by(Peer.created_at.desc()))).scalars().all()
    return templates.TemplateResponse(request, "admin/peers.html", {"peers": peers})


@router.post("/peers")
async def create_peer(
    name: str = Form(...),
    base_url: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    name, base_url = name.strip(), base_url.strip().rstrip("/")
    if name and base_url:
        exists = (await session.execute(select(Peer.id).where(Peer.base_url == base_url))).first()
        if not exists:
            session.add(Peer(name=name, base_url=base_url, enabled=True))
            await session.commit()
    return RedirectResponse(url="/admin/peers", status_code=303)


@router.post("/peers/{peer_id}/toggle")
async def toggle_peer(peer_id: int, session: AsyncSession = Depends(get_session)):
    peer = (await session.execute(select(Peer).where(Peer.id == peer_id))).scalar_one_or_none()
    if peer is not None:
        peer.enabled = not peer.enabled
        await session.commit()
    return RedirectResponse(url="/admin/peers", status_code=303)


@router.post("/peers/{peer_id}/delete")
async def delete_peer(peer_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Peer).where(Peer.id == peer_id))
    await session.commit()
    return RedirectResponse(url="/admin/peers", status_code=303)


# ---- Control actions -----------------------------------------------------------


@router.post("/actions/crawl")
async def trigger_crawl(background: BackgroundTasks):
    background.add_task(_run_crawl_task)
    return RedirectResponse(url="/admin/", status_code=303)


@router.post("/actions/pagerank")
async def trigger_pagerank(background: BackgroundTasks):
    background.add_task(_run_pagerank_task)
    return RedirectResponse(url="/admin/", status_code=303)


@router.post("/actions/reindex")
async def trigger_reindex(session: AsyncSession = Depends(get_session)):
    """Rebuild every page's search_vector from stored title/content."""
    await session.execute(
        text(
            """
            UPDATE pages SET search_vector =
                setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(content_text, '')), 'B')
            """
        )
    )
    await session.commit()
    return RedirectResponse(url="/admin/", status_code=303)


async def _run_crawl_task() -> None:
    try:
        summary = await run_crawl()
        log.info("background crawl finished: %s", summary)
    except Exception:  # noqa: BLE001
        log.exception("background crawl failed")


async def _run_pagerank_task() -> None:
    try:
        summary = await run_pagerank()
        log.info("background pagerank finished: %s", summary)
    except Exception:  # noqa: BLE001
        log.exception("background pagerank failed")
