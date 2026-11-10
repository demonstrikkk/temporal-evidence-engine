"""Answer context compiler: builds compact, structured evidence packages for synthesis.

Ensures the answer model only receives settled evidence, verified claims, and
honest uncertainty labels rather than raw unindexed web text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.verify.store import VerifyStore, iso, now


def compile_evidence_context(
    store: VerifyStore,
    run_id: str,
    user_question: str = "",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compile a compact evidence package for a verification run."""
    run = store.get_run(run_id) or {}
    answer = store.get_answer(str(run.get("answer_id") or "")) or {}
    claims = store.list_claims(run_id)

    target_question = user_question or str(answer.get("prompt") or "") or "What is verified about these claims?"
    as_of_dt = as_of or answer.get("generated_at") or now()
    as_of_str = as_of_dt.isoformat() if isinstance(as_of_dt, datetime) else str(as_of_dt)

    verified_claims = []
    unverifiable_claims = []
    sources_map: dict[str, dict[str, Any]] = {}

    for claim in claims:
        cid = str(claim["id"])
        status = str(claim.get("status") or "pending")
        evidence_rows = store.list_evidence(cid)

        # Collect accepted evidence only
        accepted_ev_ids: list[str] = []
        for ev in evidence_rows:
            link = ev.get("link") or {}
            if link.get("settlement_status") == "accepted":
                ev_id = str(ev.get("id") or link.get("evidence_id"))
                accepted_ev_ids.append(ev_id)

                if ev_id not in sources_map:
                    rev = ev.get("revision") or {}
                    src = store.get_source(str(rev.get("source_id") or "")) or {}
                    sources_map[ev_id] = {
                        "evidence_id": ev_id,
                        "quote": str(ev.get("quote_text") or ""),
                        "source_title": str(src.get("title") or "Source"),
                        "url": str(src.get("canonical_url") or ""),
                        "event_date": ev.get("event_date"),
                        "event_date_status": ev.get("event_date_status"),
                    }

        claim_info = {
            "claim_id": cid,
            "text": str(claim.get("claim_text") or ""),
            "status": status,
            "subject": str(claim.get("subject") or ""),
            "predicate": str(claim.get("predicate") or ""),
            "value_raw": str(claim.get("object_raw") or ""),
            "reason_code": claim.get("reason_code"),
            "evidence_ids": accepted_ev_ids,
        }

        if status in ("supported", "changed"):
            verified_claims.append(claim_info)
        else:
            unverifiable_claims.append(claim_info)

    return {
        "user_question": target_question,
        "answer_as_of": as_of_str,
        "verified_claims": verified_claims,
        "unverifiable_claims": unverifiable_claims,
        "sources": list(sources_map.values()),
    }
