"""Compare the models this deployment has actually used.

There is a temptation, with a page called "compare models", to invent a score.
This endpoint does not. It joins two tables that already exist and reports what
they measured:

* `model_calls` — recorded calls, tokens, cost, latency, failures, per model
  that answered. A row is one distinct `(prompt, model, stage)`; a cached repeat
  reuses it, so this is "recorded calls", not "invocations";
* `answers` joined to `claims` — the verdict mix of every answer a model wrote,
  once the caller recorded the attribution (the playground does; a pasted answer
  usually does not).

Nothing here aggregates "quality" into a single number. `supported_rate` is the
share of settled claims that came back supported, which is a fact about the
verdict mix, not a claim that one model is better: a model that writes safe
trivialities scores high on it and says nothing useful. The page says so.

Two honesty rules the code enforces rather than documents:

* An unknown cost is never a free call. `cost_source = none` calls are counted
  in `calls` and excluded from `avg_cost_usd`, with `priced_calls` beside it.
* An answer with no recorded model is grouped as `pasted answers`, not assigned
  to the model that happens to be most common and not lumped under a model id
  called `unknown`.

The free-model registry is merged in as context — vendor, transport, privacy
posture, the date it was last observed working — with its measurements left
null. It is a registry, not a benchmark.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import require_api_key
from app.providers.llm import zen
from app.verify.runtime import get_store

router = APIRouter(prefix="/api/compare", tags=["compare"],
                   dependencies=[Depends(require_api_key)])

#: Transport -> the API a reader sees. The registry writes `transport`; a
#: recorded call is always through Bifrost (invariant 4), so measured rows use
#: the gateway label regardless of what the registry says.
_API = {
    "gateway": "Bifrost gateway",
    "direct": "Zen direct REST",
    "opencode": "OpenCode server",
    "systemone": "SystemOne",
}


def _store():
    return get_store()


def _rates(row: dict[str, Any]) -> dict[str, Any]:
    """The verdict mix as shares of *settled* claims.

    Pending is excluded from the denominator: a claim still running is not a
    bad verdict, and dividing by it would make a slow model look inaccurate.
    A zero denominator yields `None`, which the page renders as "not measured".
    """
    settled = int(row.get("settled") or 0)
    out = {"settled": settled}
    for status in ("supported", "changed", "contradicted", "unverifiable"):
        count = int(row.get(status) or 0)
        out[f"{status}_rate"] = round(count / settled, 4) if settled else None
    return out


def _measured_row(model: str, calls: dict[str, Any] | None,
                  verdicts: dict[str, Any] | None) -> dict[str, Any]:
    calls = calls or {}
    verdicts = verdicts or {}
    providers = sorted({*(calls.get("providers") or []), *(verdicts.get("providers") or [])})
    row: dict[str, Any] = {
        "model": model,
        "origin": "measured",
        # Company/API as recorded. The identifier is the fact; a display name
        # invented here would be a guess about a vendor we never asked.
        "vendor": providers[0] if providers else "",
        "providers": providers,
        "transport": "gateway",
        "api": _API["gateway"],
        "privacy": "",
        "observed": "",
        "note": "",
        "calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "priced_calls": None,
        "avg_cost_usd": None,
        "timed_calls": None,
        "avg_latency_ms": None,
        "error_calls": None,
        "error_rate": None,
        "stages": [],
        "last_call_at": None,
        "answers": 0,
        "claims": 0,
        "settled": 0,
        "supported": 0,
        "changed": 0,
        "contradicted": 0,
        "unverifiable": 0,
        "pending": 0,
        "time_sensitive": 0,
        "due": 0,
        "last_verified_at": None,
        "supported_rate": None,
        "changed_rate": None,
        "contradicted_rate": None,
        "unverifiable_rate": None,
    }
    for key in ("calls", "input_tokens", "output_tokens", "cost_usd",
                "priced_calls", "avg_cost_usd", "timed_calls",
                "avg_latency_ms", "error_calls", "error_rate", "stages",
                "last_call_at"):
        if key in calls:
            row[key] = calls[key]
    for key in ("answers", "claims", "supported", "changed", "contradicted",
                "unverifiable", "pending", "time_sensitive", "due",
                "last_verified_at"):
        if key in verdicts:
            row[key] = verdicts[key]
    row.update(_rates({**verdicts, "settled": verdicts.get("settled", 0)}))
    return row


def _pasted_row(calls: dict[str, Any] | None,
                verdicts: dict[str, Any] | None) -> dict[str, Any]:
    """Pasted answers, whose author was never recorded.

    Not a model row: the model cell names the group, not an id, and the API
    column says what *was* recorded — the scan went through Bifrost (invariant
    4). Attributing these claims to the scanning model would be the exact lie
    this page exists to avoid: the extractor is not the author.
    """
    row = _measured_row("", calls, verdicts)
    row.update({
        "origin": "pasted",
        "providers": [],
        "vendor": "",
        "transport": "",
        "api": "scanned via Bifrost",
    })
    return row


def _registry_row(entry: dict[str, Any]) -> dict[str, Any]:
    transport = str(entry.get("transport") or "")
    return {
        "model": str(entry.get("id") or ""),
        "origin": "registry",
        "vendor": str(entry.get("vendor") or ""),
        "providers": [],
        "transport": transport,
        "api": _API.get(transport, transport or "not recorded"),
        "privacy": str(entry.get("data") or ""),
        "observed": str(entry.get("observed") or ""),
        "note": str(entry.get("note") or ""),
        "calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "priced_calls": None,
        "avg_cost_usd": None,
        "timed_calls": None,
        "avg_latency_ms": None,
        "error_calls": None,
        "error_rate": None,
        "stages": [],
        "last_call_at": None,
        "answers": 0,
        "claims": 0,
        "settled": 0,
        "supported": 0,
        "changed": 0,
        "contradicted": 0,
        "unverifiable": 0,
        "pending": 0,
        "time_sensitive": 0,
        "due": 0,
        "last_verified_at": None,
        "supported_rate": None,
        "changed_rate": None,
        "contradicted_rate": None,
        "unverifiable_rate": None,
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row["origin"] == "measured"]
    # Pasted answers are counted in the totals — their claims are as real as any
    # other — but they are not a model, so they never enter `measured_models`.
    counted = [row for row in rows if row["origin"] != "registry"]
    sums = {key: 0 for key in (
        "calls", "input_tokens", "output_tokens", "priced_calls", "answers",
        "claims", "settled", "supported", "changed", "contradicted",
        "unverifiable", "pending", "due")}
    cost = 0.0
    for row in counted:
        for key in sums:
            sums[key] += int(row.get(key) or 0)
        cost += float(row.get("cost_usd") or 0)
    cost = round(cost, 6)
    totals: dict[str, Any] = {
        "models": len(rows),
        "measured_models": len(measured),
        "scanned_models": sum(1 for row in measured if int(row.get("answers") or 0)),
        "pasted_answers": sum(
            int(row.get("answers") or 0) for row in rows if row["origin"] == "pasted"),
        **sums,
        "cost_usd": cost,
        "avg_cost_usd": round(cost / sums["priced_calls"], 6)
        if sums["priced_calls"] else None,
    }
    totals.update(_rates(totals))
    return totals


def _models_payload() -> dict[str, Any]:
    store = _store()
    call_rows = store.model_call_stats()
    verdict_rows = store.model_verdict_stats()

    calls_by_model = {str(row.get("model") or "unknown"): row for row in call_rows}
    # An empty model here is a pasted answer, not a missing key. Coercing it to
    # a name would put the group back in the model column.
    verdicts_by_model = {str(row.get("model") or ""): row for row in verdict_rows}

    # Observed models first: a recorded call or a scanned answer is proof the
    # model exists. The registry follows, and never overwrites a measured row.
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model in list(calls_by_model) + [m for m in verdicts_by_model if m not in calls_by_model]:
        seen.add(model)
        if model:
            rows.append(_measured_row(model, calls_by_model.get(model),
                                      verdicts_by_model.get(model)))
        else:
            rows.append(_pasted_row(calls_by_model.get(model),
                                    verdicts_by_model.get(model)))
    for entry in zen.free_models():
        model = str(entry.get("id") or "")
        if model and model not in seen:
            seen.add(model)
            rows.append(_registry_row(entry))

    # Measured rows first, by how much evidence they carry; the registry keeps
    # its own order (the tuple is curated). Ties break on the identifier so the
    # table is stable between requests.
    # Models first, then the pasted group, then the catalogue. Sorting by a
    # metric must never lift a group above a model it is not comparable to.
    def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
        weight = int(row.get("calls") or 0) + int(row.get("claims") or 0)
        rank = {"measured": 0, "pasted": 1}.get(row["origin"], 2)
        return (rank, -weight, str(row["model"]))

    rows.sort(key=sort_key)
    return {
        "models": rows,
        "totals": _totals(rows),
        "notes": {
            "calls": (
                "One row per distinct (prompt, model, stage). A cached repeat "
                "reuses the row, so this counts recorded calls, not requests."
            ),
            "cost": (
                "`cost_source = none` means the price was unknown, not zero. "
                "Average cost divides by priced calls only."
            ),
            "verdicts": (
                "Supported share is the verdict mix over settled claims "
                "(pending excluded). It is not a quality score: trivially true "
                "claims score high on it."
            ),
            "attribution": (
                "A verdict is attributed to a model only when the scan recorded "
                "the model that wrote the answer. The extractor that read the "
                "answer is not its author, so it is never credited."
            ),
            "pasted": (
                "Answers pasted into the checker name no model. They are grouped "
                "as `pasted answers`, shown with their own verdict mix, and never "
                "split across models that might have written them."
            ),
            "registry": (
                "Registry rows are the free-model catalogue, not measurements. "
                "`observed` is the date someone last saw that route work."
            ),
        },
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


@router.get("/models")
async def compare_models() -> dict[str, Any]:
    """The comparison matrix: measured rows, then the free-model registry."""
    return await asyncio.to_thread(_models_payload)
