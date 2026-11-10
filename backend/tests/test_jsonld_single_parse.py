"""The JSON-LD rung parses a page's structured data once, not once per field.

The rung used to call `present(html)`, `_site_entity_names(html)` and then
`extract(html, field)` for every field — three regex scans of the whole document
and one `json.loads` per field, on a route that runs for every page of every run.
The behaviour is identical either way, so only a counting test can see the
difference; that is what this file is.

`extract` stays as the one-field convenience and has to keep returning exactly
what `extract_from` returns for the same documents.
"""
from __future__ import annotations

import json

from app.services import jsonld, ladder

HTML = """
<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "WebSite", "name": "Example News"},
  {"@type": "Organization", "name": "Acme Inc",
   "url": "https://acme.example", "industry": "Widgets",
   "address": {"streetAddress": "1 Main St"}}
]}
</script></head><body>Acme Inc makes widgets.</body></html>
"""

FIELDS = [{"name": "website"}, {"name": "industry"},
          {"name": "company_name"}, {"name": "description"}]


def test_the_rung_decodes_the_page_once_for_all_fields(monkeypatch):
    calls = {"n": 0}
    real = jsonld.documents

    def counting(html):
        calls["n"] += 1
        return real(html)

    monkeypatch.setattr(jsonld, "documents", counting)
    out = ladder.json_ld_rung(FIELDS, {"page": {"html": HTML}})
    assert calls["n"] == 1
    assert out["website"]["value"] == "https://acme.example"
    assert out["industry"]["value"] == "Widgets"


def test_a_page_with_no_json_ld_is_read_once_and_returns_nothing(monkeypatch):
    calls = {"n": 0}
    real = jsonld.documents

    def counting(html):
        calls["n"] += 1
        return real(html)

    monkeypatch.setattr(jsonld, "documents", counting)
    assert ladder.json_ld_rung(FIELDS, {"page": {"html": "<p>plain</p>"}}) == {}
    assert calls["n"] == 1


def test_extract_from_matches_extract():
    docs = jsonld.documents(HTML)
    for field in ("company_name", "website", "industry", "description"):
        assert jsonld.extract_from(docs, field) == jsonld.extract(HTML, field)


def test_a_malformed_block_does_not_hide_a_good_one():
    broken = ('<script type="application/ld+json">{not json</script>'
              + HTML)
    assert jsonld.extract(broken, "industry") == ("Widgets", "industry")


def test_graph_members_are_flattened_in_document_order():
    docs = jsonld.documents(HTML)
    assert [d.get("@type") for d in docs] == ["WebSite", "Organization"]


def test_site_entity_names_reads_parsed_documents():
    docs = jsonld.documents(HTML)
    # The WebSite is furniture; the Organization carries an address, so it is
    # the subject and must not be refused.
    assert jsonld.site_entity_names(docs) == {"example news"}
    assert json.loads(json.dumps(docs)) == docs
