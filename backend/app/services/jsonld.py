"""schema.org JSON-LD, read out of a page.

A page that publishes JSON-LD is stating its own facts in a machine-readable
form: a job board marks up `JobPosting` with `title`, `hiringOrganization`,
`jobLocation` and `baseSalary`; a product page marks up `Product`. Nothing is
inferred, so a value read this way needs no quote check and outranks every route
that has to interpret prose.

It is the cheapest rung and the most trustworthy one, and it is on the page for
free. Leaving it unused means asking a language model to read a sentence that the
page already published as structured data.

Two shapes are handled, because pages use both:

* a single object;
* an array, or a `@graph` wrapper around several.

Only the first record is used. This is the single-item rung: the ladder asks it
for the fields of the record it is completing, and a page carrying several
entities is better served by the container rung, which knows how to tell them
apart.
"""
from __future__ import annotations

import json
import re
from typing import Any

#: `application/ld+json` blocks. Non-greedy and case-insensitive so a
#: mis-declared charset attribute does not lose the block.
_LD_BLOCK = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)

#: schema.org properties worth reading, mapped to the field name they answer.
#: Longest-first at lookup time, so `jobLocation` is tried before `jobTitle` is
#: considered a candidate for a field called `location` — handled by the
#: per-field ordered key list in `_candidates`.
_PROPERTY_HINTS: dict[str, tuple[str, ...]] = {
    "company_name": ("hiringorganization", "employer", "brand", "publisher", "author", "name"),
    "company": ("hiringorganization", "employer", "brand", "publisher", "author", "name"),
    "title": ("title", "name", "headline"),
    "job_title": ("title", "name"),
    "role": ("title", "name"),
    "location": ("joblocation", "location", "address", "worklocation"),
    "hq_country": ("joblocation", "addresscountry", "country", "location"),
    "city": ("addresslocality", "joblocation", "address"),
    "website": ("url", "sameas", "mainentityofpage"),
    "industry": ("industry", "about", "description"),
    "description": ("description", "about", "abstract"),
    "founded": ("foundingdate", "foundingdate", "datecreated"),
    "date": ("dateposted", "validthrough", "foundingdate"),
    "salary": ("basesalary", "estimatedsalary"),
    "salary_range": ("basesalary", "estimatedsalary"),
    "employment_type": ("employmenttype", "basalary", "joblocation"),
    "open_roles": ("title", "name"),
    "valuation": ("description", "about"),
}


def _blocks(html: str) -> list[str]:
    return [m.group(1) for m in _LD_BLOCK.finditer(html or "")]


def _walk(node: Any, out: list[dict]) -> None:
    """Flatten a JSON-LD document into the objects it declares.

    Handles the three shapes pages actually use: an object, an array, and a
    `@graph` wrapper whose members are the objects of interest.
    """
    if isinstance(node, list):
        for item in node:
            _walk(item, out)
        return
    if not isinstance(node, dict):
        return
    graph = node.get("@graph")
    if isinstance(graph, (list, dict)):
        _walk(graph, out)
        return
    out.append(node)


def documents(html: str) -> list[dict]:
    """Every JSON-LD object the page declares, in document order."""
    out: list[dict] = []
    for raw in _blocks(html):
        try:
            _walk(json.loads(raw.strip()), out)
        except (ValueError, TypeError):
            # A malformed block is common and says nothing about the others.
            continue
    return out


def _as_text(value: Any) -> str:
    """Flatten a schema.org value to readable text.

    schema.org allows a bare string, an object, or a list of either, at any depth
    for almost any property, so `title` can legitimately arrive as
    `{"@value": "x"}` or `["a", "b"]`. Anything the page did not state as text
    yields "" rather than a Python repr.
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, list):
        parts = [_as_text(v) for v in value]
        return "; ".join(p for p in parts if p).strip()
    if isinstance(value, dict):
        for key in ("@value", "name", "value", "text", "streetAddress",
                    "addressLocality", "addressCountry"):
            if key in value:
                return _as_text(value[key])
        return ""
    return ""


def _candidates(node: dict, field: str) -> list[str]:
    """Property names for `field`, best first.

    An exact case-insensitive match on the field name always wins, so a plan
    asking for `industry` reads a page's `industry` rather than falling through
    to a hint. Only then are the hints tried, in their declared order.
    """
    wanted = field.replace("_", "").lower()
    lowered = {k.lower(): k for k in node}
    out: list[str] = []
    if wanted in lowered:
        out.append(lowered[wanted])
    for hint in _PROPERTY_HINTS.get(field.lower(), ()):
        if hint in lowered and lowered[hint] not in out:
            out.append(lowered[hint])
    return out


def extract_from(docs: list[dict], field: str) -> tuple[str, str]:
    """`extract` against already-parsed documents.

    The ladder asks about several fields on one page. Parsing the same
    `<script type="application/ld+json">` blocks per field meant every field paid
    the regex scan and the `json.loads` again — the whole page's structured data
    decoded once per column, on a route that runs for every page of every run.
    Callers that ask more than once should parse with `documents` and use this.
    """
    for node in docs:
        for prop in _candidates(node, field):
            text = _as_text(node.get(prop))
            if text:
                return text, prop
    return "", ""


def extract(html: str, field: str) -> tuple[str, str]:
    """The first declared object that states `field`. Returns (value, property).

    "First" is document order, which is the order the page chose to present its
    entities. A page carrying both an `Organization` and a `JobPosting` will
    usually declare the organisation first, so the ladder gets whatever the page
    led with — which is a reason to treat this rung as a fast path rather than an
    authority, and it is why the value still passes the field's own type check
    downstream.
    """
    return extract_from(documents(html), field)


#: `@type` values that describe the site around the content rather than the
#: content. `Organization` is handled separately: it is furniture only when it
#: carries nothing that makes it the subject.
_PAGE_LEVEL_TYPES = frozenset({
    "website", "webpage", "newsmediaorganization", "blog",
    "collectionpage", "itemlist", "searchresultspage",
})

#: Properties that make an `Organization` the subject of the page rather than
#: its publisher. A company's own About page carries at least one.
_SUBJECT_MARKERS = ("subOrganization", "hasCredential", "address", "identifier",
                    "legalName", "founder", "numberOfEmployees")


def _norm_text(text: object) -> str:
    return " ".join(str(text or "").lower().split()).strip(" |-–—:·")


def site_entity_names(docs: list[dict]) -> set[str]:
    """Names of entities that describe the page rather than its content.

    `WebSite`, `NewsMediaOrganization`, `Blog`, `WebPage` and a bare
    `Organization` with neither `subOrganization` nor credentials are page
    furniture. An `Organization` that is clearly the subject — one carrying its
    own `hasCredential`, an address, or an identifier — is left alone, so a
    company page that publishes its own Organization still works.

    Takes parsed documents so the caller that already parsed the page does not
    decode it a second time.
    """
    site: set[str] = set()
    for node in docs:
        raw_type = node.get("@type") or node.get("type") or ""
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        names = {str(t).lower() for t in types if t}
        text = node.get("name")
        if isinstance(text, dict):
            text = text.get("@value") or text.get("name")
        if not isinstance(text, str) or not text.strip():
            continue
        if names & _PAGE_LEVEL_TYPES:
            site.add(_norm_text(text))
        elif "organization" in names and not any(
                node.get(k) for k in _SUBJECT_MARKERS):
            site.add(_norm_text(text))
    return site
