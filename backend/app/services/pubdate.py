"""When a page says its own content was published.

The search engine supplies `published_date` for news-flavoured results, and for
everything else it is absent. The page itself almost always knows: news and blog
articles carry `article:published_time` in a `<meta>` tag, and most CMSes publish
`schema.org/datePublished` in JSON-LD. Reading that is free and deterministic,
and it is what turns "we know when we fetched it" into "we know when the fact was
true".

Only the page's own statement is used. Nothing here infers a date from prose,
guesses a year from the URL, or falls back to the fetch time: an invented date is
worse than an absent one, because the absent one is reported as `unknown` and the
invented one is checkable against nothing.

`dateModified` is deliberately not read. A page edited last week can describe an
event from 2019, and labelling the content with its last edit is exactly the kind
of confident wrong date this module exists to avoid.
"""
from __future__ import annotations

import re

from app.services import jsonld, temporal

#: `<meta>` tags, then their attributes. Reading attributes rather than matching
#: one fixed order matters: both `<meta property="x" content="y">` and
#: `<meta content="y" property="x">` are real, and the latter is common.
_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*["\']([^"\']*)["\']')

#: Meta keys that state a publication date, not an edit date.
_KEYS = frozenset({
    "article:published_time",
    "og:published_time",
    "datepublished",
    "citation_publication_date",
    "citation_date",
    "pubdate",
    "publish-date",
    "publish_date",
    "publication_date",
    "dc.date.issued",
    "dcterms.issued",
})

#: JSON-LD properties, in the order worth trusting.
_JSON_KEYS = ("datePublished", "dateCreated", "uploadDate")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return next((s for s in (_text(v) for v in value) if s), "")
    if isinstance(value, dict):
        for key in ("@value", "value", "date"):
            if key in value:
                return _text(value[key])
    return ""


def _iso(raw: str) -> str:
    """A real date in ISO form, or "". Slash dates are a real meta format.

    `citation_publication_date` is `YYYY/MM/DD` in the wild, which
    `temporal.parse_date` reads as a bare year (the year pattern matches first).
    Only the year-first shape is rewritten; `03/04/2024` stays with the day-first
    reader rather than being silently reinterpreted here.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}/\d{1,2}/\d{1,2}(?!\d)", text):
        text = text.replace("/", "-")
    d = temporal.parse_date(text)
    return d.isoformat() if d else ""


def from_html(html: str) -> str:
    """The page's own publication date, ISO, or "" when it states none.

    A `<meta>` declaration wins over JSON-LD: it is one attribute written for
    exactly this purpose, while a JSON-LD graph can describe several entities
    and the first `datePublished` may belong to a review rather than the article.
    """
    html = html or ""
    for tag in _META.finditer(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag.group(0))}
        key = (attrs.get("property") or attrs.get("name")
               or attrs.get("itemprop") or "").lower()
        if key in _KEYS:
            got = _iso(attrs.get("content", ""))
            if got:
                return got
    for node in jsonld.documents(html):
        for key in _JSON_KEYS:
            if key in node:
                got = _iso(_text(node.get(key)))
                if got:
                    return got
    return ""


def from_page(page: dict) -> str:
    """`from_html` over whichever body the crawler kept for a page."""
    return from_html(str(page.get("rendered_html") or page.get("html")
                         or page.get("raw_html") or ""))
