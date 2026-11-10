"""A page's own publication date, read without a model or a guess.

The engine dates news results and little else, so `valid_time` was empty on most
cells. These tests pin the two shapes a page actually uses, the order between
them, and the refusals: an edit date is not a publication date, and a string that
is not a date yields "" rather than something plausible.
"""
from __future__ import annotations

import asyncio

from app.services import crawler as crawler_svc
from app.services import pubdate


def test_a_meta_publication_time_is_read():
    html = ('<html><head><meta property="article:published_time"'
            ' content="2024-09-03T10:00:00Z"></head><body>x</body></html>')
    assert pubdate.from_html(html) == "2024-09-03"


def test_attribute_order_does_not_matter():
    html = ('<meta content="2024-09-03" property="article:published_time">')
    assert pubdate.from_html(html) == "2024-09-03"


def test_a_slash_date_in_a_citation_meta_tag_is_read():
    html = ('<meta name="citation_publication_date" content="2024/09/03">')
    assert pubdate.from_html(html) == "2024-09-03"


def test_json_ld_date_published_is_read_when_no_meta_states_one():
    html = ('<script type="application/ld+json">'
            '{"@type": "NewsArticle", "datePublished": "2023-01-15T09:00:00+00:00"}'
            '</script>')
    assert pubdate.from_html(html) == "2023-01-15"


def test_a_meta_date_beats_the_json_ld_graph():
    # A JSON-LD graph can date several entities; the meta tag is written for
    # the article itself.
    html = ('<meta property="og:published_time" content="2024-09-03">'
            '<script type="application/ld+json">'
            '{"@type": "Person", "dateCreated": "1999-01-01"}</script>')
    assert pubdate.from_html(html) == "2024-09-03"


def test_an_edit_date_is_not_a_publication_date():
    """`dateModified` describes the page, not when its content was true."""
    html = ('<script type="application/ld+json">'
            '{"@type": "NewsArticle", "dateModified": "2026-01-01"}</script>')
    assert pubdate.from_html(html) == ""


def test_garbage_is_absent_rather_than_guessed():
    html = '<meta property="article:published_time" content="last Tuesday">'
    assert pubdate.from_html(html) == ""
    assert pubdate.from_html("") == ""
    assert pubdate.from_html("<p>no head at all</p>") == ""


def test_a_json_ld_date_in_a_list_or_wrapped_value_is_read():
    html = ('<script type="application/ld+json">'
            '{"datePublished": {"@value": "2022-05-06"}}</script>')
    assert pubdate.from_html(html) == "2022-05-06"


def test_a_malformed_json_ld_block_does_not_hide_a_meta_date():
    html = ('<script type="application/ld+json">{not json</script>'
            '<meta name="pubdate" content="2021-04-05">')
    assert pubdate.from_html(html) == "2021-04-05"


# -- the crawl carries it ------------------------------------------------------

HTML = ('<html><head><meta property="article:published_time" '
        'content="2021-06-01T00:00:00Z"></head>'
        '<body><h1>Acme</h1><p>Acme employs 240 people.</p></body></html>')


def run(coro):
    return asyncio.run(coro)


async def _noop(_):
    pass


def test_a_fetched_page_is_dated_from_its_own_markup():
    stored = {}

    async def _fetch(url, method):
        return {"url": url, "final_url": url, "title": "Acme",
                "html": HTML, "method": "http"}

    async def _persist(page):
        stored[page["url"]] = page
        return "page-1"

    pages, _ = run(crawler_svc.fetch_all(
        [{"url": "https://x.example/a", "title": ""}], _fetch, _noop, _noop, {}, _persist))
    assert pages[0]["published_date"] == "2021-06-01"
    assert stored["https://x.example/a"]["published_date"] == "2021-06-01"


def test_a_reused_page_without_a_stored_date_is_dated_from_its_html():
    async def _fetch(url, method):
        raise AssertionError("reuse must not fetch")

    async def _reuse(url):
        return {"id": "p-old", "url": url, "title": "Acme",
                "markdown": "Acme employs 240 people.", "raw_html": HTML,
                "content_hash": "h", "method": "crawl4ai"}

    pages, _ = run(crawler_svc.fetch_all(
        [{"url": "https://x.example/a", "title": ""}],
        _fetch, _noop, _noop, {}, None, _reuse))
    assert pages[0]["published_date"] == "2021-06-01"
