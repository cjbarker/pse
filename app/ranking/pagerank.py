"""Compute PageRank over the crawled link subgraph and persist per-page scores.

Run as a post-crawl job (CLI: ``python -m app.ranking.pagerank``; or the admin
"Recompute PageRank" button). Steps:
  1. Resolve outbound edges (`links.dst_url`) to in-corpus `pages.id` targets.
  2. Build a directed graph of resolved edges and run networkx PageRank.
  3. Write the normalized score back onto each page.
"""

from __future__ import annotations

import asyncio
import logging

import networkx as nx
from sqlalchemy import bindparam, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Page

log = logging.getLogger("pse.pagerank")


async def resolve_link_targets(session: AsyncSession) -> None:
    """Set `links.dst_page_id` for every edge whose target URL is a crawled page."""
    await session.execute(
        text(
            """
            UPDATE links AS l
            SET dst_page_id = p.id
            FROM pages AS p
            WHERE p.url = l.dst_url
              AND (l.dst_page_id IS DISTINCT FROM p.id)
            """
        )
    )


async def compute_pagerank(session: AsyncSession) -> dict:
    """Compute and persist PageRank for all pages. Returns a summary dict."""
    await resolve_link_targets(session)

    page_ids = [pid for (pid,) in (await session.execute(select(Page.id))).all()]
    if not page_ids:
        return {"pages": 0, "edges": 0}

    edges = (
        await session.execute(
            text("SELECT src_page_id, dst_page_id FROM links WHERE dst_page_id IS NOT NULL")
        )
    ).all()

    graph = nx.DiGraph()
    graph.add_nodes_from(page_ids)  # include sink/orphan pages so every page is ranked
    for src, dst in edges:
        if src != dst:  # ignore self-loops
            graph.add_edge(src, dst)

    scores = nx.pagerank(graph, alpha=settings.pagerank_damping)

    # Persist with one parameterized UPDATE per page (executemany). Target the Core
    # table (not the ORM entity) so this stays a plain UPDATE rather than an ORM
    # bulk-by-primary-key operation.
    payload = [{"pid": pid, "score": float(scores.get(pid, 0.0))} for pid in page_ids]
    table = Page.__table__
    stmt = update(table).where(table.c.id == bindparam("pid")).values(pagerank=bindparam("score"))
    await session.execute(stmt, payload)
    await session.commit()

    top = max(scores.values()) if scores else 0.0
    return {"pages": len(page_ids), "edges": graph.number_of_edges(), "max_score": top}


async def run() -> dict:
    async with SessionLocal() as session:
        return await compute_pagerank(session)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = asyncio.run(run())
    log.info("pagerank complete: %s", summary)


if __name__ == "__main__":
    main()
