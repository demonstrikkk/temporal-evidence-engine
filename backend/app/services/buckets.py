"""Derived views over a dataset's cells.

A bucket is not a second column and not a copy of a record. It is a rule that
says "records whose `jurisdiction` is India belong in the national view". The
service reads the stored cells, groups their record ids, and returns the view.
Nothing is written, so a bucket can never disagree with the evidence it groups.

Two matcher shapes, ORed:

* `values` — exact, case-insensitive equality (a jurisdiction list).
* `contains` — substring, for fields whose value carries more than the bucket
  word ("Regional Office" in `office`).

A record may belong to more than one bucket. That is intentional: a document
can be both regional and about a national topic, and forcing one label would
hide the other. Records matching no rule are reported as `unassigned`, not
silently dropped.
"""
from __future__ import annotations

from typing import Any


def _texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _matches(value: Any, rule: dict) -> bool:
    exact = {str(v).strip().casefold() for v in (rule.get("values") or []) if str(v).strip()}
    needles = [str(v).strip().casefold() for v in (rule.get("contains") or []) if str(v).strip()]
    for text in _texts(value):
        folded = text.casefold()
        if exact and folded in exact:
            return True
        if needles and any(needle in folded for needle in needles):
            return True
    return False


def derive(rules: list[dict], records: list[dict]) -> dict:
    """Group `records` into the buckets declared by `rules`.

    `records` is the shape `repo.get_records` returns: each has `record_id` and
    `fields`, where a field is a provenance cell carrying `value`. A malformed
    cell is ignored rather than treated as matching, because a bucket that
    catches everything is indistinguishable from no bucket at all.
    """
    rules = [r for r in (rules or []) if isinstance(r, dict) and r.get("field")]
    views: list[dict] = []
    assigned: set[str] = set()
    fallback: list[dict] = []

    for rule in rules:
        if rule.get("fallback"):
            fallback.append(rule)
            continue
        matched: list[str] = []
        values: dict[str, int] = {}
        for record in records or []:
            rid = str(record.get("record_id") or "")
            cell = (record.get("fields") or {}).get(rule["field"]) or {}
            value = cell.get("value") if isinstance(cell, dict) else cell
            if not _matches(value, rule):
                continue
            if rid:
                matched.append(rid)
            for text in _texts(value):
                values[text] = values.get(text, 0) + 1
        if matched:
            assigned.update(matched)
        views.append({
            "name": str(rule.get("name") or rule["field"]),
            "label": str(rule.get("label") or rule.get("name") or rule["field"]),
            "field": str(rule["field"]),
            "count": len(matched),
            "record_ids": matched,
            "values": values,
            "fallback": False,
        })

    for rule in fallback:
        matched = []
        for record in records or []:
            rid = str(record.get("record_id") or "")
            if rid and rid not in assigned:
                matched.append(rid)
        views.append({
            "name": str(rule.get("name") or "other"),
            "label": str(rule.get("label") or rule.get("name") or "Other"),
            "field": str(rule["field"]),
            "count": len(matched),
            "record_ids": matched,
            "values": {},
            "fallback": True,
        })
        assigned.update(matched)

    unassigned = [str(r.get("record_id") or "") for r in (records or [])
                  if str(r.get("record_id") or "") and str(r.get("record_id")) not in assigned]
    return {
        "records": len(records or []),
        "buckets": views,
        "unassigned_count": len(unassigned),
        "unassigned_record_ids": unassigned,
    }
