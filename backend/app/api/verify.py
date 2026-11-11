"""The verification API. One router, no dataset endpoints.

`POST /runs` returns immediately and the work happens in a worker thread; every
stage is readable from `GET /runs/{id}/events`, which is what the UI streams.
That shape is deliberate: a verification run takes tens of seconds because it
fetches real pages, and a synchronous endpoint would look like a hang.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from app.api.deps import correlation_id, require_api_key
from app.bifrost.client import BifrostClient
from app.core.config import RULE_VERSION, settings
from app.core.errors import not_found, validation
from app.verify import orchestrator
from app.verify.report import build_report
from app.verify.runtime import get_store
from app.verify.schemas import AnswerIn
from app.verify.value_normalizer import differing_fields

router = APIRouter(prefix="/api/verify", tags=["verify"],
                   dependencies=[Depends(require_api_key)])

_TERMINAL = {"completed", "partial", "failed"}


def _store():
    return get_store()


def _claim_view(claim: dict, evidence_count: int = 0) -> dict[str, Any]:
    return {
        "id": claim["id"],
        "answer_id": claim.get("answer_id"),
        "run_id": claim.get("run_id"),
        "ordinal": claim.get("ordinal", 0),
        "claim_text": claim.get("claim_text") or "",
        "span_start": claim.get("span_start", -1),
        "span_end": claim.get("span_end", -1),
        "subject": claim.get("subject") or "",
        "predicate": claim.get("predicate") or "",
        "object_raw": claim.get("object_raw") or "",
        "object_normalized": claim.get("object_normalized") or {},
        "qualifiers": claim.get("qualifiers") or {},
        "claim_type": claim.get("claim_type") or "factual",
        "time_sensitive": bool(claim.get("time_sensitive")),
        "volatility_class": claim.get("volatility_class") or "moderate",
        "status": claim.get("status") or "pending",
        "reason_code": claim.get("reason_code"),
        "extracted_by": claim.get("extracted_by") or "extractor",
        "next_check_at": claim.get("next_check_at"),
        "last_verified_at": claim.get("last_verified_at"),
        "evidence_count": evidence_count,
    }


def _counters(claims: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for claim in claims:
        status = str(claim.get("status") or "pending")
        out[status] = out.get(status, 0) + 1
    out["claims"] = len(claims)
    return out


@router.get("/health")
async def health() -> dict[str, Any]:
    store = _store()
    gateway = await asyncio.to_thread(BifrostClient(store).health)
    return {
        "store": store.adapter,
        "gateway": gateway,
        "tavily": {"configured": bool(settings.TAVILY_API_KEY)},
        "rule_version": RULE_VERSION,
        "cache_mode": settings.CACHE_MODE,
        "search_depth": settings.TAVILY_SEARCH_DEPTH,
    }


@router.get("/gateway")
async def gateway_status() -> dict[str, Any]:
    """What the gateway is, and what it has actually done.

    `health()` is a probe; the rest is the audit trail the product claims to
    keep. Together they answer "is this really going through Bifrost?" without
    asking anyone to trust the log output.
    """
    store = _store()
    client = BifrostClient(store)
    health = await asyncio.to_thread(client.health)
    return {
        "base_url": client.base_url,
        "virtual_key_set": bool(client.virtual_key),
        "budget_usd": client.budget_usd,
        "spent_usd": await asyncio.to_thread(client.spent),
        "cache_mode": client.cache_mode,
        "models": {stage: client.model_for(stage) for stage in
                   ("extract_claims", "propose_relationship",
                    "replacement_value", "plan_research")},
        "health": health,
        "stats": await asyncio.to_thread(store.gateway_stats),
        "recent": await asyncio.to_thread(store.list_model_calls, 25),
    }


@router.post("/runs")
async def create_verification(body: AnswerIn) -> dict[str, Any]:
    store = _store()
    if not body.answer.strip():
        raise validation("answer must not be empty")
    created = await asyncio.to_thread(orchestrator.create_run, store, body)
    await asyncio.to_thread(orchestrator.launch, store, created.run_id)
    return {"run_id": created.run_id, "answer_id": created.answer_id,
            "status": "queued", "store": store.adapter,
            "rule_version": RULE_VERSION,
            "cache_mode": settings.CACHE_MODE}


@router.get("/runs")
async def list_runs(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    store = _store()
    rows = await asyncio.to_thread(store.list_runs, limit)
    answer_cache: dict[str, dict] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        answer_id = str(row.get("answer_id") or "")
        if answer_id not in answer_cache:
            answer_cache[answer_id] = (
                await asyncio.to_thread(store.get_answer, answer_id)) or {}
        answer = answer_cache[answer_id]
        out.append({
            **row,
            "prompt": answer.get("prompt") or "",
            "generated_at": answer.get("generated_at"),
        })
    return {"runs": out}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    store = _store()
    run = await asyncio.to_thread(store.get_run, run_id)
    if not run:
        raise not_found("run", run_id)
    answer = await asyncio.to_thread(store.get_answer, str(run["answer_id"]))
    claims = await asyncio.to_thread(store.list_claims, run_id)
    gateway = await asyncio.to_thread(store.gateway_stats, run_id)
    calls = await asyncio.to_thread(store.list_model_calls, 25, run_id)
    # A replay spends nothing and writes no call row, so the panel would show an
    # empty table for a run whose model answers all came from `model_calls`.
    # Count the replays from the rail instead of implying nothing happened.
    events = await asyncio.to_thread(store.list_events, run_id)
    replays = sum(1 for event in events
                  if event.get("event_type") == "model.snapshot")
    coverage = await asyncio.to_thread(store.get_coverage_audit, run_id)
    counts: list[dict] = []
    for claim in claims:
        evidence = await asyncio.to_thread(store.list_evidence, str(claim["id"]))
        counts.append(_claim_view(claim, len(evidence)))
    return {
        "id": run["id"],
        "answer_id": run["answer_id"],
        "status": run.get("status"),
        "stage": run.get("stage"),
        "rule_version": run.get("rule_version"),
        "cache_mode": run.get("cache_mode"),
        "budget_mode": run.get("budget_mode") or "balanced",
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "error": run.get("error"),
        "answer": {
            "id": answer.get("id") if answer else None,
            "prompt": (answer or {}).get("prompt") or "",
            "answer_text": (answer or {}).get("answer_text") or "",
            "generated_at": (answer or {}).get("generated_at"),
            "generated_at_source": (answer or {}).get("generated_at_source"),
            "submitted_at": (answer or {}).get("submitted_at"),
        },
        "claims": counts,
        "counters": _counters(claims),
        "coverage": _coverage_summary(coverage),
        "gateway": {**gateway, "recent": calls, "replays": replays},
    }


def _coverage_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    dispositions = row.get("dispositions") or {}
    return {
        "total_assertions": row.get("total_assertions") or 0,
        "checked": row.get("checked") or 0,
        "coverage": row.get("coverage") or 0.0,
        "material_coverage": row.get("material_coverage") or 0.0,
        "dispositions": dispositions,
        "uncovered": dispositions.get("not_checked", 0),
        "repair_calls": row.get("repair_calls") or 0,
        "repaired_claims": row.get("repaired_claims") or 0,
    }


@router.get("/runs/{run_id}/coverage")
async def get_coverage(run_id: str) -> dict[str, Any]:
    """Every assertion the answer made, and what happened to it.

    A separate endpoint because the assertion list is long: the run view wants
    the summary, a reader investigating a gap wants the spans.
    """
    store = _store()
    run = await asyncio.to_thread(store.get_run, run_id)
    if not run:
        raise not_found("run", run_id)
    audit = await asyncio.to_thread(store.get_coverage_audit, run_id)
    spans = await asyncio.to_thread(store.list_assertion_spans, run_id)
    return {
        "run_id": run_id,
        "audit": audit,
        "assertions": spans,
        "note": "A disposition describes the extraction, not the fact. "
                "`not_checked` is a coverage gap, not a verdict.",
    }


@router.get("/runs/{run_id}/claims/{claim_id}")
async def get_claim(run_id: str, claim_id: str) -> dict[str, Any]:
    store = _store()
    claim = await asyncio.to_thread(store.get_claim, claim_id)
    if not claim or str(claim.get("run_id")) != run_id:
        raise not_found("claim", claim_id)
    evidence = await asyncio.to_thread(store.list_evidence, claim_id)
    history = await asyncio.to_thread(store.list_history, claim_id)
    assertions = await asyncio.to_thread(store.list_assertions, claim_id)
    events = await asyncio.to_thread(store.list_events, run_id, 0)
    settled = [e for e in events
               if e.get("event_type") == "claim.settled"
               and (e.get("payload") or {}).get("claim_id") == claim_id]

    from app.verify.report import evidence_view
    views = [evidence_view(row, store) for row in evidence]
    from app.verify.corroboration import assess_claim
    corroboration = assess_claim(store, claim_id,
                                 str(claim.get("subject") or ""))
    replacement = next((v for v in views
                        if v.get("candidate_value_raw")
                        and str(claim.get("status")) == "changed"), None)
    diff = None
    if replacement:
        diff = differing_fields(claim.get("object_raw") or "",
                                replacement.get("candidate_value_raw") or "")

    last = settled[-1].get("payload") if settled else {}
    return {
        **_claim_view(claim, len(views)),
        "rule": last.get("rule"),
        "explanation": last.get("explanation")
        or "No verdict has been recorded for this claim yet.",
        "freshness_index": last.get("freshness_index"),
        "value_diff": diff,
        "evidence": views,
        "corroboration": corroboration,
        "timeline": [
            {
                "status": row.get("new_status"),
                "previous_status": row.get("previous_status"),
                "reason_code": row.get("reason_code"),
                "rule_version": row.get("rule_version"),
                "decided_at": row.get("decided_at"),
            }
            for row in history
        ],
        "assertions": assertions,
    }


@router.get("/runs/{run_id}/events")
async def get_events(run_id: str, after: int = Query(default=0, ge=0)) -> dict[str, Any]:
    store = _store()
    run = await asyncio.to_thread(store.get_run, run_id)
    if not run:
        raise not_found("run", run_id)
    events = await asyncio.to_thread(store.list_events, run_id, after)
    return {
        "events": events,
        "next": int(events[-1]["sequence"]) if events else after,
        "terminal": str(run.get("status")) in _TERMINAL,
        "status": run.get("status"),
    }


@router.get("/runs/{run_id}/stream")
async def stream_events(run_id: str, request: Request,
                        after: int = Query(default=0, ge=0)):
    store = _store()
    run = await asyncio.to_thread(store.get_run, run_id)
    if not run:
        raise not_found("run", run_id)

    async def _generator():
        cursor = after
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                events = await asyncio.to_thread(store.list_events, run_id, cursor)
                current = await asyncio.to_thread(store.get_run, run_id)
            except Exception as exc:  # noqa: BLE001 — the stream reports, not crashes
                yield {"event": "stream.error",
                       "data": json.dumps({"error": str(exc)[:300]})}
                break
            for event in events:
                cursor = int(event["sequence"])
                body = {
                    **(event.get("payload") or {}),
                    "sequence": cursor,
                    "created_at": event.get("created_at"),
                }
                yield {"event": str(event["event_type"]),
                       "id": str(cursor),
                       "data": json.dumps(body, default=str, ensure_ascii=False)}
                idle = 0
            status = str((current or {}).get("status"))
            if status in _TERMINAL and not events:
                yield {"event": "stream.end",
                       "data": json.dumps({"status": status, "next": cursor})}
                break
            idle += 1
            if idle % 20 == 0:
                yield {"event": "stream.heartbeat",
                       "data": json.dumps({"next": cursor, "status": status})}
            await asyncio.sleep(0.5)

    return EventSourceResponse(_generator())


@router.get("/runs/{run_id}/report")
async def get_report(run_id: str) -> dict[str, Any]:
    store = _store()
    try:
        return await asyncio.to_thread(build_report, store, run_id)
    except KeyError:
        raise not_found("run", run_id) from None


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def get_report_markdown(run_id: str) -> PlainTextResponse:
    store = _store()
    try:
        report = await asyncio.to_thread(build_report, store, run_id)
    except KeyError:
        raise not_found("run", run_id) from None
    return PlainTextResponse(str(report["markdown"]),
                             media_type="text/markdown; charset=utf-8")


@router.get("/watchlist")
async def watchlist(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    store = _store()
    rows = await asyncio.to_thread(store.list_due, limit)
    return {
        "claims": [
            {
                "id": row.get("id"),
                "run_id": row.get("run_id"),
                "claim_text": row.get("claim_text"),
                "status": row.get("status"),
                "reason_code": row.get("reason_code"),
                "next_check_at": row.get("next_check_at"),
                "last_verified_at": row.get("last_verified_at"),
                "volatility_class": row.get("volatility_class"),
            }
            for row in rows
        ],
        "note": "Recommendations only. This product does not run a scheduler.",
    }


@router.get("/ping", dependencies=[Depends(correlation_id)])
async def ping() -> dict[str, str]:
    return {"status": "ok", "service": "temporal-evidence-engine"}
