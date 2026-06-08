"""URL normalization and domain helpers shared across crawling, scope, and seeding."""

from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import tldextract

# Use the bundled snapshot only (no network calls at runtime).
_extract = tldextract.TLDExtract(suffix_list_urls=())

# File extensions we never want to crawl/index.
_SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tiff",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".webm",
    ".mkv",
    ".flac",
    ".wav",
    ".ogg",
    ".zip",
    ".gz",
    ".tar",
    ".rar",
    ".7z",
    ".bz2",
    ".xz",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".rss",
    ".atom",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".exe",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".apk",
}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Resolve, defragment, and canonicalize a URL.

    Returns ``None`` for unsupported schemes, mailto/javascript links, or
    URLs that clearly point at non-HTML assets.
    """
    if not url:
        return None
    url = url.strip()
    if base:
        url = urljoin(base, url)
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.netloc:
        return None
    path = parts.path or "/"
    lower_path = path.lower()
    for ext in _SKIP_EXTENSIONS:
        if lower_path.endswith(ext):
            return None
    # Lowercase the host, strip default ports, drop a trailing slash on the bare root.
    netloc = parts.netloc.lower()
    if netloc.endswith(":80") and parts.scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and parts.scheme == "https":
        netloc = netloc[:-4]
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


def host_of(url: str) -> str:
    """Return the lowercased hostname (without port) of a URL."""
    return urlsplit(url).hostname or ""


def registrable_domain(value: str) -> str:
    """Return the registrable domain (eTLD+1) of a URL or bare host.

    ``https://blog.example.co.uk/x`` -> ``example.co.uk``.
    Falls back to the raw host if no public suffix is recognized.
    """
    host = value if "://" not in value else host_of(value)
    host = host.strip().lower().rstrip(".")
    if not host:
        return ""
    ext = _extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return host
