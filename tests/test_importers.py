"""Unit tests for seed importers (no database required)."""

from __future__ import annotations

from app.seeding.importers import (
    domains_from_bookmarks,
    domains_from_opml,
    domains_from_text,
    parse_upload,
)


def test_domains_from_text_dedupes_and_strips_comments():
    blob = """
    example.com
    https://blog.example.com/post   # same registrable domain
    another.org
    # a full comment line
    third.net, fourth.io
    """
    domains = domains_from_text(blob)
    assert domains == ["example.com", "another.org", "third.net", "fourth.io"]


def test_domains_from_bookmarks():
    html = """
    <!DOCTYPE NETSCAPE-Bookmark-file-1>
    <DL>
      <DT><A HREF="https://news.example.com/a">A</A>
      <DT><A HREF="https://docs.python.org/3/">Py</A>
    </DL>
    """
    domains = domains_from_bookmarks(html)
    assert "example.com" in domains
    assert "python.org" in domains


def test_domains_from_opml():
    opml = """<?xml version="1.0"?>
    <opml version="2.0"><body>
      <outline text="Blog" type="rss"
        xmlUrl="https://blog.example.com/feed" htmlUrl="https://blog.example.com/"/>
      <outline text="News" type="rss" xmlUrl="https://news.another.org/rss"/>
    </body></opml>
    """
    domains = domains_from_opml(opml)
    assert "example.com" in domains
    assert "another.org" in domains


def test_parse_upload_dispatches_by_extension_and_content():
    assert "example.com" in parse_upload("bookmarks.html", '<a href="https://example.com">x</a>')
    opml = '<?xml version="1.0"?><opml><body><outline xmlUrl="https://x.example.org/feed"/></body></opml>'
    assert "example.org" in parse_upload("feeds.opml", opml)
    assert "plain.dev" in parse_upload("list.txt", "plain.dev")
