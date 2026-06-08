"""Unit tests for seed-scope matching (no database required)."""

from __future__ import annotations

from app.crawler.scope import ScopeRule, match_scope
from app.models import ScopeMode
from app.urls import normalize_url, registrable_domain


def domain_rule(value: str, max_depth: int = 3) -> ScopeRule:
    return ScopeRule(
        seed_id=1,
        scope_mode=ScopeMode.domain,
        max_depth=max_depth,
        domain=registrable_domain(value),
        url=None,
    )


def prefix_rule(url: str) -> ScopeRule:
    return ScopeRule(
        seed_id=2,
        scope_mode=ScopeMode.prefix,
        max_depth=3,
        domain=registrable_domain(url),
        url=normalize_url(url),
    )


def exact_rule(url: str) -> ScopeRule:
    return ScopeRule(
        seed_id=3,
        scope_mode=ScopeMode.exact,
        max_depth=0,
        domain=registrable_domain(url),
        url=normalize_url(url),
    )


def test_domain_scope_matches_subdomains():
    rules = [domain_rule("example.com")]
    assert match_scope("https://example.com/page", rules) is not None
    assert match_scope("https://blog.example.com/x", rules) is not None
    assert match_scope("http://www.example.com/", rules) is not None


def test_domain_scope_rejects_other_domains():
    rules = [domain_rule("example.com")]
    assert match_scope("https://evil.com/example.com", rules) is None
    assert match_scope("https://notexample.com/", rules) is None


def test_registrable_domain_handles_multi_part_suffix():
    assert registrable_domain("https://shop.example.co.uk/x") == "example.co.uk"
    assert registrable_domain("a.b.example.com") == "example.com"


def test_prefix_scope():
    rules = [prefix_rule("https://example.com/docs")]
    assert match_scope("https://example.com/docs/intro", rules) is not None
    assert match_scope("https://example.com/blog/intro", rules) is None


def test_exact_scope():
    rules = [exact_rule("https://example.com/only")]
    assert match_scope("https://example.com/only", rules) is not None
    assert match_scope("https://example.com/only/more", rules) is None


def test_out_of_scope_and_bad_urls_return_none():
    rules = [domain_rule("example.com")]
    assert match_scope("mailto:foo@example.com", rules) is None
    assert match_scope("javascript:void(0)", rules) is None
    assert match_scope("https://example.com/file.pdf", rules) is None
