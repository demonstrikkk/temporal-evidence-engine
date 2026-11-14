"""The compare matrix: what it measures, and what it refuses to pretend.

The page exists because "which model gave the better answer" is a question a
reader will ask, and a made-up score would answer it. These tests pin the two
things that keep the answer honest:

* an unpriced call is not a free call (`priced_calls` is reported beside cost);
* a verdict is attributed only through the answer's recorded model, and an
  answer with no attribution lands in `unknown` rather than on the nearest
  model's rate.

Everything is local: `LocalStore` under `tmp_path` and a stubbed store for the
endpoint. No Postgres, no provider, no network.
"""
from __future__ import annotations

import pytest

from app.verify.store import LocalStore, iso, now

pytestmark = pytest.mark.hermetic


def _call(model: str, **overrides) -> dict:
    row = {
        "id": f"call-{overrides.get('n', 1)}",
        "run_id": None,
        "claim_id": None,
        "stage": "extract_claims",
        "provider": "mistral",
        "model": f"mistral/{model}",
        "resolved_model": model,
        "bifrost_request_id": "",
        "prompt_hash": f"hash-{overrides.get('n', 1)}",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0002,
        "cost_source": "gateway",
        "latency_ms": 900,
        "fallback_attempts": 0,
        "status": "ok",
        "cacheable": False,
        "created_at": iso(now()),
    }
    row.update(overrides)
    return row


def _answer(store: LocalStore, answer_id: str, model: str, provider: str = "mistral") -> None:
    store.create_answer({
        "id": answer_id,
        "prompt": "q",
        "answer_text": "a",
        "generated_at": None,
        "generated_at_source": "unknown",
        "submitted_at": iso(now()),
        "locale": "en",
        "source_urls": [],
        "domain_filter": [],
        "skip_non_factual": True,
        "model": model,
        "provider": provider,
    })


def _claim(store: LocalStore, claim_id: str, answer_id: str, status: str,
           **overrides) -> None:
    store.add_claim({
        "id": claim_id,
        "answer_id": answer_id,
        "run_id": f"run-{answer_id}",
        "ordinal": 0,
        "claim_text": "x",
        "span_start": 0,
        "span_end": 1,
        "status": status,
        "reason_code": None,
        "time_sensitive": False,
        "next_check_at": None,
        "last_verified_at": iso(now()),
        **overrides,
    })


def test_calls_are_grouped_by_the_model_that_answered(store):
    store.add_model_call(_call("mistral-small-latest", n=1))
    store.add_model_call(_call("mistral-small-latest", n=2,
                               input_tokens=300, output_tokens=150,
                               cost_usd=0.0004, latency_ms=1100))
    store.add_model_call(_call("llama-3.3-70b", n=3, provider="groq",
                               input_tokens=10, output_tokens=5,
                               cost_usd=0.0, cost_source="none",
                               latency_ms=0))

    rows = {row["model"]: row for row in store.model_call_stats()}

    small = rows["mistral-small-latest"]
    assert small["calls"] == 2
    assert small["input_tokens"] == 400
    assert small["output_tokens"] == 200
    assert small["cost_usd"] == pytest.approx(0.0006)
    assert small["priced_calls"] == 2
    assert small["avg_cost_usd"] == pytest.approx(0.0003)
    assert small["avg_latency_ms"] == 1000
    assert small["error_rate"] == 0
    assert small["providers"] == ["mistral"]

    # The unpriced call is counted and has no average cost — the difference
    # between "we were not told" and "it was free".
    llama = rows["llama-3.3-70b"]
    assert llama["calls"] == 1
    assert llama["priced_calls"] == 0
    assert llama["avg_cost_usd"] is None
    assert llama["timed_calls"] == 0
    assert llama["avg_latency_ms"] is None


def test_a_failed_call_counts_against_the_requested_model(store):
    store.add_model_call(_call("mistral-small-latest", n=1))
    store.add_model_call(_call("mistral-small-latest", n=2, resolved_model="",
                               status="error", provider="", cost_usd=0.0,
                               latency_ms=0))

    rows = {row["model"]: row for row in store.model_call_stats()}
    assert rows["mistral-small-latest"]["error_rate"] == 0.5
    assert rows["mistral-small-latest"]["error_calls"] == 1
    assert set(rows) == {"mistral-small-latest"}


def test_unrecorded_authors_are_a_pasted_group_not_a_model_named_unknown(store):
    _answer(store, "a1", "mistral-small-latest")
    _claim(store, "c1", "a1", "supported")
    _claim(store, "c2", "a1", "changed")
    _claim(store, "c3", "a1", "pending")

    _answer(store, "a2", "")
    _claim(store, "c4", "a2", "contradicted")

    rows = {row["model"]: row for row in store.model_verdict_stats()}

    small = rows["mistral-small-latest"]
    assert small["answers"] == 1
    assert small["claims"] == 3
    assert small["settled"] == 2
    assert small["supported"] == 1
    assert small["changed"] == 1
    assert small["pending"] == 1

    # No recorded model is the empty key — the answer was pasted, and the store
    # does not manufacture a model name for it or donate its claims elsewhere.
    assert "unknown" not in rows
    assert rows[""]["contradicted"] == 1
    assert rows[""]["answers"] == 1


def test_due_claims_are_counted_from_the_recheck_clock(store):
    _answer(store, "a1", "mistral-small-latest")
    _claim(store, "c1", "a1", "supported",
           next_check_at="2000-01-01T00:00:00+00:00", time_sensitive=True)
    _claim(store, "c2", "a1", "supported",
           next_check_at="2999-01-01T00:00:00+00:00", time_sensitive=True)
    _claim(store, "c3", "a1", "unverifiable")

    row = store.model_verdict_stats()[0]
    assert row["due"] == 1
    assert row["time_sensitive"] == 2


class _StubStore:
    adapter = "stub"

    def model_call_stats(self):
        return [
            {"model": "mistral-small-latest", "providers": ["mistral"],
             "calls": 2, "input_tokens": 400, "output_tokens": 200,
             "cost_usd": 0.0006, "priced_calls": 2, "avg_cost_usd": 0.0003,
             "timed_calls": 2, "avg_latency_ms": 1000, "error_calls": 0,
             "error_rate": 0.0, "stages": ["extract_claims"],
             "last_call_at": "2026-11-13T09:00:00+00:00"},
            {"model": "scanned-only", "providers": ["somevendor"],
             "calls": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0, "priced_calls": 0, "avg_cost_usd": None,
             "timed_calls": 0, "avg_latency_ms": None, "error_calls": 0,
             "error_rate": None, "stages": [], "last_call_at": None},
        ]

    def model_verdict_stats(self):
        return [
            {"model": "mistral-small-latest", "providers": ["mistral"],
             "answers": 1, "claims": 4, "settled": 4, "supported": 3,
             "changed": 1, "contradicted": 0, "unverifiable": 0, "pending": 0,
             "time_sensitive": 2, "due": 1,
             "last_verified_at": "2026-11-13T09:00:00+00:00"},
            {"model": "", "providers": [], "answers": 1, "claims": 1,
             "settled": 1, "supported": 0, "changed": 0, "contradicted": 1,
             "unverifiable": 0, "pending": 0, "time_sensitive": 0, "due": 0,
             "last_verified_at": None},
        ]


def test_the_endpoint_merges_registry_rows_without_inventing_measurements(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    from app.main import app
    import app.api.compare as compare_api

    monkeypatch.setattr(compare_api, "_store", lambda: _StubStore())
    response = TestClient(app).get("/api/compare/models")
    assert response.status_code == 200, response.text
    body = response.json()

    rows = {row["model"]: row for row in body["models"]}
    small = rows["mistral-small-latest"]
    assert small["origin"] == "measured"
    assert small["supported_rate"] == pytest.approx(0.75)
    assert small["changed_rate"] == pytest.approx(0.25)
    assert small["api"] == "Bifrost gateway"

    # A model with scans but no recorded calls still reports its verdicts and
    # does not pretend to have cost or latency.
    scanned = rows["scanned-only"]
    assert scanned["origin"] == "measured"
    assert scanned["answers"] == 0
    assert scanned["calls"] == 0
    assert scanned["avg_cost_usd"] is None

    # Registry rows carry vendor/transport and null measurements.
    registry = rows["big-pickle"]
    assert registry["origin"] == "registry"
    assert registry["vendor"] == "stealth"
    assert registry["transport"] == "opencode"
    assert registry["calls"] is None
    assert registry["avg_cost_usd"] is None
    assert registry["supported_rate"] is None

    # The unattributed answers are a named group with their verdicts, not a
    # model called "unknown" and not silently dropped from the totals.
    pasted = rows[""]
    assert pasted["origin"] == "pasted"
    assert pasted["api"] == "scanned via Bifrost"
    assert pasted["answers"] == 1
    assert pasted["contradicted"] == 1
    assert pasted["calls"] is None
    assert pasted["avg_cost_usd"] is None

    # Models, then the pasted group, then the catalogue. The first row is the
    # model with the most evidence, not a group and not the first alphabetically.
    origins = [row["origin"] for row in body["models"]]
    assert origins.index("pasted") > origins.count("measured") - 1
    assert origins.index("registry") > origins.index("pasted")
    assert body["models"][0]["model"] == "mistral-small-latest"

    totals = body["totals"]
    assert totals["measured_models"] == 2
    assert totals["pasted_answers"] == 1
    assert totals["calls"] == 2
    assert totals["claims"] == 5
    assert totals["supported"] == 3
    assert totals["contradicted"] == 1
    assert totals["supported_rate"] == pytest.approx(0.6)
    assert totals["avg_cost_usd"] == pytest.approx(0.0003)

    # The provenance notes travel with the numbers so the page cannot show the
    # matrix without the caveats that make it readable.
    assert set(body["notes"]) == {
        "calls", "cost", "verdicts", "attribution", "pasted", "registry",
    }
    assert body["generated_at"].endswith("Z")
