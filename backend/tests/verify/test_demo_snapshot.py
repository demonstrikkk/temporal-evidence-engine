"""The demo snapshot must be a store a judge can inspect, not decoration.

Every assertion here is about something that could rot silently: an evidence row
whose offsets no longer resolve, a run left `running`, a claim with no verdict, an
answer whose timestamp was "refreshed" so the replay looks live. The snapshot is
committed, so these run in the ordinary gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parents[3] / "demo" / "state.json"

pytestmark = pytest.mark.skipif(not SNAPSHOT.is_file(),
                                reason="no demo snapshot committed")


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_snapshot_says_what_it_is(snapshot):
    meta = snapshot.get("demo_meta") or {}
    assert meta.get("frozen_at"), "a snapshot with no freeze time is undated"
    assert meta.get("runs"), "the snapshot should name the runs it contains"
    assert "not live" in (meta.get("note") or "").lower() or \
        "replay" in (meta.get("note") or "").lower()


def test_no_run_is_left_mid_flight(snapshot):
    statuses = {run["status"] for run in snapshot["runs"]}
    assert "running" not in statuses, \
        "a run frozen while running would show a spinner forever"
    assert "queued" not in statuses
    assert statuses <= {"completed", "partial"}, statuses


def test_every_claim_has_a_verdict_and_a_reason(snapshot):
    statuses = {"supported", "changed", "contradicted", "unverifiable"}
    for claim in snapshot["claims"]:
        assert claim["status"] in statuses, claim["status"]
        assert claim.get("reason_code"), \
            f"claim {claim['id']} has no reason code"


def test_every_evidence_quote_resolves_against_stored_text(snapshot):
    revisions = {row["id"]: row for row in snapshot["revisions"]}
    checked = 0
    for row in snapshot["evidence"]:
        revision = revisions.get(row["source_revision_id"])
        assert revision is not None, \
            f"evidence {row['id']} cites a revision that is not in the snapshot"
        content = str(revision.get("content") or "")
        start, end = row["start_char"], row["end_char"]
        assert isinstance(start, int) and isinstance(end, int)
        assert 0 <= start < end <= len(content), row["id"]
        assert content[start:end] == row["quote_text"], \
            f"evidence {row['id']} does not resolve at its offsets"
        checked += 1
    assert checked > 0, "a snapshot with no evidence shows nothing"


def test_event_dates_are_not_copied_from_publication_dates(snapshot):
    revisions = {row["id"]: row for row in snapshot["revisions"]}
    for row in snapshot["evidence"]:
        if row.get("event_date"):
            revision = revisions[row["source_revision_id"]]
            assert str(row["event_date"]) != str(revision.get("retrieved_at")), \
                "an event date equal to the retrieval time is a merged timestamp"


def test_the_answers_keep_the_time_they_were_generated(snapshot):
    for answer in snapshot["answers"]:
        assert answer.get("generated_at"), \
            f"answer {answer['id']} has no generated_at"
        assert answer.get("generated_at") != answer.get("submitted_at"), \
            "generated_at and submitted_at are different clocks"


def test_the_recording_is_complete_enough_to_replay(snapshot):
    """A replay needs the model calls and the search cache. L2/L5 must be gone."""
    assert "cache_entries" not in snapshot, \
        "shipping L2/L5 entries would replay verdicts instead of recomputing them"
    assert snapshot["calls"], "no recorded model calls; a replay would fail"
    assert snapshot["cache"], "no recorded search responses; a replay would fail"


def test_no_provider_key_is_stored_in_the_snapshot(snapshot):
    blob = SNAPSHOT.read_text(encoding="utf-8")
    for marker in ("tvly-", "gsk_", "sk-bf-", "sk-proj-"):
        assert marker not in blob, f"{marker} looks like a credential in a demo"
