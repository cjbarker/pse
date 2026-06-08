"""Unit tests for HTML parsing (no database required)."""

from __future__ import annotations

from app.crawler.parser import parse_html

SAMPLE = """
<!doctype html>
<html lang="en">
  <head>
    <title>  Vintage Macintosh SE/30  </title>
    <style>.x{color:red}</style>
    <script>var a = 1;</script>
  </head>
  <body>
    <h1>The Macintosh SE/30</h1>
    <p>A compact Mac with a 68030 processor.</p>
    <a href="/specs">Specs</a>
    <a href="https://other.example/page#frag">External</a>
    <a href="mailto:a@b.com">Mail</a>
    <a href="/image.png">An image</a>
  </body>
</html>
"""


def test_extracts_title_text_and_lang():
    parsed = parse_html(SAMPLE, "https://mac.example/se30")
    assert parsed.title == "Vintage Macintosh SE/30"
    assert parsed.lang == "en"
    assert "68030 processor" in parsed.text
    # Script/style content must not leak into the indexed text.
    assert "var a" not in parsed.text
    assert "color:red" not in parsed.text


def test_links_are_normalized_and_filtered():
    parsed = parse_html(SAMPLE, "https://mac.example/se30")
    assert "https://mac.example/specs" in parsed.links
    # Fragment stripped from external link.
    assert "https://other.example/page" in parsed.links
    # mailto and image assets are excluded.
    assert all(not link.startswith("mailto:") for link in parsed.links)
    assert all(not link.endswith(".png") for link in parsed.links)


def test_content_hash_is_stable():
    a = parse_html(SAMPLE, "https://mac.example/se30")
    b = parse_html(SAMPLE, "https://mac.example/se30")
    assert a.content_hash == b.content_hash and len(a.content_hash) == 64
