"""HTTP fetching with per-host politeness delay and HTML-only enforcement."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class FetchResult:
    url: str
    status: int
    html: str | None
    error: str | None = None


class Fetcher:
    """Async HTTP client that enforces a minimum delay between hits to a host."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._last_hit: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, host: str) -> asyncio.Lock:
        lock = self._locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[host] = lock
        return lock

    async def _respect_delay(self, host: str) -> None:
        async with self._lock_for(host):
            last = self._last_hit.get(host)
            now = time.monotonic()
            if last is not None:
                wait = settings.crawl_host_delay - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def fetch(self, url: str, host: str) -> FetchResult:
        await self._respect_delay(host)
        try:
            resp = await self._client.get(url, timeout=settings.crawl_timeout)
        except httpx.HTTPError as exc:
            return FetchResult(url=url, status=0, html=None, error=str(exc))

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return FetchResult(url=url, status=resp.status_code, html=None, error="non-html")

        # Enforce the body-size cap even when servers omit content-length.
        raw = resp.content[: settings.crawl_max_bytes]
        try:
            html = raw.decode(resp.encoding or "utf-8", "replace")
        except (LookupError, UnicodeError):
            html = raw.decode("utf-8", "replace")
        return FetchResult(url=url, status=resp.status_code, html=html)
