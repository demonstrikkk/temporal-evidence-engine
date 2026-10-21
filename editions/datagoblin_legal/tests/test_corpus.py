from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.api import DomainProfile, check_profile  # noqa: E402
from ingest.normalize import (BOOLEAN_SUPPORT, compose, fields_for,  # noqa: E402
                              find_span, quote_for, summary_quote, tier_for)
from ingest.parse import constitution_article  # noqa: E402
from ingest.validate import validate  # noqa: E402

EDITION = Path(__file__).resolve().parents[1]
CORPUS = EDITION / "corpus" / "documents.json"


def _manifest() -> dict:
    return json.loads((EDITION / "edition.json").read_text(encoding="utf-8"))


def test_the_profile_conforms() -> None:
    profile = DomainProfile.model_validate_json(
        (EDITION / "edition.json").read_text(encoding="utf-8"))
    assert check_profile(profile) == []
    assert {b.name for b in profile.buckets} == {"local", "regional", "national"}


def test_the_tier_comes_from_the_manifest_not_from_a_place_name_in_code() -> None:
    """A tier is derived from the manifest's own metadata.

    The rules live in `edition.json`: which states are union law, and which
    state this edition serves. Adding a state is an edit to that list, not an
    edit to the code that reads it.
    """
    policy = _manifest()["tiering"]
    assert policy["national_states"]
    assert policy["home_jurisdiction"]

    assert tier_for({"state": "CENTRAL", "jurisdiction": "India"}, policy) == "national"
    assert tier_for({"state": "Delhi", "jurisdiction": "Delhi"}, policy) == "local"
    assert tier_for({"state": "Bihar", "jurisdiction": "Bihar"}, policy) == "regional"


def test_a_document_with_nowhere_to_go_is_a_build_failure() -> None:
    """A document with no state and no jurisdiction is refused, not guessed.

    Defaulting it to national would put a document in a tier nobody chose.
    """
    policy = _manifest()["tiering"]
    try:
        tier_for({"state": "", "jurisdiction": ""}, policy)
    except ValueError as exc:
        assert "tier" in str(exc)
    else:
        raise AssertionError("an unplaceable document was silently tiered")


def test_every_source_can_be_placed_in_a_tier() -> None:
    """Every document in the manifest carries the metadata the tier needs.

    A source added without a state would otherwise be a build warning buried
    in a list of 68.
    """
    sources = json.loads((EDITION / "sources.json").read_text(encoding="utf-8"))
    policy = _manifest()["tiering"]
    for source in sources["documents"]:
        assert source.get("state") or source.get("jurisdiction"), (
            f"{source['doc_id']} has no state and no jurisdiction")
        assert tier_for(source, policy) in {"local", "regional", "national"}


def test_the_boolean_support_terms_are_declared_in_the_edition() -> None:
    """The words that make a boolean true are the edition's, not the core's.

    The core used to carry them, which meant the shared importer decided what
    every other edition's booleans meant.
    """
    terms = BOOLEAN_SUPPORT.get("dlsa_eligible", {}).get("true_terms", [])
    assert terms, "the edition must declare what supports a true dlsa_eligible"
    if not CORPUS.is_file():
        return
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    for document in payload["documents"]:
        field = document["fields"].get("dlsa_eligible")
        if not field:
            continue
        assert field.get("support_terms"), (
            f"{document['doc_id']} stores a boolean with no declared terms")


def test_a_quote_is_a_slice_of_the_stored_text() -> None:
    text = "Title: Example\n\nHeld that free legal aid is a right."
    span = find_span(text, "free   legal aid")
    assert span is not None
    assert text[span[0]:span[1]] == "free legal aid"
    assert quote_for(text, "not present") == ""


def test_compose_puts_every_header_value_somewhere_quoteable() -> None:
    document = {"doc_id": "x", "title": "Example", "doc_type": "judgment",
                "citation": "(2024) 1 SCC 1", "court": "Supreme Court of India",
                "jurisdiction": "India", "tier": "national", "year": 2024,
                "act": "Example Act", "section": ""}
    text, lines = compose(document, "Held that free legal aid is a right.")
    fields = fields_for(document, text, lines, "Held that free legal aid is a right.")

    assert lines["document_title"] == "Title: Example"
    assert lines["tier"] == "Tier: national"
    assert fields["year"]["value"] == 2024
    assert fields["court"]["quote"] == "Court: Supreme Court of India"
    assert fields["tier"]["quote"] == "Tier: national"
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


def test_every_bucket_rule_matches_something_in_the_corpus() -> None:
    """A bucket that can never match is a dead rule.

    The first Local rule looked for district-court wording in the `court`
    field. The corpus holds no district court, so Local was empty forever and
    the corpus silently had no local tier at all. A rule is only a view if it
    actually views something.
    """
    if not CORPUS.is_file():
        return
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    rules = {b["name"]: b for b in payload["buckets"]}
    assert set(rules) == {"local", "regional", "national"}
    # Every bucket reads the same derived field, so the tiers partition the
    # corpus instead of overlapping it.
    assert {r["field"] for r in rules.values()} == {"tier"}

    def matches(value, rule):
        if not value:
            return False
        text = value.lower()
        if text in {v.lower() for v in rule.get("values", [])}:
            return True
        return any(c.lower() in text for c in rule.get("contains", []))

    hits = {name: 0 for name in rules}
    for document in payload["documents"]:
        for name, rule in rules.items():
            value = document["fields"].get(rule["field"], {}).get("value", "")
            if matches(value, rule):
                hits[name] += 1
    for name, count in hits.items():
        assert count > 0, f"bucket {name!r} matches nothing in the corpus"

    # Every document is in at least one tier, so nothing is lost.
    covered = 0
    for document in payload["documents"]:
        if any(matches(document["fields"].get(r["field"], {}).get("value", ""), r)
               for r in rules.values()):
            covered += 1
    assert covered == len(payload["documents"])


def test_no_judgment_summary_starts_at_the_caption() -> None:
    """A judgment's summary must be the court's reasoning, not its front page.

    Every judgment PDF opens with a caption, a reporter citation, the counsel
    list and the bench. Taking the first paragraph of that block made every
    Supreme Court summary read "Page 1 / REPORTABLE / IN THE SUPREME COURT",
    which is a document header, not a holding.
    """
    if not CORPUS.is_file():
        return
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    caption = re.compile(
        r"^(IN THE|HIGH COURT|SUPREME COURT|Page\b|\[?\d{4}/|PETITIONER|"
        r"JUDGMENT|CITATION|BENCH|DATE OF|Reportable)", re.IGNORECASE)
    for document in payload["documents"]:
        if document.get("doc_type") != "judgment":
            continue
        summary = document["fields"]["summary"]["value"].strip()
        assert not caption.match(summary), (
            f"{document['doc_id']} summarizes its own caption: {summary[:80]!r}")


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
