"""The coverage disclosure, read back from a real Postgres.

`test_coverage.py` proves the audit computes `unscanned`, and
`test_pg_store.py` proves the INSERT carries the columns. Neither executes the
DDL or the SQL. This does, and it compares the result with the local store for
the same row, so "Local and Postgres return the same field" is a measurement
rather than an intention.

Skipped unless `DATABASE_URL` is exported in the process environment (see
`backend/pyproject.toml`); `.env` is deliberately ignored. The migrations are
idempotent, so the test applies them rather than assuming a prepared database.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.verify.store import PgStore, now

pytestmark = pytest.mark.postgres

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _migrate(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))


def _audit_row(run_id: str, answer_id: str) -> dict:
    stamp = now().isoformat()
    return {
        "run_id": run_id, "answer_id": answer_id, "answer_chars": 74,
        "sentences": 2, "candidates": 1, "claims": 1,
        "claims_without_candidate": 0, "total_assertions": 1, "checked": 1,
        "coverage": 1.0, "material_assertions": 1, "material_coverage": 1.0,
        "dispositions": {"checked": 1}, "unscanned": 1,
        "unscanned_sentences": [{
            "start": 31, "end": 74,
            "text": "Acme Pro is faster than Nimbus Pro.",
            "signals": {"comparative": True},
        }],
        "repair_calls": 0, "repaired_claims": 0, "repair_targets": [],
        "created_at": stamp, "updated_at": stamp,
    }


def test_the_disclosure_round_trips_and_matches_the_local_store(store):
    if not settings.DATABASE_URL:
        pytest.skip("no DATABASE_URL; the stored columns cannot be checked "
                    "without a database")
    _migrate(settings.DATABASE_URL)
    pg = PgStore(settings.DATABASE_URL)

    run_id = f"parity-{uuid.uuid4()}"
    answer_id = f"parity-answer-{uuid.uuid4()}"
    pg.create_answer({
        "id": answer_id, "prompt": "Is Acme faster?",
        "answer_text": ("Acme Pro costs $99 per month. "
                        "Acme Pro is faster than Nimbus Pro."),
        "generated_at": None, "generated_at_source": "user_supplied",
        "submitted_at": now(), "locale": "en", "source_urls": [],
        "domain_filter": [], "skip_non_factual": True,
    })
    pg.create_run({
        "id": run_id, "answer_id": answer_id, "status": "completed",
        "stage": "done", "rule_version": "tev-1.2.0",
        "cache_mode": "read-write", "budget_mode": "balanced",
        "started_at": now(),
    })

    row = _audit_row(run_id, answer_id)
    store.put_coverage_audit(dict(row))
    pg.put_coverage_audit(dict(row))

    local = store.get_coverage_audit(run_id)
    remote = pg.get_coverage_audit(run_id)
    assert remote is not None, "the parity row was not written"

    for field in ("unscanned", "sentences", "checked", "coverage",
                  "material_coverage"):
        assert remote[field] == local[field], field
    assert remote["unscanned"] == 1
    assert remote["unscanned_sentences"] == local["unscanned_sentences"]

    # Re-writing the row without the disclosure must store 0 and [], which is
    # what a row written before this column existed reads as. NULL would make
    # the reader handle a case the schema says cannot happen.
    pg.put_coverage_audit(_audit_row(run_id, answer_id) | {
        "unscanned": 0, "unscanned_sentences": []})
    reread = pg.get_coverage_audit(run_id)
    assert reread["unscanned"] == 0
    assert reread["unscanned_sentences"] == []
