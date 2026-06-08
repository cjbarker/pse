"""End-to-end integration test (requires Postgres).

Serves a tiny interlinked fixture site over HTTP, crawls it, then verifies indexing,
full-text search, PageRank ordering, self-seeding discovery, and federation merge.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import func, select

from app.index.search import search
from app.models import DiscoveredDomain, Link, Page, ScopeMode, Seed
from app.ranking.pagerank import compute_pagerank
from app.seeding.discover import record_click

# Fixture site: index + page-a both link to page-b (the hub). Index also links to an
# out-of-scope external domain (for the self-seeding test).
PAGES = {
    "/": """<html lang="en"><head><title>Home</title></head><body>
        <p>macintosh classic computing index</p>
        <a href="/page-a.html">A</a> <a href="/page-b.html">B</a>
        <a href="https://discovered-example.org/cool">external</a>
        </body></html>""",
    "/page-a.html": """<html><head><title>Page A</title></head><body>
        <p>macintosh se30 specifications page</p>
        <a href="/page-b.html">B</a></body></html>""",
    "/page-b.html": """<html><head><title>Page B</title></head><body>
        <p>apple history hub page</p>
        <a href="/">home</a></body></html>""",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fixture_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


async def test_crawl_index_search_and_pagerank(session, fixture_site):
    # Import here so the app engine is already bound to the test DB (see conftest).
    from app.crawler.worker import run_crawl

    session.add(Seed(value=fixture_site, scope_mode=ScopeMode.domain, max_depth=2, enabled=True))
    await session.commit()

    summary = await run_crawl()
    assert summary["pages_crawled"] >= 3

    page_count = (await session.execute(select(func.count()).select_from(Page))).scalar_one()
    assert page_count == 3
    link_count = (await session.execute(select(func.count()).select_from(Link))).scalar_one()
    assert link_count >= 3

    # Full-text search finds the pages mentioning "macintosh" (index + page-a), not page-b.
    results = await search(session, "macintosh")
    urls = {hit.url for hit in results.hits}
    assert any(u.endswith("/") for u in urls)  # the index page
    assert any(u.endswith("/page-a.html") for u in urls)
    assert all(not u.endswith("/page-b.html") for u in urls)

    # PageRank: page-b is linked to by both other pages, so it must rank highest.
    await compute_pagerank(session)
    ranks = {url: pr for url, pr in (await session.execute(select(Page.url, Page.pagerank))).all()}
    hub = next(u for u in ranks if u.endswith("/page-b.html"))
    assert ranks[hub] == max(ranks.values())
    assert ranks[hub] > 0


async def test_self_seeding_discovers_external_domain(session, fixture_site):
    from app.crawler.worker import run_crawl

    session.add(Seed(value=fixture_site, scope_mode=ScopeMode.domain, max_depth=2, enabled=True))
    await session.commit()
    await run_crawl()

    index_page = (
        (await session.execute(select(Page).where(Page.url.like("%127.0.0.1%")).order_by(Page.id)))
        .scalars()
        .first()
    )
    await record_click(session, index_page.id)

    discovered = (await session.execute(select(DiscoveredDomain.domain))).scalars().all()
    assert "discovered-example.org" in discovered


async def test_federation_merges_peer_results(session):
    """A stubbed peer's results are appended after local hits, tagged with its name."""
    from app.federation.client import federated_hits
    from app.models import Peer

    peer_payload = {
        "results": [
            {
                "url": "https://peer.example/doc",
                "title": "Peer Doc",
                "snippet": "from a friend",
                "domain": "peer.example",
                "text_rank": 0.5,
                "pagerank": 0.1,
                "score": 0.7,
            }
        ]
    }

    class _PeerHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = json.dumps(peer_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _PeerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        session.add(Peer(name="friend", base_url=f"http://{host}:{port}", enabled=True))
        await session.commit()

        hits = await federated_hits(session, "anything", limit=10)
        assert len(hits) == 1
        assert hits[0].url == "https://peer.example/doc"
        assert hits[0].source == "friend"
    finally:
        server.shutdown()
