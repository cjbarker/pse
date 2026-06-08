.PHONY: help up down logs build migrate crawl rank reindex-help seed test lint fmt install

help:
	@echo "PSE — make targets:"
	@echo "  make up        Start db + web + worker (Docker Compose), runs migrations"
	@echo "  make down      Stop and remove containers"
	@echo "  make logs      Tail web + worker logs"
	@echo "  make migrate   Apply database migrations (in the web image)"
	@echo "  make crawl     Run one crawl pass against the running db"
	@echo "  make rank      Recompute PageRank against the running db"
	@echo "  make seed      Load a few example seeds"
	@echo "  make install   Install Python deps locally (editable, with dev extras)"
	@echo "  make test      Run the test suite (needs a Postgres at TEST_DATABASE_URL)"
	@echo "  make lint      Run ruff"
	@echo "  make fmt       Auto-fix lint + format with ruff"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f web worker

build:
	docker compose build

migrate:
	docker compose run --rm migrate

crawl:
	docker compose run --rm worker python -m app.crawler.worker

rank:
	docker compose run --rm worker python -m app.ranking.pagerank

seed:
	docker compose run --rm web python -m scripts.seed_examples

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .

fmt:
	ruff check --fix .
	ruff format .
