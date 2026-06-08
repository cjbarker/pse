"""Persist crawled pages and maintain the Postgres full-text `search_vector`.

The inverted index is Postgres' own GIN index over `search_vector`; there is no
separate search service. Title text is weighted 'A' (highest) and body 'B'.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.parser import ParsedPage
from app.urls import host_of

# Upsert a page by URL, (re)computing the weighted tsvector in one statement.
_UPSERT_PAGE = text(
    """
    INSERT INTO pages
        (url, domain, title, content_text, content_hash, http_status, lang,
         depth, seed_id, pagerank, fetched_at, search_vector)
    VALUES
        (:url, :domain, :title, :content_text, :content_hash, :http_status, :lang,
         :depth, :seed_id, 0.0, now(),
         setweight(to_tsvector('english', coalesce(:title, '')), 'A') ||
         setweight(to_tsvector('english', coalesce(:content_text, '')), 'B'))
    ON CONFLICT (url) DO UPDATE SET
        domain = EXCLUDED.domain,
        title = EXCLUDED.title,
        content_text = EXCLUDED.content_text,
        content_hash = EXCLUDED.content_hash,
        http_status = EXCLUDED.http_status,
        lang = EXCLUDED.lang,
        depth = LEAST(pages.depth, EXCLUDED.depth),
        seed_id = COALESCE(pages.seed_id, EXCLUDED.seed_id),
        fetched_at = EXCLUDED.fetched_at,
        search_vector = EXCLUDED.search_vector
    RETURNING id
    """
)


async def upsert_page(
    session: AsyncSession,
    *,
    url: str,
    parsed: ParsedPage,
    http_status: int,
    depth: int,
    seed_id: int | None,
) -> int:
    """Insert or update a page and return its id."""
    result = await session.execute(
        _UPSERT_PAGE,
        {
            "url": url,
            "domain": host_of(url),
            "title": parsed.title,
            "content_text": parsed.text or None,
            "content_hash": parsed.content_hash,
            "http_status": http_status,
            "lang": parsed.lang,
            "depth": depth,
            "seed_id": seed_id,
        },
    )
    return int(result.scalar_one())


async def replace_links(session: AsyncSession, src_page_id: int, dst_urls: list[str]) -> None:
    """Replace a page's outbound edges with the freshly parsed set."""
    await session.execute(text("DELETE FROM links WHERE src_page_id = :src"), {"src": src_page_id})
    if not dst_urls:
        return
    await session.execute(
        text(
            """
            INSERT INTO links (src_page_id, dst_url)
            VALUES (:src, :dst)
            ON CONFLICT (src_page_id, dst_url) DO NOTHING
            """
        ),
        [{"src": src_page_id, "dst": dst} for dst in dst_urls],
    )
