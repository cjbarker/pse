"""Search UI: the Google-like search page, an HTMX results partial, and the
self-seeding ``/go`` tracked redirect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.federation.client import federated_hits
from app.index.search import search
from app.models import Page
from app.seeding.discover import record_click
from app.templating import templates

router = APIRouter(tags=["search"])


@router.get("/")
async def search_page(
    request: Request,
    q: str = Query(""),
    page: int = Query(1, ge=1),
    federated: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    results = await search(session, q, page=page, page_size=settings.search_page_size)
    hits = list(results.hits)
    if federated and q.strip() and page == 1:
        remote = await federated_hits(session, q, limit=settings.search_page_size)
        seen = {h.url for h in hits}
        hits.extend(h for h in remote if h.url not in seen)

    total_pages = (results.total + results.page_size - 1) // results.page_size
    ctx = {
        "q": q,
        "hits": hits,
        "results": results,
        "federated": federated,
        "total_pages": total_pages,
    }
    # HTMX requests swap only the results fragment.
    template = "search/_results.html" if request.headers.get("HX-Request") else "search/index.html"
    return templates.TemplateResponse(request, template, ctx)


@router.get("/go")
async def go(
    page_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Record a result click (self-seeding) and redirect to the destination page."""
    page = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one_or_none()
    if page is None:
        return RedirectResponse(url="/", status_code=303)
    try:
        await record_click(session, page_id)
    except Exception:  # noqa: BLE001 - self-seeding must never block navigation
        await session.rollback()
    return RedirectResponse(url=page.url, status_code=303)
