"""Application configuration, sourced from environment variables (.env supported)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://pse:pse@localhost:5432/pse"
    sync_database_url: str = "postgresql+psycopg://pse:pse@localhost:5432/pse"

    # Crawler
    pse_user_agent: str = "PSE/0.1 (+https://github.com/cjbarker/pse)"
    crawl_host_delay: float = 1.0
    crawl_concurrency: int = 4
    crawl_timeout: float = 15.0
    crawl_max_bytes: int = 2_000_000
    crawl_default_max_depth: int = 3
    crawl_obey_robots: bool = True

    # Ranking
    pagerank_damping: float = 0.85
    rank_weight_text: float = 1.0
    rank_weight_pagerank: float = 2.0

    # Self-seeding
    self_seed_auto_add: bool = False

    # Federation
    federation_timeout: float = 3.0

    # Search
    search_page_size: int = 10

    # MCP server: when false, only read/retrieval tools are registered (no mutating
    # or crawl-triggering tools).
    mcp_enable_admin: bool = Field(default=True, validation_alias="PSE_MCP_ADMIN")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
