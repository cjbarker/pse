"""robots.txt fetching, caching, and allow-checks (per host)."""

from __future__ import annotations

import urllib.robotparser
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import settings


class RobotsCache:
    """Caches one parsed robots.txt per (scheme, host).

    A missing or unreachable robots.txt is treated as "allow all", matching
    common crawler convention.
    """

    def __init__(self, client: httpx.AsyncClient, obey: bool | None = None):
        self._client = client
        self._obey = settings.crawl_obey_robots if obey is None else obey
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    async def allowed(self, url: str) -> bool:
        if not self._obey:
            return True
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        parser = self._parsers.get(key)
        if parser is None:
            parser = await self._load(parts.scheme, parts.netloc)
            self._parsers[key] = parser
        return parser.can_fetch(settings.pse_user_agent, url)

    async def _load(self, scheme: str, netloc: str) -> urllib.robotparser.RobotFileParser:
        parser = urllib.robotparser.RobotFileParser()
        robots_url = urlunsplit((scheme, netloc, "/robots.txt", "", ""))
        try:
            resp = await self._client.get(robots_url, timeout=settings.crawl_timeout)
            if resp.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(resp.text.splitlines())
        except (httpx.HTTPError, UnicodeError):
            parser.allow_all = True
        return parser
