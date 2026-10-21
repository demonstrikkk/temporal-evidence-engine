"""Buckets group stored cells; they never invent a value."""
from __future__ import annotations

from app.services.buckets import derive


def _record(rid: str, **fields) -> dict:
    return {"record_id": rid,
            "fields": {name: {"value": value} for name, value in fields.items()}}


def test_exact_values_are_case_insensitive_and_record_ids_are_returned() -> None:
    rules = [{"name": "national", "field": "jurisdiction", "values": ["India"]},
             {"name": "regional", "field": "jurisdiction",
              "values": ["Delhi", "Bihar"]}]
    records = [_record("r1", jurisdiction="india"),
               _record("r2", jurisdiction="Delhi"),
               _record("r3", jurisdiction="Rajasthan")]

    out = derive(rules, records)
    views = {b["name"]: b for b in out["buckets"]}

    assert views["national"]["record_ids"] == ["r1"]
    assert views["regional"]["record_ids"] == ["r2"]
    assert views["regional"]["values"] == {"Delhi": 1}
    assert out["unassigned_record_ids"] == ["r3"]


def test_contains_matches_a_longer_court_name() -> None:
    rules = [{"name": "supreme", "field": "court",
              "contains": ["supreme court"]}]
    records = [_record("r1", court="SUPREME COURT OF INDIA"),
               _record("r2", court="High Court of Delhi")]

    out = derive(rules, records)

    assert out["buckets"][0]["record_ids"] == ["r1"]


def test_a_record_may_belong_to_more_than_one_bucket() -> None:
    rules = [{"name": "regional", "field": "jurisdiction", "values": ["Delhi"]},
             {"name": "legalaid", "field": "topic", "contains": ["legal aid"]}]
    records = [_record("r1", jurisdiction="Delhi", topic="free legal aid")]

    out = derive(rules, records)

    assert [b["count"] for b in out["buckets"]] == [1, 1]
    assert out["unassigned_count"] == 0


def test_one_fallback_takes_the_records_no_rule_matched() -> None:
    rules = [{"name": "national", "field": "jurisdiction", "values": ["India"]},
             {"name": "other", "label": "Other", "field": "jurisdiction",
              "fallback": True}]
    records = [_record("r1", jurisdiction="India"),
               _record("r2", jurisdiction="Goa")]

    out = derive(rules, records)
    other = out["buckets"][-1]

    assert other["fallback"] is True
    assert other["record_ids"] == ["r2"]
    assert out["unassigned_count"] == 0


def test_a_malformed_cell_does_not_match_everything() -> None:
    rules = [{"name": "national", "field": "jurisdiction", "values": ["India"]}]
    records = [{"record_id": "r1", "fields": {"jurisdiction": None}}]

    out = derive(rules, records)

    assert out["buckets"][0]["count"] == 0
    assert out["unassigned_record_ids"] == ["r1"]
