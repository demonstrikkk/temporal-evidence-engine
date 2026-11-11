"""The run as a tree of spans, written by the same call that writes the event.

`verification_events` is the audit rail: it is ordered, append-only, and it is
what a reader replays. It is not a tree, and it carries no durations or prices,
so "which stage cost the money" could not be answered from it.

This module adds the tree without adding a second account of the run. A span is
only closed by the rail event that describes it (`end(span, event, ...)` takes
the row `emit` returned), and `trace_spans.event_id` records that row's id. So a
span that exists without an event cannot be written, and the trace is a
projection: re-derive it from the rail and you get the same parent links.

Three rules this module holds:

1. **Never break the run.** Every store call is guarded; a trace write failure
   becomes a `trace.failed` event and the verification continues. Losing the
   trace is bad. Losing the verdict because the trace failed is worse.
2. **Structure, not timing, is the contract.** A replayed run reproduces the same
   spans, the same parents and the same order - `fingerprint()` is the canonical
   form of exactly that. Tokens, latency and timestamps are measurements of a
   particular execution and are expected to differ.
3. **No invented numbers.** `cost_usd` is copied from the call receipt and keeps
   its `cost_source`; a span with no price says `none` rather than zero.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logging import log
from app.core.config import settings
from app.verify.store import VerifyStore, iso, new_id, now
from app.verify.otel import OtelExporter, config_from_settings

#: Columns a caller may write. Anything else is a typo, and a typo in a trace is
#: a column that stays empty forever while looking like "this did not happen".
COLUMNS = (
    "stage", "operation", "attempt", "claim_id", "requested_model",
    "resolved_model", "provider", "prompt_version", "prompt_hash",
    "input_hash", "output_hash", "tool_name", "tool_arguments",
    "tool_result_ref", "input_tokens", "output_tokens", "cost_usd",
    "cost_source", "latency_ms", "cache_status", "error_type",
    "decision_rule", "decision_result",
)

#: Fields copied verbatim from a model call receipt onto the span that caused it.
def model_fields(receipt: dict | None, *, requested: str = "") -> dict[str, Any]:
    """A model call's receipt, as span columns.

    `requested_model` is what this build asked for; `resolved_model` is what the
    gateway says answered. Recording only one of them makes a fallback
    indistinguishable from a routing decision. `requested` is omitted rather
    than guessed when the caller did not name one - the span already carries
    what it asked for, and copying the answer into both would erase the
    difference this column exists to show.
    """
    receipt = receipt or {}
    fields: dict[str, Any] = {
        "resolved_model": str(receipt.get("model") or ""),
        "provider": str(receipt.get("provider") or ""),
        "input_tokens": int(receipt.get("input_tokens") or 0),
        "output_tokens": int(receipt.get("output_tokens") or 0),
        "cost_usd": float(receipt.get("cost_usd") or 0.0),
        "cost_source": str(receipt.get("cost_source") or "none"),
        "latency_ms": int(receipt.get("latency_ms") or 0),
        "cache_status": str(receipt.get("status") or ""),
    }
    if requested:
        fields["requested_model"] = requested
    return fields

#: What an unset column holds. `cost_source` is `none`, never `gateway`: an
#: unpriced span must not look like a free one.
DEFAULTS: dict[str, Any] = {
    "claim_id": "", "requested_model": "", "resolved_model": "", "provider": "",
    "prompt_version": "", "prompt_hash": "", "input_hash": "", "output_hash": "",
    "tool_name": "", "tool_arguments": {}, "tool_result_ref": "",
    "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
    "cost_source": "none", "latency_ms": 0, "cache_status": "",
    "error_type": "", "decision_rule": "", "decision_result": "",
}


@dataclass
class Span:
    """One operation that has begun and not yet been written."""

    id: str
    trace_id: str
    parent_span_id: str | None
    stage: str
    operation: str
    ordinal: int
    attempt: int = 1
    started_at: str = ""
    started_clock: float = 0.0
    fields: dict[str, Any] = field(default_factory=dict)
    closed: bool = False

    def row(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": self.id, "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "ordinal": self.ordinal, "stage": self.stage,
            "operation": self.operation, "attempt": self.attempt,
            "started_at": self.started_at,
        }
        # Every column is written, not left to the store's default. The local
        # store has no schema, so a missing key would read back as `None` there
        # and as the column default in Postgres - the same trace with two shapes.
        for name in COLUMNS:
            base.setdefault(name, DEFAULTS.get(name))
        base.update(self.fields)
        return base


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(fields) - set(COLUMNS))
    if unknown:
        raise TypeError(f"trace span has no column for {unknown}")
    return {key: value for key, value in fields.items() if value is not None}


class TraceRecorder:
    """Builds one run's tree, or nothing at all when tracing is off.

    `enabled=False` exists so a bulk harness can skip writes it will not read.
    Every method then returns `None` and the orchestrator's call sites stay
    unchanged - there is no second code path that could drift from the traced
    one. A run with tracing off has no tree; the API says so rather than serving
    an empty one, because "no tree" and "a tree with nothing in it" are
    different claims about a run.
    """

    def __init__(self, store: VerifyStore, run_id: str, *, emit: Callable[
            [str, dict], dict | None], enabled: bool = True,
                 exporter: OtelExporter | None = None) -> None:
        self.store = store
        self.run_id = run_id
        self.trace_id = new_id()
        self._emit = emit
        self.enabled = enabled
        self.exporter = exporter
        self.root: Span | None = None
        self.error = ""
        self._ordinal = 0
        self._open: list[Span] = []

    # -- lifecycle --------------------------------------------------------
    def open(self, *, event: dict | None = None,
             started_at: str | None = None) -> Span | None:
        """Create the trace and its root span.

        The root is the run. Its children are the stages, and the run's own
        duration is the only one that is guaranteed to be meaningful even when
        every child failed early.
        """
        if not self.enabled:
            return None
        self.root = self._begin("run", "run", parent=None, ordinal=0,
                                started_at=started_at or iso(now()),
                                attempt=1, fields={})
        # The root occupies ordinal 0; children start at 1 and there is no gap.
        self._ordinal = 0
        try:
            self.store.put_execution_trace({
                "id": new_id(), "run_id": self.run_id,
                "trace_id": self.trace_id, "root_span_id": self.root.id,
                "status": "running", "started_at": started_at or iso(now()),
                "completed_at": None,
            })
        except Exception as exc:  # noqa: BLE001 - a trace failure is not a run failure
            self._degrade(exc)
        return self.root

    def begin(self, stage: str, operation: str, *, parent: Span | None = None,
              ordinal: int | None = None, started_at: str | None = None,
              attempt: int = 1, **fields: Any) -> Span | None:
        if not self.enabled:
            return None
        return self._begin(stage, operation, parent=parent, ordinal=ordinal,
                           started_at=started_at, attempt=attempt, fields=fields)

    def _begin(self, stage: str, operation: str, *, parent: Span | None,
               ordinal: int | None, started_at: str | None, attempt: int,
               fields: dict[str, Any]) -> Span:
        self._ordinal += 1
        span = Span(
            id=new_id(), trace_id=self.trace_id,
            parent_span_id=parent.id if parent is not None else (
                self.root.id if self.root is not None else None),
            stage=stage, operation=operation,
            ordinal=self._ordinal if ordinal is None else ordinal,
            attempt=attempt, started_at=started_at or iso(now()),
            started_clock=time.perf_counter(), fields=_clean(fields),
        )
        self._open.append(span)
        return span

    def end(self, span: Span | None, event: dict | None = None,
            **fields: Any) -> dict[str, Any] | None:
        """Close a span, linking it to the rail row `event` came from.

        `event` is the dict `Verifier.emit` returned. Passing None is allowed
        and means the rail has no row for this operation; the span still exists
        and `event_id` stays NULL, which is a fact rather than a gap.
        """
        if span is None or not self.enabled:
            return None
        if span.closed:
            # Closing twice would write two rows for one operation, which is the
            # quickest way to make a cost total twice the bill.
            return None
        span.closed = True
        clean = _clean(fields)
        if "latency_ms" not in clean:
            elapsed = (time.perf_counter() - span.started_clock) * 1000.0
            clean["latency_ms"] = max(0, int(round(elapsed)))
        span.fields.update(clean)
        row = {
            **span.row(),
            "event_id": str(event["id"]) if event else None,
            "ended_at": iso(now()),
        }
        try:
            self.store.add_trace_span(row)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            self._degrade(exc)
            self._discard(span)
            return None
        # Export to OTLP (non-blocking; failures are logged and swallowed).
        if self.exporter is not None:
            try:
                self.exporter.emit(row)
            except Exception as exc:  # noqa: BLE001
                log.warning("OTLP export emit failed",
                            extra={"data": {"error": str(exc)[:200]}})
        self._discard(span)
        return row

    def _discard(self, span: Span) -> None:
        try:
            self._open.remove(span)
        except ValueError:
            pass

    def finish(self, status: str, *, event: dict | None = None,
               **fields: Any) -> None:
        """Close the root span and stamp the trace's final status.

        Any span still open at this point is a defect in the orchestrator, and
        it is written as one (`error_type="unclosed"`) rather than dropped: an
        operation that began and never reported is exactly the silence this
        repository exists to remove.
        """
        if not self.enabled:
            return
        for span in list(self._open):
            if span is self.root:
                continue
            log.warning("trace span was never closed",
                        extra={"data": {"run_id": self.run_id,
                                        "stage": span.stage,
                                        "operation": span.operation}})
            self.end(span, None, error_type="unclosed")
        self.end(self.root, event, **fields)
        try:
            self.store.update_execution_trace(
                self.run_id, status=status, completed_at=iso(now()),
                root_span_id=(self.root.id if self.root else ""))
        except Exception as exc:  # noqa: BLE001
            self._degrade(exc)
        # Flush OTLP export so a completed run's trace is exported before exit.
        if self.exporter is not None:
            try:
                self.exporter.flush(timeout_s=10.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("OTLP export flush failed",
                            extra={"data": {"error": str(exc)[:200]}})

    def _degrade(self, exc: Exception) -> None:
        """Report once, then keep going. A broken trace must not stop a verdict."""
        if self.error:
            return
        self.error = str(exc)[:300]
        log.warning("execution trace could not be written",
                    extra={"data": {"run_id": self.run_id,
                                    "error": self.error}})
        try:
            self._emit("trace.failed", {"error": self.error,
                                        "detail": "the run continues; the tree "
                                                  "is incomplete"})
        except Exception:  # noqa: BLE001 - the emitter itself is the failure
            pass

    # -- reading ----------------------------------------------------------
    def spans(self) -> list[dict]:
        try:
            return self.store.list_trace_spans(self.trace_id)
        except Exception:  # noqa: BLE001
            return []


# ---------------------------------------------------------------------------
# reading the tree
# ---------------------------------------------------------------------------

def build_tree(spans: list[dict]) -> dict[str, Any] | None:
    """Nest spans by parent, preserving the order they were begun in.

    A span whose parent is missing is attached to the root and flagged, because
    silently promoting it to a top-level stage would hide a broken tree.
    """
    ordered = sorted(spans, key=lambda row: int(row.get("ordinal") or 0))
    nodes: dict[str, dict[str, Any]] = {}
    for row in ordered:
        node = {**_clean_row(row), "children": []}
        nodes[str(row["id"])] = node
    root: dict[str, Any] | None = None
    for row in ordered:
        node = nodes[str(row["id"])]
        parent_id = row.get("parent_span_id")
        if not parent_id:
            root = root or node
            if root is not node:
                node["orphan"] = True
                root["children"].append(node)
            continue
        parent = nodes.get(str(parent_id))
        if parent is None:
            node["orphan"] = True
            if root is None:
                root = node
            else:
                root["children"].append(node)
            continue
        parent["children"].append(node)
    return root


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in row.items() if key != "children"}
    for name in ("started_at", "ended_at"):
        value = out.get(name)
        out[name] = iso(value) if hasattr(value, "astimezone") else value
    return out


def summarise(spans: list[dict]) -> dict[str, Any]:
    """Totals per stage, with billed and estimated cost kept apart.

    A single `cost_usd` that adds a gateway-reported bill to a catalogue
    estimate is a number that looks better than it is. The split is the point.
    """
    stages: dict[str, dict[str, Any]] = {}
    total = {"spans": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0, "gateway_usd": 0.0, "estimated_usd": 0.0,
             "priced_spans": 0, "gateway_spans": 0, "catalogue_spans": 0,
             "unpriced_spans": 0, "errors": 0, "snapshots": 0}
    for row in spans:
        stage = str(row.get("stage") or "run")
        bucket = stages.setdefault(stage, {
            "stage": stage, "spans": 0, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0, "gateway_usd": 0.0,
            "estimated_usd": 0.0, "priced_spans": 0, "gateway_spans": 0,
            "catalogue_spans": 0, "unpriced_spans": 0, "errors": 0,
            "latency_ms": 0})
        cost = float(row.get("cost_usd") or 0.0)
        source = str(row.get("cost_source") or "none")
        tokens_in = int(row.get("input_tokens") or 0)
        tokens_out = int(row.get("output_tokens") or 0)
        for target in (bucket, total):
            target["spans"] += 1
            target["input_tokens"] += tokens_in
            target["output_tokens"] += tokens_out
            target["cost_usd"] += cost
        if source == "gateway":
            bucket["gateway_usd"] += cost
            bucket["gateway_spans"] += 1
            bucket["priced_spans"] += 1
            total["gateway_usd"] += cost
            total["gateway_spans"] += 1
            total["priced_spans"] += 1
        elif source == "catalogue":
            bucket["estimated_usd"] += cost
            bucket["catalogue_spans"] += 1
            bucket["priced_spans"] += 1
            total["estimated_usd"] += cost
            total["catalogue_spans"] += 1
            total["priced_spans"] += 1
        else:
            bucket["unpriced_spans"] += 1
            total["unpriced_spans"] += 1
        if row.get("error_type"):
            bucket["errors"] += 1
            total["errors"] += 1
        if str(row.get("cache_status") or "") == "snapshot":
            total["snapshots"] += 1
        bucket["latency_ms"] += int(row.get("latency_ms") or 0)
    total["cost_usd"] = round(total["cost_usd"], 6)
    total["gateway_usd"] = round(total["gateway_usd"], 6)
    total["estimated_usd"] = round(total["estimated_usd"], 6)
    for bucket in stages.values():
        for key in ("cost_usd", "gateway_usd", "estimated_usd"):
            bucket[key] = round(bucket[key], 6)
    ordered = sorted(stages.values(),
                     key=lambda bucket: (-bucket["cost_usd"],
                                         -bucket["input_tokens"], bucket["stage"]))
    return {"totals": total, "stages": ordered}


def fingerprint(spans: list[dict]) -> str:
    """The canonical form of the tree's structure.

    Ids are UUIDs and timings are measurements, so neither can be part of a
    claim that two runs produced the same tree. What is compared instead: each
    span's position, its parent's position, what it did, and which attempt it
    was. That is the whole of "same spans, same parents, same order".
    """
    import json

    ordinal_of = {str(row["id"]): int(row.get("ordinal") or 0) for row in spans}
    canonical = [
        {
            "ordinal": int(row.get("ordinal") or 0),
            "parent": ordinal_of.get(str(row.get("parent_span_id")), None),
            "stage": row.get("stage") or "",
            "operation": row.get("operation") or "",
            "attempt": int(row.get("attempt") or 1),
            "tool": row.get("tool_name") or "",
            "prompt_version": row.get("prompt_version") or "",
            "error_type": row.get("error_type") or "",
            "cache_status": row.get("cache_status") or "",
        }
        for row in sorted(spans, key=lambda r: int(r.get("ordinal") or 0))
    ]
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
