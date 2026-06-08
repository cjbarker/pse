"""Pydantic request/response models for the JSON API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import ScopeMode


class SeedCreate(BaseModel):
    value: str = Field(..., min_length=1, max_length=2048)
    scope_mode: ScopeMode = ScopeMode.domain
    max_depth: int = Field(3, ge=0, le=10)
    enabled: bool = True
    note: str | None = None


class SeedOut(BaseModel):
    id: int
    value: str
    scope_mode: ScopeMode
    max_depth: int
    enabled: bool
    note: str | None = None

    model_config = {"from_attributes": True}


class SearchHitOut(BaseModel):
    url: str
    title: str | None
    snippet: str
    domain: str
    text_rank: float
    pagerank: float
    score: float
    source: str = "local"


class SearchResponse(BaseModel):
    query: str
    total: int
    page: int
    page_size: int
    results: list[SearchHitOut]
    federated: bool = False


class StatsResponse(BaseModel):
    pages: int
    links: int
    seeds_enabled: int
    seeds_total: int
    queue_pending: int
    queue_in_progress: int
    queue_error: int
    discovered_pending: int
    peers_enabled: int
    last_job: dict | None = None
    top_domains: list[dict] = []
