"""HTML parsing: extract title, visible text, and normalized outbound links."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from app.urls import normalize_url

# Elements whose text should never be treated as page content.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "svg", "head")


@dataclass
class ParsedPage:
    title: str | None
    text: str
    links: list[str] = field(default_factory=list)
    lang: str | None = None
    content_hash: str = ""


def parse_html(html: str, base_url: str) -> ParsedPage:
    """Parse a page into title, visible text, outbound links, and a content hash."""
    tree = HTMLParser(html)

    title = None
    if tree.css_first("title"):
        title = (tree.css_first("title").text() or "").strip() or None

    lang = None
    root = tree.css_first("html")
    if root is not None:
        lang = (root.attributes.get("lang") or "").strip() or None

    for node in tree.css(",".join(_NON_CONTENT_TAGS)):
        node.decompose()

    body = tree.body or tree.root
    text = ""
    if body is not None:
        text = body.text(separator=" ", strip=True)
    text = " ".join(text.split())

    links: list[str] = []
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href:
            continue
        normalized = normalize_url(href, base=base_url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            links.append(normalized)

    content_hash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    return ParsedPage(title=title, text=text, links=links, lang=lang, content_hash=content_hash)
