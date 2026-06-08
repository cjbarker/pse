"""Seed-scope matching: decide whether a candidate URL is in-scope for crawling.

Keeping the crawl inside the curated set of seeds is what makes a PSE tractable
and high-signal. A URL is crawlable only if it matches at least one *enabled* seed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ScopeMode, Seed
from app.urls import normalize_url, registrable_domain


@dataclass(frozen=True)
class ScopeRule:
    seed_id: int
    scope_mode: ScopeMode
    max_depth: int
    # Precomputed match key: registrable domain (domain mode) or normalized URL.
    domain: str
    url: str | None


def rule_from_seed(seed: Seed) -> ScopeRule:
    value = seed.value.strip()
    normalized = normalize_url(value if "://" in value else f"https://{value}")
    return ScopeRule(
        seed_id=seed.id,
        scope_mode=seed.scope_mode,
        max_depth=seed.max_depth,
        domain=registrable_domain(value),
        url=normalized,
    )


def _matches(rule: ScopeRule, url: str) -> bool:
    if rule.scope_mode == ScopeMode.domain:
        return bool(rule.domain) and registrable_domain(url) == rule.domain
    if rule.scope_mode == ScopeMode.prefix:
        return rule.url is not None and url.startswith(rule.url)
    if rule.scope_mode == ScopeMode.exact:
        return rule.url is not None and url == rule.url
    return False


def match_scope(url: str, rules: list[ScopeRule]) -> ScopeRule | None:
    """Return the first scope rule the URL satisfies, or ``None`` if out of scope."""
    normalized = normalize_url(url)
    if normalized is None:
        return None
    for rule in rules:
        if _matches(rule, normalized):
            return rule
    return None
