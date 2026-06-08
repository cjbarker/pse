"""JSON API: search (also the federation endpoint) and stats.

The same ``/api/search`` endpoint serves the local UI, external clients, and peer
PSE nodes. Peers always pass ``local_only=true`` so federation stays single-hop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.federation.client import federated_hits
from app.index.search import SearchHit, search
from app.schemas import SearchHitOut, SearchResponse, StatsResponse
from app.stats import gather_stats

router = APIRouter(prefix="/api", tags=["api"])


def _to_out(hit: SearchHit) -> SearchHitOut:
    return SearchHitOut(
        url=hit.url,
        title=hit.title,
        snippet=hit.snippet,
        domain=hit.domain,
        text_rank=hit.text_rank,
        pagerank=hit.pagerank,
        score=hit.score,
        source=hit.source,
    )


@router.get("/search", response_model=SearchResponse)
async def api_search(
    q: str = Query("", description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    federated: bool = Query(False, description="Also query trusted peer PSEs"),
    local_only: bool = Query(False, description="Set by peers to prevent multi-hop federation"),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    results = await search(session, q, page=page, page_size=page_size)
    hits = list(results.hits)

    # Federate only on the first page, when asked, and never when a peer called us.
    do_federate = federated and not local_only and page == 1
    if do_federate:
        remote = await federated_hits(session, q, limit=page_size)
        seen = {h.url for h in hits}
        hits.extend(h for h in remote if h.url not in seen)

    return SearchResponse(
        query=results.query,
        total=results.total,
        page=results.page,
        page_size=results.page_size,
        results=[_to_out(h) for h in hits],
        federated=do_federate,
    )


@router.get("/stats", response_model=StatsResponse)
async def api_stats(session: AsyncSession = Depends(get_session)) -> StatsResponse:
    return StatsResponse(**await gather_stats(session))
