"""The Postgres adapter that no local test could reach.

`PgStore._one/_all/_write` used to call `tuple(params)` before handing the
parameters to psycopg. Every statement written with named placeholders
(`%(id)s`) therefore received a tuple of *keys*, and psycopg raised

    TypeError: named placeholders require a mapping of parameters

on the first write - `POST /api/verify/runs` answered 500 against a real
PostgreSQL while the entire suite was green, because the local store has no SQL
and no placeholders.

These tests drive the real method bodies against a cursor that enforces the same
rule psycopg enforces. They are hermetic: no server, no DSN, no network. A live
Postgres run is still the only proof that the SQL itself is valid, and that is
recorded in docs/BUILD-PLAN.md rather than claimed here.
"""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.verify.store import PgStore

#: A row that satisfies every INSERT the adapter makes. The cursor ignores the
#: values; what matters is the *shape* of the parameter container.
ROW = {
    "id": "00000000-0000-4000-8000-000000000001",
    "answer_id": "00000000-0000-4000-8000-000000000002",
    "run_id": "00000000-0000-4000-8000-000000000003",
    "claim_id": "00000000-0000-4000-8000-000000000004",
    "evidence_id": "00000000-0000-4000-8000-000000000005",
    "source_id": "00000000-0000-4000-8000-000000000006",
    "source_revision_id": "00000000-0000-4000-8000-000000000007",
    "ordinal": 0,
    "prompt": "",
    "answer_text": "",
    "generated_at": None,
    "generated_at_source": "user_supplied",
    "submitted_at": "2026-01-01T00:00:00Z",
    "locale": "en",
    "source_urls": [],
    "domain_filter": [],
    "skip_non_factual": True,
    "status": "queued",
    "stage": "queued",
    "rule_version": "tev-1.2.0",
    "cache_mode": "read-write",
    "started_at": "2026-01-01T00:00:00Z",
    "claim_text": "x",
    "span_start": 0,
    "span_end": 1,
    "subject": "s",
    "predicate": "p",
    "object_raw": "o",
    "object_normalized": {},
    "qualifiers": {},
    "claim_type": "factual",
    "time_sensitive": False,
    "volatility_class": "stable",
    "temporal_scope": {},
    "extraction_confidence": 0.9,
    "reason_code": None,
    "canonical_url": "https://example.test/a",
    "domain": "example.test",
    "title": "t",
    "first_seen_at": "2026-01-01T00:00:00Z",
    "content_hash": "h",
    "content": "c",
    "published_date": None,
    "published_date_source": "unknown",
    "retrieved_at": "2026-01-01T00:00:00Z",
    "extraction_method": "tavily",
    "tavily_request_id": "r",
    "fetch_outcome": {},
    "quote_text": "q",
    "start_char": 0,
    "end_char": 1,
    "event_date": None,
    "event_date_quote": None,
    "event_date_status": "missing",
    "relationship_proposed": "supports",
    "relationship_settled": "supports",
    "settlement_status": "accepted",
    "settlement_reason": "",
    "settlement_start": 0,
    "settlement_end": 1,
    "candidate_value_raw": "",
    "candidate_value_normalized": {},
    "model_call_id": None,
    "created_at": "2026-01-01T00:00:00Z",
    "value_raw": "v",
    "value_normalized": {},
    "valid_from": None,
    "valid_until": None,
    "is_original": True,
    "previous_status": "pending",
    "new_status": "supported",
    "decided_at": "2026-01-01T00:00:00Z",
    "stage_name": "extract_claims",
    "provider": "groq",
    "model": "m",
    "resolved_model": "",
    "bifrost_request_id": "r",
    "prompt_hash": "ph",
    "response_json": {},
    "input_tokens": 1,
    "output_tokens": 1,
    "cost_usd": 0.0,
    "latency_ms": 1,
    "fallback_attempts": 0,
    "cacheable": True,
}


class _Cursor:
    """Enforces psycopg's placeholder rule, then pretends the row was written."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, object]] = []
        self.description = [("id",)]
        self._last: str = ""

    def execute(self, sql: str, params: object = None) -> "_Cursor":
        if "%(" in sql and not isinstance(params, Mapping):
            raise TypeError("named placeholders require a mapping of parameters")
        if "%s" in sql and isinstance(params, Mapping):
            raise TypeError("positional placeholders cannot take a mapping")
        self.seen.append((sql, params))
        self._last = sql
        return self

    def fetchone(self):
        return dict(ROW)

    def fetchall(self):
        return [dict(ROW)]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def pg(monkeypatch):
    store = PgStore("postgresql://ignored/ignored")
    cursor = _Cursor()
    monkeypatch.setattr(store, "_conn", lambda: _Connection(cursor))
    return store, cursor


def test_every_named_insert_passes_a_mapping(pg):
    """The exact call that answered 500 on a real PostgreSQL."""
    store, cursor = pg

    store.create_answer(dict(ROW))
    store.create_run(dict(ROW))
    store.add_claim(dict(ROW))
    store.add_source(dict(ROW))
    store.add_revision(dict(ROW))
    store.add_evidence(dict(ROW))
    store.add_link(dict(ROW))
    store.add_assertion(dict(ROW))
    store.add_history(dict(ROW))
    store.add_model_call(dict(ROW))
    store.append_event(ROW["run_id"], "run.started", {"a": 1})
    store.update_run(ROW["run_id"], status="running")
    store.update_claim(ROW["claim_id"], status="supported")

    assert cursor.seen, "the fake cursor was never asked to execute anything"


def test_positional_reads_still_pass_a_tuple(pg):
    store, cursor = pg

    store.get_answer(ROW["answer_id"])
    store.list_claims(ROW["run_id"])
    store.list_events(ROW["run_id"])
    store.gateway_stats(ROW["run_id"])

    assert all(not isinstance(params, Mapping) for _sql, params in cursor.seen)


def test_coverage_audit_carries_the_unscanned_columns_as_a_mapping(pg):
    """The disclosure is stored, not only rendered.

    A column added to the INSERT but not to the parameter mapping is a
    KeyError; a parameter without a column is a silent drop. Both are checked
    here without a server.
    """
    store, cursor = pg

    def row(**overrides):
        base = {
            "run_id": ROW["run_id"], "answer_id": ROW["answer_id"],
            "answer_chars": 40, "sentences": 2, "candidates": 0, "claims": 0,
            "claims_without_candidate": 0, "total_assertions": 0, "checked": 0,
            "coverage": 1.0, "material_assertions": 0, "material_coverage": 1.0,
            "dispositions": {}, "unscanned": 2,
            "unscanned_sentences": [{"start": 0, "end": 41, "text": "x",
                                     "signals": {}}],
            "repair_calls": 0, "repaired_claims": 0, "repair_targets": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        base.update(overrides)
        return base

    store.put_coverage_audit(row())

    sql, params = cursor.seen[-1]
    assert isinstance(params, Mapping)
    for column in ("unscanned", "unscanned_sentences"):
        assert f"{column}," in sql or f"{column})" in sql
        assert f"%({column})s" in sql
        assert column in params
    assert params["unscanned"] == 2
    # A row written before the disclosure existed reads as 0, not NULL.
    store.put_coverage_audit(row(unscanned=None))
    assert cursor.seen[-1][1]["unscanned"] == 0




def test_an_event_payload_with_a_datetime_survives_a_jsonb_round_trip():
    """`claims.extracted` carries the model receipt, which carries `created_at`.

    psycopg's default JSON dumper raised `Object of type datetime is not JSON
    serializable`, the orchestrator logged `could not append run event`, and the
    run lost that event on Postgres only. The payload is now encodable, and the
    receipt uses ISO text so both stores record the same shape.
    """
    import json
    from datetime import date, datetime, timezone
    from decimal import Decimal

    from psycopg.types.json import Jsonb

    from app.verify.store import _jsonb

    dumped = _jsonb({
        "created_at": datetime(2026, 11, 10, 4, 26, 9, 278697, tzinfo=timezone.utc),
        "event_date": date(2013, 1, 1),
        "cost_usd": Decimal("0.0000"),
    })
    assert isinstance(dumped, Jsonb)
    payload = json.loads(dumped.dumps(dumped.obj))
    assert payload == {"created_at": "2026-11-10T04:26:09.278697+00:00",
                       "event_date": "2013-01-01", "cost_usd": 0.0}
