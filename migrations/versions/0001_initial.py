"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: these enums are created/dropped explicitly in up/downgrade, so
# create_table must not also try to emit CREATE TYPE for the columns that use them.
scope_mode = postgresql.ENUM("domain", "prefix", "exact", name="scope_mode", create_type=False)
crawl_status = postgresql.ENUM(
    "pending", "in_progress", "done", "error", name="crawl_status", create_type=False
)
job_status = postgresql.ENUM(
    "running", "finished", "failed", name="job_status", create_type=False
)
discovery_status = postgresql.ENUM(
    "pending", "approved", "rejected", name="discovery_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (scope_mode, crawl_status, job_status, discovery_status):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "seeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(2048), nullable=False, unique=True),
        sa.Column("scope_mode", scope_mode, nullable=False, server_default="domain"),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "pages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("lang", sa.String(16), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "seed_id", sa.Integer(), sa.ForeignKey("seeds.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("pagerank", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
    )
    op.create_index("ix_pages_domain", "pages", ["domain"])
    op.create_index("ix_pages_content_hash", "pages", ["content_hash"])
    op.create_index("ix_pages_pagerank", "pages", ["pagerank"])
    op.create_index("ix_pages_search_vector", "pages", ["search_vector"], postgresql_using="gin")

    op.create_table(
        "links",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "src_page_id",
            sa.BigInteger(),
            sa.ForeignKey("pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dst_url", sa.String(2048), nullable=False),
        sa.Column(
            "dst_page_id",
            sa.BigInteger(),
            sa.ForeignKey("pages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("src_page_id", "dst_url", name="uq_link_src_dst"),
    )
    op.create_index("ix_links_src_page_id", "links", ["src_page_id"])
    op.create_index("ix_links_dst_page_id", "links", ["dst_page_id"])

    op.create_table(
        "crawl_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column(
            "seed_id", sa.Integer(), sa.ForeignKey("seeds.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", crawl_status, nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_crawl_queue_status", "crawl_queue", ["status"])

    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", job_status, nullable=False, server_default="running"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "discovered_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "source_page_id",
            sa.BigInteger(),
            sa.ForeignKey("pages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", discovery_status, nullable=False, server_default="pending"),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_discovered_domains_status", "discovered_domains", ["status"])

    op.create_table(
        "peers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("peers")
    op.drop_index("ix_discovered_domains_status", table_name="discovered_domains")
    op.drop_table("discovered_domains")
    op.drop_table("crawl_jobs")
    op.drop_index("ix_crawl_queue_status", table_name="crawl_queue")
    op.drop_table("crawl_queue")
    op.drop_index("ix_links_dst_page_id", table_name="links")
    op.drop_index("ix_links_src_page_id", table_name="links")
    op.drop_table("links")
    op.drop_index("ix_pages_search_vector", table_name="pages")
    op.drop_index("ix_pages_pagerank", table_name="pages")
    op.drop_index("ix_pages_content_hash", table_name="pages")
    op.drop_index("ix_pages_domain", table_name="pages")
    op.drop_table("pages")
    op.drop_table("seeds")
    bind = op.get_bind()
    for enum in (discovery_status, job_status, crawl_status, scope_mode):
        enum.drop(bind, checkfirst=True)
