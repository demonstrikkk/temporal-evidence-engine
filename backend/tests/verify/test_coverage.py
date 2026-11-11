"""Coverage: the answer's assertions, and the ones no claim touched.

The Eiffel failure was not a bad verdict. It was two claims extracted from a
paragraph of seven assertions, with nothing anywhere saying five were skipped.
These tests pin the deterministic half (segmentation, candidate finding,
dispositions) and the product half (the audit is stored, the repair is capped,
and a gap is reported rather than rounded away).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.verify.assertions import candidates, disposition_for
from app.verify.coverage import audit
from app.verify.orchestrator import Verifier, create_run
from app.verify.report import build_report
from app.verify.schemas import AnswerIn
from app.verify.segment import segment
from app.verify.value_normalizer import normalize_value

GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# sentence segmentation
# ---------------------------------------------------------------------------

def test_a_decimal_is_not_a_sentence_boundary():
    sentences = segment("Pi is 3.14 exactly. That is all.")
    assert [s.text for s in sentences] == ["Pi is 3.14 exactly.", "That is all."]


def test_an_abbreviation_is_not_a_sentence_boundary():
    sentences = segment("Dr. Smith arrived at 9 a.m. today. He left later.")
    assert [s.text for s in sentences] == [
        "Dr. Smith arrived at 9 a.m. today.", "He left later."]


def test_a_version_number_is_not_a_sentence_boundary():
    sentences = segment("Use v1.2.3 now. Then stop.")
    assert [s.text for s in sentences] == ["Use v1.2.3 now.", "Then stop."]


def test_a_blank_line_starts_a_new_sentence():
    sentences = segment("First line\n\nSecond line")
    assert [s.text for s in sentences] == ["First line", "Second line"]


def test_a_bullet_starts_a_new_sentence():
    sentences = segment("Intro\n- 330 metres tall\n- Built in 1889")
    assert [s.text for s in sentences] == ["Intro", "- 330 metres tall",
                                           "- Built in 1889"]


def test_offsets_are_absolute_and_no_character_is_lost():
    text = "Dr. Smith paid $3.50 for v1.2.3.\n\nHe said \"Go.\" Then left."
    sentences = segment(text)
    assert all(text[s.start:s.end] == s.text for s in sentences)
    covered = set()
    for sentence in sentences:
        covered.update(range(sentence.start, sentence.end))
    missing = [index for index, char in enumerate(text)
               if not char.isspace() and index not in covered]
    assert missing == [], f"characters {missing} belong to no sentence"


# ---------------------------------------------------------------------------
# candidate finding
# ---------------------------------------------------------------------------

def test_a_date_containing_a_year_is_one_candidate():
    found = candidates("Founded in January 1887 at a height of 330 metres.")
    texts = [(c.kind, c.text) for c in found]
    assert ("date", "January 1887") in texts
    assert all(c.text != "1887" for c in found), \
        "the year inside a date must not become a second assertion"
    assert ("measure", "330 metres") in texts


def test_money_wins_over_a_bare_number():
    found = candidates("The plan costs $129 per month.")
    assert [(c.kind, c.text) for c in found][0][0] == "money"
    assert all(c.kind != "number" for c in found)


def test_no_two_candidates_overlap():
    found = candidates("Between 2019-2020 the price rose 12.5% to $1,299.")
    spans = sorted((c.start, c.end) for c in found)
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        assert first_end <= second_start, spans


def test_an_opinion_is_excused_but_never_checked():
    assert disposition_for("The 330-metre tower is beautiful.") == "subjective"


def test_a_prediction_is_excused_but_never_checked():
    assert disposition_for("The tower will be 400 metres tall by 2030.") == \
        "prediction"


def test_a_plain_uncovered_fact_is_admitted():
    assert disposition_for("The tower was completed in 1889.") == "not_checked"


def test_a_checked_value_is_never_excused():
    assert disposition_for("The tower will be beautiful.", covered=True) == \
        "checked"


# ---------------------------------------------------------------------------
# the audit itself
# ---------------------------------------------------------------------------

EIFFEL = (
    "The Eiffel Tower is 330 metres tall and was completed in 1889. "
    "It was the tallest structure in the world until 1930. "
    "Visitors paid about 29 euros to reach the top in 2024. "
    "It remains a beloved symbol of Paris."
)


def _claim(claim_text, answer, *, object_raw="", claim_type="factual",
           volatility="moderate", subject="Eiffel Tower"):
    start = answer.index(claim_text)
    return {
        "id": f"claim-{start}",
        "claim_text": claim_text,
        "span_start": start,
        "span_end": start + len(claim_text),
        "subject": subject,
        "predicate": "height",
        "object_raw": object_raw or claim_text,
        "object_normalized": normalize_value(object_raw or claim_text),
        "claim_type": claim_type,
        "volatility_class": volatility,
    }


def test_two_claims_from_seven_assertions_are_reported_as_a_gap():
    claims = [_claim("330 metres tall", EIFFEL, object_raw="330 metres"),
              _claim("completed in 1889", EIFFEL, object_raw="1889")]
    report = audit(EIFFEL, claims)

    assert report.claims == 2
    assert report.candidates >= 3, report.items
    assert report.checked == 2
    assert report.coverage < 1.0
    assert report.uncovered >= 1
    uncovered_text = [item.text for item in report.items
                      if item.disposition == "not_checked"]
    assert any("29" in text for text in uncovered_text), uncovered_text
    assert report.repair_targets, "an uncovered value must produce a target"


def test_a_claim_covering_the_whole_sentence_counts_as_an_assertion():
    answer = "The tower is in Paris."
    claim = _claim(answer, answer, object_raw="Paris")
    report = audit(answer, [claim])

    assert report.candidates == 0
    assert report.claims_without_candidate == 1
    assert report.total_assertions == 1
    assert report.checked == 1
    assert report.coverage == 1.0


def test_a_subjective_assertion_does_not_lower_material_coverage():
    answer = "The 330-metre tower is beautiful."
    report = audit(answer, [])
    assert report.dispositions.get("subjective") == 1
    assert report.material_assertions == 0
    assert report.material_coverage == 1.0


def test_coverage_never_writes_a_claim_status():
    """Coverage is a statement about extraction, not a verdict about a fact."""
    answer = "The tower is 330 metres tall. It is beautiful."
    claim = _claim("The tower is 330 metres tall", answer)
    report = audit(answer, [claim])
    for item in report.items:
        assert item.disposition in {"checked", "subjective", "prediction",
                                    "not_checked"}
    # The audit returns dispositions; it has no status field to write.
    assert not hasattr(report, "status")


# ---------------------------------------------------------------------------
# the run: audit, capped repair, durable record
# ---------------------------------------------------------------------------

ANSWER = ("Acme Pro costs $99 per month. "
          "Acme was founded in 2015 and is based in Berlin.")


def _start(store, answer=ANSWER):
    return create_run(store, AnswerIn(
        answer=answer, prompt="What does Acme Pro cost?",
        generated_at=GEN, generated_at_source="user_supplied"))


def _pricing_claim():
    return {
        "claim_text": "Acme Pro costs $99 per month",
        "subject": "Acme Pro", "predicate": "price",
        "object_raw": "$99/month", "claim_type": "time_sensitive",
        "time_sensitive": True, "volatility_class": "high",
        "extraction_confidence": 0.9,
    }


def _founded_claim():
    return {
        "claim_text": "Acme was founded in 2015",
        "subject": "Acme", "predicate": "founded",
        "object_raw": "2015", "claim_type": "stable_factual",
        "time_sensitive": False, "volatility_class": "stable",
        "extraction_confidence": 0.9,
    }


def test_a_run_records_coverage_before_it_researches_anything(
        store, make_gateway, make_tavily):
    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    audit_row = store.get_coverage_audit(run.run_id)
    assert audit_row is not None
    assert audit_row["total_assertions"] >= 2
    assert audit_row["checked"] == 1
    spans = store.list_assertion_spans(run.run_id)
    assert spans, "every assertion gets a durable row"
    assert {row["disposition"] for row in spans} <= {
        "checked", "subjective", "prediction", "not_checked"}

    kinds = [event["event_type"] for event in store.list_events(run.run_id)]
    assert "coverage.audited" in kinds
    assert "coverage.recorded" in kinds


def test_one_capped_repair_recovers_an_uncovered_assertion(
        store, make_gateway, make_tavily):
    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()],
                      repair_claims=[_founded_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    assert gw.calls == ["extract_claims", "repair_coverage"], \
        "coverage repair is exactly one additional call"

    claims = store.list_claims(run.run_id)
    assert len(claims) == 2
    recovered = [row for row in claims
                 if row.get("extracted_by") == "coverage_repair"]
    assert len(recovered) == 1
    assert recovered[0]["claim_text"] == "Acme was founded in 2015"
    # A recovered claim is verified like any other; it is not a special case.
    assert recovered[0]["status"] in {"supported", "changed", "contradicted",
                                      "unverifiable"}

    audit_row = store.get_coverage_audit(run.run_id)
    assert audit_row["repair_calls"] == 1
    assert audit_row["repaired_claims"] == 1
    assert audit_row["coverage"] == 1.0, audit_row

    span = next(row for row in store.list_assertion_spans(run.run_id)
                if "2015" in str(row.get("text")))
    assert span["disposition"] == "checked"
    assert span["claim_id"] == recovered[0]["id"]

    events = [event["event_type"] for event in store.list_events(run.run_id)]
    assert "claim.recovered" in events
    assert "coverage.repaired" in events


def test_repair_can_be_disabled_and_the_gap_is_still_reported(
        store, make_gateway, make_tavily, monkeypatch):
    monkeypatch.setattr(settings, "COVERAGE_MAX_REPAIR_CALLS", 0)
    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()],
                      repair_claims=[_founded_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    assert gw.calls == ["extract_claims"], "0 means no repair call at all"
    audit_row = store.get_coverage_audit(run.run_id)
    assert audit_row["repair_calls"] == 0
    assert audit_row["dispositions"]["not_checked"] >= 1
    assert audit_row["coverage"] < 1.0
    assert len(store.list_claims(run.run_id)) == 1


def test_a_failed_repair_is_an_event_not_a_better_number(
        store, make_gateway, make_tavily):
    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()],
                      fail_stage="repair_coverage")
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    run_row = store.get_run(run.run_id)
    assert run_row["status"] in {"completed", "partial"}, \
        "a repair failure must not fail the run"
    events = {(event["event_type"], event["payload"].get("kind"))
              for event in store.list_events(run.run_id)}
    assert ("coverage.repair_failed", "fatal") in events
    audit_row = store.get_coverage_audit(run.run_id)
    assert audit_row["repair_calls"] == 1
    assert audit_row["repaired_claims"] == 0
    assert audit_row["coverage"] < 1.0


def test_the_repair_cannot_duplicate_or_expand_a_claim(
        store, make_gateway, make_tavily):
    run = _start(store)
    duplicate = dict(_pricing_claim())
    gw = make_gateway(claims=[_pricing_claim()],
                      repair_claims=[duplicate, _founded_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    claims = store.list_claims(run.run_id)
    texts = [row["claim_text"] for row in claims]
    assert texts.count("Acme Pro costs $99 per month") == 1, texts
    assert "Acme was founded in 2015" in texts


def test_offline_mode_does_not_spend_a_repair_call(
        store, make_gateway, make_tavily):
    run = create_run(store, AnswerIn(
        answer=ANSWER, prompt="What does Acme Pro cost?",
        generated_at=GEN, generated_at_source="user_supplied",
        budget_mode="offline"))
    gw = make_gateway(claims=[_pricing_claim()],
                      repair_claims=[_founded_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    assert "repair_coverage" not in gw.calls
    audit_row = store.get_coverage_audit(run.run_id)
    assert audit_row["repair_calls"] == 0


# ---------------------------------------------------------------------------
# the export
# ---------------------------------------------------------------------------

def test_the_report_and_markdown_show_the_gap(
        store, make_gateway, make_tavily):
    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    report = build_report(store, run.run_id)
    assert report["coverage"]["total_assertions"] >= 2
    assert report["coverage"]["coverage"] < 1.0
    assert report["coverage"]["spans"]

    markdown = report["markdown"]
    assert "## Coverage" in markdown
    assert "Not checked" in markdown


def test_the_coverage_endpoint_returns_the_audit_and_every_span(
        store, make_gateway, make_tavily, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.verify as verify_api

    run = _start(store)
    gw = make_gateway(claims=[_pricing_claim()])
    Verifier(store, run.run_id, bifrost=gw, tavily=make_tavily()).execute()

    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED", True)
    monkeypatch.setattr(verify_api, "_store", lambda: store)
    client = TestClient(app)
    response = client.get(f"/api/verify/runs/{run.run_id}/coverage")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audit"]["total_assertions"] >= 2
    assert body["assertions"]
    assert "not a verdict" in body["note"]
