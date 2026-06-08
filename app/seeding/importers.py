"""Bulk seed import from a pasted list, an OPML/RSS file, or a bookmarks export.

All importers reduce their input to a set of registrable domains and create
`domain`-scoped seeds (skipping any that already exist).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import feedparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ScopeMode, Seed
from app.urls import registrable_domain


def domains_from_text(blob: str) -> list[str]:
    """One domain/URL per line or whitespace-separated; '#' starts a comment."""
    out: list[str] = []
    for raw in blob.replace(",", "\n").splitlines():
        token = raw.split("#", 1)[0].strip()
        if not token:
            continue
        dom = registrable_domain(token)
        if dom:
            out.append(dom)
    return _dedupe(out)


def domains_from_opml(content: str) -> list[str]:
    """Extract site domains from an OPML subscription list or an RSS/Atom feed.

    OPML stores subscriptions as ``<outline xmlUrl=... htmlUrl=...>`` elements, which
    feedparser does not surface; those are read directly via ElementTree. RSS/Atom
    documents are handled by feedparser (feed link + entry links).
    """
    out: list[str] = []

    # OPML: pull every outline's xmlUrl/htmlUrl attribute (case-insensitive).
    try:
        root = ET.fromstring(content.strip())
    except ET.ParseError:
        root = None
    if root is not None:
        for el in root.iter():
            attrs = {k.lower(): v for k, v in el.attrib.items()}
            for key in ("htmlurl", "xmlurl", "url"):
                if attrs.get(key):
                    dom = registrable_domain(attrs[key])
                    if dom:
                        out.append(dom)

    # RSS/Atom: feed link plus per-entry links.
    parsed = feedparser.parse(content)
    feed_link = parsed.get("feed", {}).get("link")
    if feed_link:
        dom = registrable_domain(feed_link)
        if dom:
            out.append(dom)
    for entry in parsed.get("entries", []):
        for key in ("link", "id"):
            val = entry.get(key)
            if val and "://" in str(val):
                dom = registrable_domain(str(val))
                if dom:
                    out.append(dom)

    return _dedupe(out)


class _BookmarkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.hrefs.append(value)


def domains_from_bookmarks(content: str) -> list[str]:
    """Extract domains from a Netscape-format bookmarks HTML export."""
    parser = _BookmarkParser()
    parser.feed(content)
    out = [registrable_domain(h) for h in parser.hrefs]
    return _dedupe([d for d in out if d])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


async def add_domains_as_seeds(session: AsyncSession, domains: list[str]) -> int:
    """Create domain-scoped seeds for any domains not already present."""
    if not domains:
        return 0
    existing = set(
        (await session.execute(select(Seed.value).where(Seed.value.in_(domains)))).scalars().all()
    )
    added = 0
    for dom in domains:
        if dom in existing:
            continue
        session.add(
            Seed(
                value=dom,
                scope_mode=ScopeMode.domain,
                max_depth=settings.crawl_default_max_depth,
                enabled=True,
            )
        )
        existing.add(dom)
        added += 1
    await session.commit()
    return added


# Convenience: pick the right parser from an uploaded file's name/content.
def parse_upload(filename: str, content: str) -> list[str]:
    name = (filename or "").lower()
    if name.endswith((".opml", ".xml", ".rss", ".atom")):
        return domains_from_opml(content)
    if name.endswith((".html", ".htm")):
        return domains_from_bookmarks(content)
    # Fall back to sniffing the content.
    head = content.lstrip()[:200].lower()
    if head.startswith("<?xml") or "<opml" in head or "<rss" in head or "<feed" in head:
        return domains_from_opml(content)
    if "<!doctype netscape-bookmark" in head or "<a " in content.lower()[:2000]:
        return domains_from_bookmarks(content)
    return domains_from_text(content)
