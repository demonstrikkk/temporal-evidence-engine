"""Provenance helpers: single construction site for evidence-wrapped fields."""
import datetime


def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def field(value: object, status: str, url: str, title: str, quote: str,
          retrieved_at: str = "") -> dict:
    return {"value": value, "verification_status": status,
            "source": {"url": url, "title": title, "quote": quote,
                       "retrieved_at": retrieved_at or now_iso()}}


def summarize(rows: list[dict]) -> dict:
    """Record-level summary, plus field counts for detail.

    This used to report `{"records":1,"verified":0,"needs_review":4}`, which
    reads as four bad records when it is one record with four unproven fields.
    The counts are now named for what they measure, and a record is only
    `fully_verified` when every one of its fields is.
    """
    fully = partial = 0
    f_verified = f_unverified = f_unjudged = f_conflicting = f_throttled = 0
    # Temporal accounting: how many cells carry a date at all, and how their
    # dates sit against the time the request asked about. `temporal.check` is
    # advisory (it never changes `verification_status`), so these are reported
    # apart from the verification counts rather than folded into them.
    dated = 0
    t_within = t_before = t_after = t_uncertain = t_unknown = 0
    for r in rows:
        statuses = [v.get("verification_status")
                    for v in r.get("fields", {}).values() if isinstance(v, dict)]
        for cell in r.get("fields", {}).values():
            if not isinstance(cell, dict):
                continue
            if str(cell.get("valid_time") or "").strip():
                dated += 1
            status = str((cell.get("temporal_check") or {}).get("status") or "")
            if status == "within":
                t_within += 1
            elif status == "before":
                t_before += 1
            elif status == "after":
                t_after += 1
            elif status == "uncertain":
                t_uncertain += 1
            elif status == "unknown":
                t_unknown += 1
        for st in statuses:
            if st == "verified":
                f_verified += 1
            elif st == "rate_limited":
                f_throttled += 1
            elif st == "judgment_unavailable":
                f_unjudged += 1
            elif st == "conflicting":
                f_conflicting += 1
            else:
                f_unverified += 1
        if statuses and all(s == "verified" for s in statuses):
            fully += 1
        else:
            partial += 1
    return {"records": len(rows),
            "records_fully_verified": fully,
            "records_needing_review": partial,
            "fields_verified": f_verified,
            "fields_unverified": f_unverified,
            "fields_judgment_unavailable": f_unjudged,
            "fields_conflicting": f_conflicting,
            # Throttled is reported separately from unjudged: one is "come back
            # later", the other is "we had no judge to ask".
            "fields_rate_limited": f_throttled,
            # When the sources say their facts were true, and how that lines up
            # with the requested time. A cell can be perfectly verified and still
            # be from the wrong year; these counts are how a run says so.
            "cells_dated": dated,
            "temporal_within": t_within,
            "temporal_before": t_before,
            "temporal_after": t_after,
            "temporal_uncertain": t_uncertain,
            # Absence of a publication date is not evidence of the wrong date;
            # counting it as "outside" would slander every undated source.
            "temporal_unknown": t_unknown}
