"""Full-text search over crawled pages, ranked by text relevance blended with PageRank."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


@dataclass
class SearchHit:
    page_id: int
    url: str
    title: str | None
    snippet: str
    domain: str
    text_rank: float
    pagerank: float
    score: float
    source: str = "local"  # "local" or a peer name


@dataclass
class SearchResults:
    query: str
    total: int
    hits: list[SearchHit]
    page: int
    page_size: int


# websearch_to_tsquery understands quoted phrases, OR, and - operators like a search box.
_SEARCH_SQL = text(
    """
    WITH q AS (SELECT websearch_to_tsquery('english', :query) AS tsq)
    SELECT
        p.id,
        p.url,
        p.title,
        p.domain,
        p.pagerank,
        ts_rank_cd(p.search_vector, q.tsq) AS text_rank,
        ts_headline(
            'english', coalesce(p.content_text, ''), q.tsq,
            'StartSel=<mark>, StopSel=</mark>, MaxFragments=2, MaxWords=30, MinWords=8'
        ) AS snippet,
        (:w_text * ts_rank_cd(p.search_vector, q.tsq) + :w_rank * p.pagerank) AS score,
        count(*) OVER () AS total
    FROM pages p, q
    WHERE p.search_vector @@ q.tsq
    ORDER BY score DESC
    LIMIT :limit OFFSET :offset
    """
)


async def search(
    session: AsyncSession, query: str, *, page: int = 1, page_size: int | None = None
) -> SearchResults:
    query = (query or "").strip()
    page = max(page, 1)
    page_size = page_size or settings.search_page_size
    if not query:
        return SearchResults(query=query, total=0, hits=[], page=page, page_size=page_size)

    offset = (page - 1) * page_size
    rows = (
        (
            await session.execute(
                _SEARCH_SQL,
                {
                    "query": query,
                    "w_text": settings.rank_weight_text,
                    "w_rank": settings.rank_weight_pagerank,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        )
        .mappings()
        .all()
    )

    total = int(rows[0]["total"]) if rows else 0
    hits = [
        SearchHit(
            page_id=r["id"],
            url=r["url"],
            title=r["title"],
            snippet=r["snippet"],
            domain=r["domain"],
            text_rank=float(r["text_rank"]),
            pagerank=float(r["pagerank"]),
            score=float(r["score"]),
        )
        for r in rows
    ]
    return SearchResults(query=query, total=total, hits=hits, page=page, page_size=page_size)
