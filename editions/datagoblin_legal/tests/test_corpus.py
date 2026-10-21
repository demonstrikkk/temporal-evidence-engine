from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.api import DomainProfile, check_profile  # noqa: E402
from ingest.normalize import compose, fields_for, find_span, quote_for, summary_quote  # noqa: E402
from ingest.parse import constitution_article  # noqa: E402
from ingest.validate import validate  # noqa: E402

EDITION = Path(__file__).resolve().parents[1]
CORPUS = EDITION / "corpus" / "documents.json"


def test_the_profile_conforms() -> None:
    profile = DomainProfile.model_validate_json(
        (EDITION / "edition.json").read_text(encoding="utf-8"))
    assert check_profile(profile) == []
    assert {b.name for b in profile.buckets} == {"local", "regional", "national"}


def test_a_quote_is_a_slice_of_the_stored_text() -> None:
    text = "Title: Example\n\nHeld that free legal aid is a right."
    span = find_span(text, "free   legal aid")
    assert span is not None
    assert text[span[0]:span[1]] == "free legal aid"
    assert quote_for(text, "not present") == ""


def test_compose_puts_every_header_value_somewhere_quoteable() -> None:
    document = {"doc_id": "x", "title": "Example", "doc_type": "judgment",
                "citation": "(2024) 1 SCC 1", "court": "Supreme Court of India",
                "jurisdiction": "India", "year": 2024, "act": "Example Act",
                "section": ""}
    text, lines = compose(document, "Held that free legal aid is a right.")
    fields = fields_for(document, text, lines, "Held that free legal aid is a right.")

    assert lines["document_title"] == "Title: Example"
    assert fields["year"]["value"] == 2024
    assert fields["court"]["quote"] == "Court: Supreme Court of India"
    assert "section" not in fields
    for field in fields.values():
        assert find_span(text, field["quote"]) is not None


def test_summary_is_verbatim_not_a_paraphrase() -> None:
    body = ("This is a sufficiently long opening paragraph that states the "
            "question and the holding in the court's own words. " * 3).strip()
    quote = summary_quote(body)
    assert quote in body
    assert quote == body or body.startswith(quote)


def test_an_article_row_stores_the_article_not_the_whole_constitution() -> None:
    body = ("Preamble text that must not become Article 21's summary.\n"
            "21. Protection of life and personal liberty. No person shall be "
            "deprived of his life or personal liberty except according to "
            "procedure established by law.\n"
            "22. Protection against arrest and detention. A person arrested "
            "shall be informed of the grounds of arrest.")

    excerpt = constitution_article(
        body, "Art. 21", "Constitution of India, Art. 21 - Protection of life and personal liberty")

    assert excerpt.startswith("21. Protection of life and personal liberty.")
    assert "Preamble" not in excerpt
    assert "22. Protection against arrest" not in excerpt


def test_a_judgment_summary_starts_after_the_caption() -> None:
    body = ("IN THE SUPREME COURT OF INDIA\nJUDGMENT\n"
            "The petitioner apprehends arrest in a case under Section 498-A, "
            "and the court must decide whether that arrest is necessary. "
            "The court held that reasons must be recorded before arrest.")

    summary = summary_quote(body)

    assert summary.startswith("The petitioner apprehends arrest")
    assert "IN THE SUPREME COURT" not in summary


def test_the_built_corpus_is_complete_and_locatable() -> None:
    if not CORPUS.is_file():
        return
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert validate(payload, expected_count=68) == []
    assert len(payload["documents"]) == 68
    judgments = [d for d in payload["documents"] if d["doc_type"] == "judgment"]
    assert len(judgments) == 14
    assert all(d["fields"] for d in payload["documents"])
    assert all(d["fields"].get("document_title") for d in payload["documents"])
