# PSE — Personalized Search Engine

[![CI](https://github.com/cjbarker/pse/actions/workflows/ci.yml/badge.svg)](https://github.com/cjbarker/pse/actions/workflows/ci.yml)

A **personal, local-first search engine**. Seed it with the domains and URLs *you*
trust, and it crawls + indexes only those, then ranks results with classic
**PageRank** over the crawled link graph. It's private by construction: it indexes
only what you choose rather than spying on you.

> Inspired by the "personal search engines" concept — instead of crawling the whole
> (spam-ridden) web, you curate a small set of high-signal seeds. A few hundred
> specialized domains give fast, relevant, trustworthy results on modest hardware.

## Features

- **Curated seeds** — add bare domains or fully-qualified URLs, each with a crawl
  **scope** (`domain` / `prefix` / `exact`) and max depth. Bulk-import from a pasted
  list, an **OPML/RSS** file, or a **bookmarks** export.
- **Scoped crawler** — async, polite (per-host delay, `robots.txt`, custom UA),
  depth-limited, dedup'd. A Postgres-backed frontier lets multiple workers run safely.
- **Full-text index** — Postgres `tsvector` + GIN (title weighted above body); no
  separate search service.
- **PageRank ranking** — authority over the crawled subgraph, blended with text
  relevance: `score = w_text·ts_rank + w_rank·pagerank` (weights configurable).
- **Self-seeding** — clicking a result harvests its outbound domains into a
  **review queue** (or auto-adds them) so your index grows along your interests.
- **Federation** — query trusted **peer PSEs**; their hits are appended after your
  local results, capped at a configurable timeout (default 3s). The same endpoint
  lets your node answer peers and "graduate" to a public, contributing node.
- **Two UIs** — a Google-like **search** page and an **admin** dashboard (seeds,
  discovered-domains review, peers, live crawl stats) built with Jinja2 + HTMX.

## Architecture

```
Seeds ──▶ Crawl frontier (Postgres) ──▶ Crawler (httpx + selectolax)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                      ▼                     ▼
                  pages (+tsvector)         links (graph)      discovered_domains
                        │                      │
                        ▼                      ▼
                Full-text search  ◀──blend──  PageRank (networkx)
                        │
                        ▼
        Search UI  ──▶ /go click ──▶ self-seeding   Federation ──▶ peer PSEs
```

| Layer        | Choice                                              |
|--------------|-----------------------------------------------------|
| API + crawler| Python 3.11+ / FastAPI / asyncio                    |
| Storage+index| PostgreSQL 16 (`tsvector` full-text, GIN)           |
| Ranking      | networkx PageRank                                   |
| UI           | Server-rendered Jinja2 + HTMX                       |
| Deploy       | Docker Compose (`db`, `migrate`, `web`, `worker`)   |

Key modules live under `app/`: `crawler/` (scope, robots, fetcher, parser, worker),
`index/` (indexer, search), `ranking/pagerank.py`, `seeding/` (importers, self-seed
discovery), `federation/client.py`, and `routers/` (admin, search, api).

## Quickstart (Docker)

```bash
cp .env.example .env          # tweak as desired
make up                       # builds + starts db, runs migrations, web + worker
```

Then:

1. Open **http://localhost:8000/admin/seeds** and add a seed (e.g. a small docs site),
   or import from OPML/bookmarks.
2. On the **Dashboard**, click **Start crawl**; watch the counters climb (auto-refresh).
   The `worker` service also crawls + ranks on a loop automatically.
3. Click **Recompute PageRank**.
4. Open **http://localhost:8000/** and search. Click a result to trigger self-seeding;
   review finds under **Admin ▸ Discovered**.

Useful targets: `make crawl`, `make rank`, `make seed`, `make logs`, `make down`.

## Local development (no Docker)

This project uses [uv](https://docs.astral.sh/uv/). `uv sync` creates a `.venv`
and installs the locked dependencies (including the dev group); prefix commands
with `uv run` to use it (no manual activation needed).

```bash
uv sync                       # create .venv + install deps from uv.lock (or: make install)

# Point at a Postgres you control:
export DATABASE_URL=postgresql+asyncpg://pse:pse@localhost:5432/pse
export SYNC_DATABASE_URL=postgresql+psycopg://pse:pse@localhost:5432/pse
uv run alembic upgrade head

uv run uvicorn app.main:app --reload     # web UI + API
uv run python -m app.crawler.worker      # one crawl pass
uv run python -m app.ranking.pagerank    # recompute PageRank
```

After editing dependencies in `pyproject.toml`, run `uv lock` (or `make lock`) to
refresh `uv.lock`, and commit it.

## Configuration

All settings come from environment variables (see `.env.example`): database URLs,
crawler politeness (`CRAWL_HOST_DELAY`, `CRAWL_CONCURRENCY`, `CRAWL_OBEY_ROBOTS`,
`CRAWL_DEFAULT_MAX_DEPTH`), ranking weights (`RANK_WEIGHT_TEXT`,
`RANK_WEIGHT_PAGERANK`, `PAGERANK_DAMPING`), self-seeding (`SELF_SEED_AUTO_ADD`),
and federation (`FEDERATION_TIMEOUT`).

## API

- `GET /api/search?q=...&page=1&page_size=10&federated=false` → JSON results. This is
  also the **federation endpoint**: peers call it with `local_only=true` (single-hop).
- `GET /api/stats` → crawl/index counters.
- `GET /healthz` → liveness.

## MCP server

PSE ships an [MCP](https://modelcontextprotocol.io) server (stdio) so AI assistants can
search the personal index and drive the crawler. Run it with the `pse-mcp` console
script; it uses the same environment as the rest of PSE (notably `DATABASE_URL`).

```bash
uv run pse-mcp        # stdio MCP server
```

**Tools**

- *Retrieval (always available):* `search`, `get_page` (full stored document text),
  `stats`.
- *Administration (only when `PSE_MCP_ADMIN=true`, the default):* `list_seeds` /
  `add_seed` / `set_seed_enabled` / `remove_seed` / `import_seeds`; `start_crawl` /
  `crawl_status` / `list_crawl_jobs`; `recompute_pagerank` / `reindex`;
  `list_discovered_domains` / `approve_discovered_domain` / `reject_discovered_domain`;
  `list_peers` / `add_peer` / `set_peer_enabled` / `remove_peer`.

Set `PSE_MCP_ADMIN=false` for a read-only server (search/get_page/stats only).

**Client config** (e.g. Claude Desktop/Code) — point it at the project and run via uv:

```json
{
  "mcpServers": {
    "pse": {
      "command": "uv",
      "args": ["--directory", "/path/to/pse", "run", "pse-mcp"],
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://pse:pse@localhost:5432/pse",
        "PSE_MCP_ADMIN": "true"
      }
    }
  }
}
```

## Testing

```bash
make test            # unit tests run anywhere; integration tests need Postgres
```

Unit tests (scope matching, HTML parsing, importers, PageRank graph) need no database.
Integration tests crawl a tiny in-process fixture site and verify indexing, search,
PageRank ordering, self-seeding, and federation — they auto-skip if no Postgres is
reachable. Point them at a throwaway DB with
`TEST_DATABASE_URL=postgresql+asyncpg://pse:pse@localhost:5432/pse uv run pytest`.

### Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

- **lint-test** (Python 3.11 and 3.12) against a `postgres:16` service container —
  installs with `uv sync --frozen`, then `ruff check`, `ruff format --check`, an
  Alembic migration round-trip (`upgrade head → downgrade base → upgrade head`), and
  the full `pytest` suite (unit **and** Postgres-backed integration tests).
- **docker-build** — builds the production image (uv-based) to validate the `Dockerfile`.

## License

MIT — see [LICENSE](LICENSE).
