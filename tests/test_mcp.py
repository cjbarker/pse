"""Tests for the MCP server tools (Postgres-backed; auto-skip without a database).

Tools open their own ``SessionLocal`` against the same test database, so we set up
state via the ``session`` fixture (committed) and then call the tool callables.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app.mcp_server as mcpmod
from app.models import ScopeMode, Seed

PAGES = {
    "/": (
        "<html lang=en><head><title>Home</title></head><body>"
        "<p>macintosh classic computing</p>"
        "<a href='/a.html'>A</a> <a href='/b.html'>B</a></body></html>"
    ),
    "/a.html": (
        "<html><head><title>Specs</title></head><body>"
        "<p>macintosh se30 specifications</p><a href='/b.html'>B</a></body></html>"
    ),
    "/b.html": (
        "<html><head><title>History</title></head><body>"
        "<p>apple history hub</p><a href='/'>home</a></body></html>"
    ),
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


async def test_admin_gating_controls_registered_tools():
    """Read tools are always registered; admin tools only when enabled. No DB needed."""
    read_only = {t.name for t in await mcpmod.build_server(enable_admin=False).list_tools()}
    full = {t.name for t in await mcpmod.build_server(enable_admin=True).list_tools()}

    assert read_only == {"search", "get_page", "stats"}
    assert {"add_seed", "start_crawl", "remove_seed", "recompute_pagerank"} <= full
    # Admin tools must be hidden when admin is disabled.
    assert "add_seed" not in read_only and "start_crawl" not in read_only


async def test_retrieval_tools(session, fixture_site):
    from app.crawler.worker import run_crawl

    session.add(Seed(value=fixture_site, scope_mode=ScopeMode.domain, max_depth=2, enabled=True))
    await session.commit()
    await run_crawl()
    await mcpmod.recompute_pagerank()

    # search
    res = await mcpmod.search("macintosh")
    urls = [hit["url"] for hit in res["results"]]
    assert res["total"] >= 1
    assert any(u.endswith("/") or u.endswith("/a.html") for u in urls)
    assert all("<mark>" in hit["snippet"] for hit in res["results"])

    # get_page returns full content for a hit
    page = await mcpmod.get_page(urls[0])
    assert page["found"] is True
    assert "macintosh" in page["content_text"].lower()

    # get_page for an unknown URL
    missing = await mcpmod.get_page("https://nope.example/x")
    assert missing["found"] is False

    # stats
    st = await mcpmod.stats()
    assert st["pages"] == 3


async def test_seed_admin_tools(session):
    created = await mcpmod.add_seed("example.com", scope_mode="domain", max_depth=2)
    assert created["created"] is True
    seed_id = created["seed"]["id"]

    # duplicate is reported, not re-created
    again = await mcpmod.add_seed("example.com")
    assert again["created"] is False

    # invalid scope_mode is rejected
    with pytest.raises(ValueError):
        await mcpmod.add_seed("bad.example", scope_mode="nonsense")

    listed = await mcpmod.list_seeds()
    assert any(s["value"] == "example.com" for s in listed["seeds"])

    toggled = await mcpmod.set_seed_enabled(seed_id, False)
    assert toggled["seed"]["enabled"] is False

    imported = await mcpmod.import_seeds("alpha.dev\nbeta.org, gamma.net")
    assert imported["added"] == 3
    assert set(imported["domains"]) == {"alpha.dev", "beta.org", "gamma.net"}

    removed = await mcpmod.remove_seed(seed_id)
    assert removed["removed"] is True


async def test_crawl_control_and_jobs(session, fixture_site):
    session.add(Seed(value=fixture_site, scope_mode=ScopeMode.domain, max_depth=2, enabled=True))
    await session.commit()

    started = await mcpmod.start_crawl(max_pages=5)
    assert started["started"] is True

    # Drain the background crawl task (bounded wait).
    for _ in range(100):
        if not mcpmod._bg_tasks:
            break
        await asyncio.sleep(0.1)
    assert not mcpmod._bg_tasks

    status = await mcpmod.crawl_status()
    assert status["pages"] >= 1
    jobs = await mcpmod.list_crawl_jobs()
    assert jobs["jobs"] and jobs["jobs"][0]["status"] in {"finished", "running"}


async def test_peer_admin_tools(session):
    created = await mcpmod.add_peer("friend", "https://pse.friend.example/")
    assert created["created"] is True
    peer_id = created["peer"]["id"]
    assert created["peer"]["base_url"] == "https://pse.friend.example"  # trailing slash trimmed

    listed = await mcpmod.list_peers()
    assert any(p["id"] == peer_id for p in listed["peers"])

    disabled = await mcpmod.set_peer_enabled(peer_id, False)
    assert disabled["peer"]["enabled"] is False

    removed = await mcpmod.remove_peer(peer_id)
    assert removed["removed"] is True
