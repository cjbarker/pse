"""SQLAlchemy ORM models for the personalized search engine.

A single Postgres database holds everything: seeds, crawled pages (with their
full-text `search_vector`), the outbound link graph, the crawl frontier/queue,
crawl-job history, the self-seeding discovered-domains queue, and federation peers.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ScopeMode(enum.StrEnum):
    """How a seed's crawl scope is matched against candidate URLs."""

    domain = "domain"  # same registrable domain (and subdomains)
    prefix = "prefix"  # URL must start with the seed value
    exact = "exact"  # only the single seed URL


class CrawlStatus(enum.StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    error = "error"


class JobStatus(enum.StrEnum):
    running = "running"
    finished = "finished"
    failed = "failed"


class DiscoveryStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Seed(Base):
    __tablename__ = "seeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # A bare domain (e.g. "example.com") or a fully-qualified URL.
    value: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    scope_mode: Mapped[ScopeMode] = mapped_column(
        Enum(ScopeMode, name="scope_mode"), default=ScopeMode.domain, nullable=False
    )
    max_depth: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    pages: Mapped[list[Page]] = relationship(back_populates="seed")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seed_id: Mapped[int | None] = mapped_column(
        ForeignKey("seeds.id", ondelete="SET NULL"), nullable=True
    )
    pagerank: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Maintained by the indexer on upsert (title weighted A, body weighted B).
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)

    seed: Mapped[Seed | None] = relationship(back_populates="pages")
    outbound_links: Mapped[list[Link]] = relationship(
        back_populates="src_page",
        foreign_keys="Link.src_page_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_pages_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_pages_pagerank", "pagerank"),
    )


class Link(Base):
    """A directed outbound edge from one crawled page to a target URL.

    `dst_page_id` is resolved during the PageRank step for in-corpus targets.
    """

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    src_page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dst_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    dst_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("pages.id", ondelete="SET NULL"), nullable=True, index=True
    )

    src_page: Mapped[Page] = relationship(
        back_populates="outbound_links", foreign_keys=[src_page_id]
    )

    __table_args__ = (UniqueConstraint("src_page_id", "dst_url", name="uq_link_src_dst"),)


class CrawlQueue(Base):
    """The crawl frontier. Workers claim rows with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "crawl_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    seed_id: Mapped[int | None] = mapped_column(
        ForeignKey("seeds.id", ondelete="CASCADE"), nullable=True
    )
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[CrawlStatus] = mapped_column(
        Enum(CrawlStatus, name="crawl_status"),
        default=CrawlStatus.pending,
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enqueued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.running, nullable=False
    )
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DiscoveredDomain(Base):
    """Self-seeding queue: domains harvested from clicked search results."""

    __tablename__ = "discovered_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("pages.id", ondelete="SET NULL"), nullable=True
    )
    times_seen: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DiscoveryStatus] = mapped_column(
        Enum(DiscoveryStatus, name="discovery_status"),
        default=DiscoveryStatus.pending,
        nullable=False,
        index=True,
    )
    discovered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Peer(Base):
    """A trusted remote PSE node to federate searches with."""

    __tablename__ = "peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
