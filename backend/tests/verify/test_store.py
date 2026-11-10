"""The store's guarantees, tested against the adapter that needs no database.

These properties are asserted for `LocalStore`; the Postgres adapter implements
the same protocol and is covered by the `postgres` marker when a DSN is present.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.verify.store import LocalStore, iso, new_id


def test_events_receive_a_contiguous_sequence_per_run(store):
    first = store.append_event("run-a", "run.started", {"x": 1})
    second = store.append_event("run-a", "claim.started", {"x": 2})
    other = store.append_event("run-b", "run.started", {"x": 3})
    assert (first["sequence"], second["sequence"], other["sequence"]) == (1, 2, 1)
    assert [e["sequence"] for e in store.list_events("run-a")] == [1, 2]
    assert [e["sequence"] for e in store.list_events("run-a", after=1)] == [2]


def test_model_calls_are_listed_newest_first_without_their_prompts(store):
    """The panel shows routes and costs, not the prompt or the raw response.

    A call row carries the prompt hash and the full response body; neither
    belongs on a page a reviewer screenshots. The projection is asserted here so
    a future `SELECT *` does not quietly publish the model's context.
    """
    for index, (run_id, when) in enumerate([
        ("run-a", "2026-11-01T10:00:00+00:00"),
        ("run-b", "2026-11-01T10:05:00+00:00"),
        ("run-a", "2026-11-01T10:10:00+00:00"),
    ]):
        store.add_model_call({
            "id": f"c{index}", "run_id": run_id, "stage": "propose_relationship",
            "provider": "groq", "model": "openai/gpt-oss-20b",
            "bifrost_request_id": f"req-{index}", "prompt_hash": f"h{index}",
            "response_json": {"text": "secret prompt echo"},
            "input_tokens": 10, "output_tokens": 2, "cost_usd": 0.001,
            "latency_ms": 40, "fallback_attempts": 0, "status": "ok",
            "cacheable": True, "created_at": when,
        })

    rows = store.list_model_calls(10)
    assert [row["bifrost_request_id"] for row in rows] == ["req-2", "req-1", "req-0"]
    assert "prompt_hash" not in rows[0]
    assert "response_json" not in rows[0]
    assert set(rows[0]) >= {"stage", "provider", "model", "cost_usd", "status"}

    only_a = store.list_model_calls(10, "run-a")
    assert [row["bifrost_request_id"] for row in only_a] == ["req-2", "req-0"]
    assert len(store.list_model_calls(1)) == 1


def test_a_failed_model_call_does_not_poison_the_cache_key(store):
    key = {"prompt_hash": "h1", "model": "m", "stage": "extract_claims"}
    store.add_model_call({**key, "id": new_id(), "status": "error",
                          "cacheable": False, "response_json": {"error": "boom"},
                          "created_at": iso(datetime.now(timezone.utc))})
    assert store.find_model_call("h1", "m", "extract_claims") is None

    store.add_model_call({**key, "id": new_id(), "status": "ok",
                          "cacheable": True, "response_json": {"text": "{}"},
                          "created_at": iso(datetime.now(timezone.utc))})
    found = store.find_model_call("h1", "m", "extract_claims")
    assert found and found["status"] == "ok"


def test_a_second_successful_call_reuses_the_first_cache_row(store):
    base = {"prompt_hash": "h2", "model": "m", "stage": "propose_relationship",
            "status": "ok", "cacheable": True,
            "created_at": iso(datetime.now(timezone.utc))}
    first = store.add_model_call({**base, "id": "one",
                                  "response_json": {"text": "first"}})
    second = store.add_model_call({**base, "id": "two",
                                   "response_json": {"text": "second"}})
    assert first["id"] == second["id"] == "one"


def test_due_claims_are_ordered_by_next_check(store):
    early = iso(datetime.now(timezone.utc) - timedelta(hours=1))
    late = iso(datetime.now(timezone.utc) + timedelta(hours=1))
    store.add_claim({"id": "c1", "run_id": "r", "ordinal": 0,
                     "next_check_at": late, "claim_text": "late"})
    store.add_claim({"id": "c2", "run_id": "r", "ordinal": 1,
                     "next_check_at": early, "claim_text": "early"})
    store.add_claim({"id": "c3", "run_id": "r", "ordinal": 2,
                     "next_check_at": None, "claim_text": "never"})
    assert [row["id"] for row in store.list_due()] == ["c2", "c1"]


def test_sources_are_unique_by_canonical_url(store):
    one = store.add_source({"id": "s1", "canonical_url": "https://a.test/x",
                            "domain": "a.test", "title": "A",
                            "first_seen_at": None})
    two = store.add_source({"id": "s2", "canonical_url": "https://a.test/x",
                            "domain": "a.test", "title": "A again",
                            "first_seen_at": None})
    assert one["id"] == two["id"] == "s1"


def test_the_local_store_survives_a_restart(tmp_path):
    directory = str(tmp_path / "state")
    first = LocalStore(directory)
    first.append_event("run", "run.started", {"kept": True})
    second = LocalStore(directory)
    assert second.list_events("run")[0]["payload"] == {"kept": True}


def test_a_locked_state_file_is_retried_not_reported_as_a_claim_failure(
        tmp_path, monkeypatch):
    """Windows refuses `os.replace` while another handle holds the target.

    That is a virus scanner, not a bug in the run, but the exception used to
    escape `update_claim` and be caught by the orchestrator as
    `extraction_failed` — a claim whose extraction had worked perfectly was
    marked unverifiable for a transient file lock. The write is retried; the
    claim keeps its real verdict.
    """
    import os as os_mod

    from app.verify import store as store_mod

    real_replace = os_mod.replace
    attempts = {"count": 0}

    def flaky_replace(src, dst, *args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise PermissionError(13, "Access is denied", str(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(store_mod.os, "replace", flaky_replace)
    store = LocalStore(str(tmp_path / "state"))
    store.add_claim({"id": "c1", "run_id": "r", "ordinal": 0,
                     "claim_text": "Acme Pro costs $99 per month"})

    assert attempts["count"] == 3, "the write must be retried, not abandoned"
    assert LocalStore(str(tmp_path / "state")).list_claims("r")[0]["id"] == "c1"


def test_a_state_file_that_stays_locked_does_not_take_the_run_down(
        tmp_path, monkeypatch):
    """Persistent failure is logged, not raised: memory is still the truth."""
    from app.verify import store as store_mod

    def always_locked(*_args, **_kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(store_mod.os, "replace", always_locked)
    store = LocalStore(str(tmp_path / "state"))
    store.add_claim({"id": "c1", "run_id": "r", "ordinal": 0,
                     "claim_text": "still here"})

    assert store.list_claims("r")[0]["id"] == "c1"
    # Nothing half-written is left behind under a stale temp name.
    leftovers = [p.name for p in (tmp_path / "state").iterdir()
                 if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers
