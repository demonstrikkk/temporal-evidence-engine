"""The labelled-lines rung: reading a listing without a model.

The rung's own docstring promised that a listing page's heading supplies the
record's name. The guard that was supposed to enforce it counted headings *after*
the walk had already broken at the second one, so `headings >= 2` was never true
and the identity field was never filled. On the common shape — a page title
followed by one heading per record — the rung returned nothing at all, so every
such page still paid for a model call.

These tests pin the four things that failure hid:

1. a listing yields its first record *and* that record's name;
2. a page title, a section heading, or a single-record page is not a name;
3. an explicit `Company: ...` line outranks an inferred heading;
4. the votes survive the whole `extract_page` path with zero model calls.
"""
from __future__ import annotations

import asyncio

from app.services import extractor as extractor_svc
from app.services import labelled as L
from app.services import ladder as ladder_svc


def fields(*names: str) -> list[dict]:
    return [{"name": n, "type": "string", "required": False,
             "description": n.replace("_", " ")} for n in names]


LISTING = """# Top AI Companies

A hand-picked list of the companies to watch.

## 1. Mistral
Industry: Artificial Intelligence / Generative AI
Location: Paris, Île-de-France, France

## 2. Anthropic
Industry: Artificial Intelligence
Location: San Francisco, California
"""


def test_a_listing_reads_the_first_record_and_its_name():
    out = L.labelled_pairs(LISTING, fields("company_name", "industry", "location"))

    assert out["company_name"] == ("Mistral", "## 1. Mistral")
    assert out["industry"][0] == "Artificial Intelligence / Generative AI"
    assert out["location"][0] == "Paris, Île-de-France, France"


def test_the_second_record_never_leaks_into_the_first():
    out = L.labelled_pairs(LISTING, fields("company_name", "location"))
    assert out["company_name"][0] == "Mistral"
    assert "San Francisco" not in out["location"][0]


def test_a_page_title_is_not_an_entity_name():
    """The title's section is the intro; the next section is the record."""
    out = L.labelled_pairs(LISTING, fields("company_name", "industry"))
    assert out["company_name"][0] == "Mistral"
    assert out["company_name"][0] != "Top AI Companies"


def test_a_single_record_page_does_not_take_its_heading_as_a_name():
    page = """# Senior Machine Learning Engineer

Location: Remote
Salary: 180000
"""
    out = L.labelled_pairs(page, fields("company_name", "location", "salary"))
    assert "company_name" not in out
    assert out["location"][0] == "Remote"


def test_a_section_heading_is_not_a_name():
    """`h1` title plus one `h2` section is a record, not a listing."""
    page = """# Roku

## Job details
Location: Remote
Company: Roku
"""
    out = L.labelled_pairs(page, fields("company_name", "location"))
    assert out["company_name"] == ("Roku", "Company: Roku")


def test_an_explicit_label_outranks_the_heading():
    page = """## 1. Wrong name
Company: Right name
Industry: AI

## 2. Another
Industry: AI
"""
    out = L.labelled_pairs(page, fields("company_name", "industry"))
    assert out["company_name"][0] == "Right name"


def test_a_long_labelled_value_is_kept():
    """160 characters was an arbitrary cut, and it dropped stated addresses."""
    address = ("1 Example Street, Suite 400, Building C, Some District, "
               "Some City, Some State 12345, United States of America, "
               "care of the receiving department on the second floor")
    assert 160 < len(address) <= L._MAX_VALUE_CHARS
    page = f"## 1. Acme\nAddress: {address}\n\n## 2. Globex\nAddress: Elsewhere\n"
    out = L.labelled_pairs(page, fields("company_name", "address"))
    assert out["address"][0] == address


def test_an_oversize_labelled_value_is_refused():
    paragraph = "word " * 200
    page = ("## 1. Acme\nIndustry: AI\n"
            f"Description: {paragraph}\n\n## 2. Globex\nIndustry: AI\n")
    out = L.labelled_pairs(page, fields("company_name", "industry", "description"))
    assert "description" not in out
    assert out["company_name"][0] == "Acme"


def test_every_value_is_quoted_from_the_page():
    out = L.labelled_pairs(LISTING, fields("company_name", "industry", "location"))
    for name, (value, quote) in out.items():
        assert quote in LISTING, f"the quote for {name} is not on the stored page"
        assert value in quote, f"the value for {name} is not inside its quote"


def test_a_labelled_intro_line_does_not_beat_the_record_headings():
    """A `Updated:` line in the page intro must not make the intro the record."""
    page = """# Top AI Companies

Updated: 2024
Location: Global

## 1. Mistral
Industry: AI
Location: Paris

## 2. Anthropic
Industry: AI
Location: San Francisco
"""
    out = L.labelled_pairs(page, fields("company_name", "industry", "location"))
    assert out["company_name"][0] == "Mistral"
    assert out["location"][0] == "Paris"


def test_the_rung_reads_the_markdown_shapes_a_reducer_emits():
    """A bullet or bold label is still the page saying `Industry: AI`."""
    shapes = [
        "- Industry: AI",
        "* Industry: AI",
        "1. Industry: AI",
        "**Industry:** AI",
        "- **Industry:** AI",
        "**Industry**: AI",
        "_Industry_: AI",
    ]
    for line in shapes:
        page = f"## 1. Acme\n{line}\n\n## 2. Globex\nIndustry: Other\n"
        out = L.labelled_pairs(page, fields("company_name", "industry"))
        assert out["industry"] == ("AI", line), f"missed the shape {line!r}"


def test_the_rung_returns_the_ladder_shape():
    out = L.rung(fields("company_name", "location"), {"page": {"markdown": LISTING}})
    assert out["company_name"]["read_method"] == "labelled"
    assert out["company_name"]["quote"] == "## 1. Mistral"
    assert out["company_name"]["value"] == "Mistral"


def test_extract_page_reads_a_listing_without_calling_the_model():
    calls: list[str] = []

    async def llm(prompt, schema=None):
        calls.append(prompt)
        return {"data": {}, "provider": "llm"}

    plan = {"goal": "AI companies", "fields": fields("company_name", "industry", "location")}
    page = {"url": "https://example.test/ai-companies", "markdown": LISTING,
            "page_id": "pg1", "title": "Top AI Companies"}

    records, provider = asyncio.run(extractor_svc.extract_page(plan, page, llm))

    assert calls == [], f"the deterministic path paid for {len(calls)} model call(s)"
    assert records, "every field was filled and the record still vanished"
    assert provider.startswith("ladder:")
    got = {k: v.get("value") for k, v in records[0]["fields"].items()}
    assert got["company_name"] == "Mistral"
    assert got["location"] == "Paris, Île-de-France, France"


def test_structured_routes_run_before_the_generic_label_reader(monkeypatch):
    """First writer wins, so the more authoritative route must be asked first."""
    order: list[str] = []

    def route(name):
        def _rung(_fields, _ctx):
            order.append(name)
            return {}
        return _rung

    monkeypatch.setattr(ladder_svc, "selectors_rung", route("selectors"))
    monkeypatch.setattr(ladder_svc, "json_ld_rung", route("json_ld"))
    monkeypatch.setattr(L, "rung", route("labelled"))

    async def llm(prompt, schema=None):
        return {"data": {"records": []}, "provider": "llm"}

    page = {"url": "https://example.test/x", "html": "<html><body>x</body></html>",
            "markdown": "## A\nIndustry: x\n## B\nIndustry: y\n"}
    asyncio.run(extractor_svc.extract_page(
        {"goal": "x", "fields": fields("company_name")}, page, llm))

    assert order == ["selectors", "json_ld", "labelled"]


def test_the_ladder_summary_counts_every_deterministic_route():
    """A labelled/json win is a model call avoided, not zero."""
    trace = [
        {"rung": "json", "asked": 3, "filled": 2},
        {"rung": "labelled", "asked": 1, "filled": 1},
        {"rung": "llm", "asked": 0, "filled": 0},
    ]
    summary = ladder_svc.summarise(trace, {"a": {}, "b": {}, "c": {}})
    assert summary["model_avoided"] == 3
    assert summary["by_rung"] == {"json": 2, "labelled": 1, "llm": 0}
