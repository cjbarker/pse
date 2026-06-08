"""Federation: query trusted peer PSE nodes and merge their hits after local ones.

When the local index runs thin, a PSE can broaden a search by asking peers. Peers
are queried concurrently with a single overall wall-clock budget
(``FEDERATION_TIMEOUT``, default 3s). Federated calls set ``local_only=true`` so a
peer does not recursively fan out (single-hop only).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.index.search import SearchHit
from app.models import Peer, utcnow

log = logging.getLogger("pse.federation")


async def federated_hits(session: AsyncSession, query: str, limit: int) -> list[SearchHit]:
    """Query all enabled peers concurrently within the timeout budget."""
    peers = (await session.execute(select(Peer).where(Peer.enabled.is_(True)))).scalars().all()
    if not peers:
        return []

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.pse_user_agent}, follow_redirects=True
    ) as client:
        tasks = [asyncio.create_task(_query_peer(client, peer, query, limit)) for peer in peers]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=settings.federation_timeout,
            )
        except TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()

    hits: list[SearchHit] = []
    ok_peer_ids: list[int] = []
    for peer, task in zip(peers, tasks, strict=True):
        if task.cancelled():
            continue
        try:
            peer_hits = task.result()
        except Exception:  # noqa: BLE001 - a flaky peer must not break the search
            continue
        if peer_hits:
            ok_peer_ids.append(peer.id)
            hits.extend(peer_hits)

    if ok_peer_ids:
        await session.execute(
            update(Peer).where(Peer.id.in_(ok_peer_ids)).values(last_ok_at=utcnow())
        )
        await session.commit()

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def _query_peer(
    client: httpx.AsyncClient, peer: Peer, query: str, limit: int
) -> list[SearchHit]:
    url = peer.base_url.rstrip("/") + "/api/search"
    resp = await client.get(
        url,
        params={"q": query, "page_size": limit, "local_only": "true"},
        timeout=settings.federation_timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    out: list[SearchHit] = []
    for r in data.get("results", []):
        out.append(
            SearchHit(
                page_id=-1,
                url=r.get("url", ""),
                title=r.get("title"),
                snippet=r.get("snippet", ""),
                domain=r.get("domain", ""),
                text_rank=float(r.get("text_rank", 0.0)),
                pagerank=float(r.get("pagerank", 0.0)),
                score=float(r.get("score", 0.0)),
                source=peer.name,
            )
        )
    return out
