"""The run: extract, research, settle, decide — and say what failed.

This module is the only place that assembles a verdict. It does so by calling
`decision.decide` with stored rows, and it never writes a status a model
suggested.

Every stage emits a durable event with a per-run sequence. A missing key, a
failed extraction, a page that could not be fetched, a model that returned
garbage: all of them are events, and all of them can end the run as
`run.partial`. A run that silently drops half its sources is worse than a run
that fails.
"""
from __future__ import annotations

import re
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from app.bifrost.client import BifrostClient, BifrostError
from app.core.config import RULE_VERSION, settings
from app.core.logging import log
from app.research.planner import plan_queries_detail
from app.research.sources import (canonicalize_url, domain_of, find_existing_revisions,
                                  save_revision, save_source)
from app.research.tavily import ResearchError, TavilyClient
from app.verify.decision import EvidenceFact, decide
from app.verify.coverage import apply_repair, audit as audit_coverage
from app.verify.extract import extract_claims, extract_missing_claims
from app.verify.freshness import freshness_index, next_check_at
from app.verify.prompts import (EXTRACT_PROMPT_VERSION, PROPOSE_PROMPT_VERSION,
                                REPAIR_PROMPT_VERSION)
from app.verify.relationship import Proposal, propose
from app.verify.schemas import (AnswerIn, ClaimDraft, ClaimStatus, ReasonCode,
                                Relationship, RunCreated)
from app.verify.settlement import settle_assertion
from app.verify.store import VerifyStore, build_store, iso, new_id, now, parse_iso
from app.verify.temporal import event_date_from_quote
from app.verify.otel import OtelExporter, config_from_settings, parse_traceparent
from app.verify.trace import TraceRecorder, model_fields
from app.cache.coordinator import CacheCoordinator

#: Negation cues that can contradict a claim without naming a replacement value.
_NEGATIONS = ("no longer", "not available", "no longer available",
              "discontinued", "has ended", "has been discontinued",
              "was discontinued", "shut down", "has shut down", "deprecated",
              "removed from", "closed down", "out of stock", "sold out")

_ACTIVE_RUNS: set[str] = set()
_LOCK = threading.Lock()

#: A word, for the pre-filter. Unicode-aware so a claim about "Zürich" can
#: match a page that says "zürich"; no stemming, because a stemmer that gets it
#: wrong would skip a page that would have answered the claim.
_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

#: Terms shorter than this are not evidence of anything: "per" appears on every
#: pricing page, so matching it would examine everything and save nothing.
_MIN_TERM = 4

#: Terms are compared as their first six characters, so "germany" matches
#: "german" and "available" matches "availability". Longer prefixes would miss
#: inflections; shorter ones would match unrelated words.
_TERM_PREFIX = 6


@dataclass
class ResearchPlan:
    """One subject group, researched once.

    The plan owns what would otherwise be recomputed per claim: the queries, the
    revisions they produced, and the pairs the pre-filter refused. A group of one
    is a plan of one — there is no second code path for the common case, because
    two paths drift.
    """

    group_key: str
    budget_mode: str
    claims: list[tuple[dict, ClaimDraft]]
    queries: list[str]
    queries_dropped: int = 0
    span: Any = None
    event: Any = None
    asked: list[str] = field(default_factory=list)
    revisions: list[str] = field(default_factory=list)
    pairs_examined: int = 0
    pairs_skipped: int = 0

    def still_unasked(self, query: str) -> bool:
        return self.asked.count(query) == 0


def _normalise_subject(subject: Any) -> str:
    """The grouping key: a subject reduced to its words.

    Case and punctuation are not what makes two claims the same topic, and
    keeping them would split "Acme Pro" from "acme pro." into two plans that
    then issue the same queries twice.
    """
    return " ".join(_WORD.findall(str(subject or "").casefold()))


def _claim_terms(draft: ClaimDraft) -> set[str]:
    """The words a page would have to share to be worth a model call."""
    text = f"{draft.subject or ''} {draft.object_raw or ''}".casefold()
    return {word[:_TERM_PREFIX] for word in _WORD.findall(text)
            if len(word) >= _MIN_TERM}


def _shares_term(content: str, terms: set[str]) -> bool:
    """Whether a document could possibly be about the claim's subject.

    An empty `terms` answers True: a claim with no usable term cannot be
    filtered, and guessing would skip pages this function is not smart enough
    to judge. The filter may only ever *skip* a pair whose text shares nothing
    with the claim, and every skip is counted and reported.
    """
    if not terms:
        return True
    lowered = str(content or "").casefold()
    return any(term in lowered for term in terms)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern, order in ((r"^(\d{4})-(\d{2})-(\d{2})", "ymd"),
                           (r"^(\d{1,2})/(\d{1,2})/(\d{4})", "mdy")):
        match = re.match(pattern, text)
        if match:
            try:
                if order == "ymd":
                    return date(int(match.group(1)), int(match.group(2)),
                                int(match.group(3)))
                return date(int(match.group(3)), int(match.group(1)),
                            int(match.group(2)))
            except ValueError:
                return None
    return None


def _credibility(domain: str, subject: str) -> str:
    """First-party when the domain is the subject's own name.

    `acme.com` is primary for a claim about Acme; `techcrunch.com` is secondary
    for the same claim. This is a naming heuristic, not a reputation database,
    and it only ever changes which rule resolves a conflict — never whether a
    quote counts.

    The label has to be read before the dots are stripped. An earlier version
    removed every non-alphanumeric character first, so `acme.example` became
    `acmeexample`, the `"." in host` branch was dead, and R7 could never fire:
    a rule that existed, was tested directly, and was unreachable from the
    product.
    """
    name = re.sub(r"[^a-z0-9]", "", (subject or "").lower())
    host = (domain or "").lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    label = re.sub(r"[^a-z0-9]", "", host.split(".")[0]) if host else ""
    if not name or not label:
        return "secondary"
    if len(label) >= 3 and (name.startswith(label) or label.startswith(name[:12])):
        return "primary"
    return "secondary"


def _dedupe_hits(hits: list[Any], *, per_domain: int = 2) -> list[Any]:
    seen_urls: set[str] = set()
    domain_count: dict[str, int] = {}
    out = []
    for hit in hits:
        canonical = canonicalize_url(hit.url)
        if not canonical or canonical in seen_urls:
            continue
        domain = domain_of(canonical)
        if domain_count.get(domain, 0) >= per_domain:
            continue
        seen_urls.add(canonical)
        domain_count[domain] = domain_count.get(domain, 0) + 1
        out.append(hit)
    return out


# ---------------------------------------------------------------------------
# run creation / execution
# ---------------------------------------------------------------------------

def create_run(store: VerifyStore, payload: AnswerIn,
               traceparent: str | None = None) -> RunCreated:
    answer_id = new_id()
    run_id = new_id()
    stamp = now()
    # The operator's selected mode is the default; an explicit per-request mode
    # wins. `model_fields_set` distinguishes "balanced because that is the
    # default" from "balanced because the caller asked for it".
    budget_mode = payload.budget_mode
    if "budget_mode" not in getattr(payload, "model_fields_set", set()):
        from app.governance.resource_budget import budget_manager
        budget_mode = budget_manager.current_mode
    store.create_answer({
        "id": answer_id,
        "prompt": payload.prompt,
        "answer_text": payload.answer,
        "generated_at": iso(payload.generated_at),
        "generated_at_source": payload.generated_at_source,
        "submitted_at": iso(stamp),
        "locale": payload.locale,
        "source_urls": list(payload.source_urls),
        "domain_filter": list(payload.domain_filter),
        "skip_non_factual": payload.skip_non_factual,
        # Attribution travels with the answer, not with the run: the same answer
        # may be re-scanned, and the model that wrote it is a property of the
        # text. Empty is the honest value when the caller did not say.
        "model": payload.model,
        "provider": payload.provider,
    })
    store.create_run({
        "id": run_id, "answer_id": answer_id, "status": "queued",
        "stage": "queued", "rule_version": RULE_VERSION,
        "cache_mode": settings.CACHE_MODE, "budget_mode": budget_mode,
        "started_at": iso(stamp),
        "traceparent": traceparent,
    })
    return RunCreated(run_id=run_id, answer_id=answer_id)


def launch(store: VerifyStore, run_id: str) -> None:
    """Execute in a worker thread. The API returns the run id immediately."""
    with _LOCK:
        if run_id in _ACTIVE_RUNS:
            return
        _ACTIVE_RUNS.add(run_id)

    def _target() -> None:
        try:
            Verifier(store, run_id).execute()
        finally:
            with _LOCK:
                _ACTIVE_RUNS.discard(run_id)

    threading.Thread(target=_target, name=f"verify-{run_id[:8]}",
                     daemon=True).start()


class Verifier:
    def __init__(self, store: VerifyStore, run_id: str, *,
                 bifrost: BifrostClient | None = None,
                 tavily: TavilyClient | None = None) -> None:
        self.store = store
        self.run_id = run_id
        self.bifrost = bifrost or BifrostClient(store, run_id)
        self.tavily = tavily or TavilyClient(store)
        self.cache = CacheCoordinator(store)
        self.failures: list[dict[str, Any]] = []
        #: The caller's W3C trace context, when the run was started with a
        #: `traceparent`. The run continues that trace; absent or malformed
        #: headers start a fresh one. Never a failure either way.
        self._incoming_traceparent: str | None = None
        run = self.store.get_run(self.run_id) or {}
        incoming = str(run.get("traceparent") or "")
        parsed = parse_traceparent(incoming) if incoming else None
        if parsed:
            self._incoming_traceparent = incoming
        #: OTLP export of the execution trace — additive observability only.
        #: A bad config or unavailable collector never reaches a verdict:
        #: `OtelExporter` degrades to disabled and keeps swallowing failures.
        self.otel = OtelExporter(
            config=config_from_settings(), store=store, run_id=run_id,
            trace_id_hint=(parsed or {}).get("trace_id", ""))
        #: The run as a tree. Every span is closed by the same emit call that
        #: writes its rail row, so the trace cannot drift from the events.
        self.trace = TraceRecorder(store, run_id, emit=self.emit,
                                   enabled=settings.TRACE_ENABLED,
                                   exporter=self.otel)
        self._trace_token: Any = None
        self.counters: dict[str, int] = {
            "claims": 0, "queries": 0, "sources": 0, "evidence": 0,
            "settled": 0, "failed_sources": 0, "failed_calls": 0,
            #: Model calls answered from the cache instead of the gateway. A run
            #: that replayed everything spent nothing and touched no provider,
            #: and saying so is the difference between a snapshot and a live run.
            "replays": 0,
            #: Proposals the extractor returned that did not become claims, and
            #: claims recovered by the one focused coverage repair. Both exist so
            #: a run cannot silently lose what the answer actually asserted.
            "extraction_drops": 0, "coverage_recovered": 0,
            #: Research plans: how many subject groups were formed, how many
            #: query slots were not spent because another claim in the group had
            #: already asked the same question, and what the pre-filter did.
            #: `pairs_skipped` is printed beside `pairs_examined` on purpose: a
            #: cheaper run that hides what it never looked at is the failure
            #: this repository exists to remove.
            "plans": 0, "queries_shared": 0,
            "pairs_examined": 0, "pairs_skipped": 0,
        }
        #: Pages this run already tried and could not read, by canonical URL.
        #: The planner derives several queries per claim, so one paywalled page
        #: used to be extracted once per query and reported once per attempt —
        #: three identical failures for one document, and a counter that said
        #: more sources failed than the run ever asked for. `extracted` is the
        #: memory of pages that worked; this is its twin for pages that did not.
        self._unreadable: set[str] = set()

    # -- plumbing ---------------------------------------------------------
    def emit(self, event_type: str, payload: dict[str, Any] | None = None
             ) -> dict[str, Any] | None:
        """Append one rail row and hand it back.

        The row is returned so a trace span can record *which* event reports it.
        A span that cannot name its own event is a second account of the run.
        """
        try:
            return self.store.append_event(self.run_id, event_type, payload or {})
        except Exception as exc:  # noqa: BLE001 — an event failure is logged, not thrown
            log.warning("could not append run event",
                        extra={"data": {"event": event_type, "error": str(exc)[:200]}})
            return None

    def fail(self, stage: str, message: str, **detail: Any) -> None:
        entry = {"stage": stage, "message": message, **detail}
        self.failures.append(entry)
        self.emit("run.failure", entry)
        log.warning("verification stage failed",
                    extra={"data": {"run_id": self.run_id, **entry}})

    # -- coverage ---------------------------------------------------------
    @staticmethod
    def _claim_key(text: Any) -> str:
        return re.sub(r"\W+", " ", str(text or "").casefold()).strip()

    def _emit_drops(self, drops: list[dict[str, Any]], *, stage: str) -> None:
        for drop in drops:
            self.emit("claim.dropped", {"stage": stage, **drop})
        self.counters["extraction_drops"] += len(drops)

    def _store_claim(self, answer_id: Any, ordinal: int, draft: ClaimDraft,
                     *, extracted_by: str) -> dict:
        row = {
            "id": new_id(), "answer_id": answer_id,
            "run_id": self.run_id, "ordinal": ordinal,
            "claim_text": draft.claim_text,
            "span_start": draft.span_start, "span_end": draft.span_end,
            "subject": draft.subject, "predicate": draft.predicate,
            "object_raw": draft.object_raw,
            "object_normalized": draft.object_normalized,
            "qualifiers": draft.qualifiers,
            "claim_type": draft.claim_type.value,
            "time_sensitive": draft.time_sensitive,
            "volatility_class": draft.volatility_class.value,
            "temporal_scope": draft.temporal_scope,
            "extraction_confidence": draft.extraction_confidence,
            "status": ClaimStatus.PENDING.value, "reason_code": None,
            "extracted_by": extracted_by,
        }
        return self.store.add_claim(row)

    def _audit_and_repair(self, answer_id: str, answer_text: str,
                          stored: list[tuple[dict, ClaimDraft]], *,
                          budget_mode: str) -> Any:
        span = self.trace.begin("coverage", "audit")
        try:
            report = audit_coverage(answer_text, [row for row, _ in stored])
        except Exception as exc:  # noqa: BLE001 — coverage must never break a run
            self.trace.end(span, self.emit("coverage.failed", {
                "error": str(exc)[:400],
                "trace": traceback.format_exc()[-1200:],
                "detail": "the answer was not audited; claims still verify"}),
                error_type="audit_failed")
            return None
        event = self.emit("coverage.audited", {
            "run_id": self.run_id,
            "summary": report.summary(),
            "coverage": round(report.coverage, 4),
            "material_coverage": round(report.material_coverage, 4),
            "total_assertions": report.total_assertions,
            "checked": report.checked,
            "uncovered": report.uncovered,
            "dispositions": dict(report.dispositions),
            "repair_targets": len(report.repair_targets),
        })
        if (report.repair_targets
                and settings.COVERAGE_MAX_REPAIR_CALLS > 0
                and budget_mode != "offline"):
            report = self._repair_coverage(answer_id, answer_text, stored,
                                           report, parent=span)
        self._store_coverage(answer_id, report)
        self.trace.end(span, event,
                       decision_rule="coverage_audit",
                       decision_result=(f"checked {report.checked}/"
                                        f"{report.total_assertions}"))
        return report

    def _repair_coverage(self, answer_id: str, answer_text: str,
                         stored: list[tuple[dict, ClaimDraft]],
                         report: Any, *, parent: Any = None) -> Any:
        """One focused call over the sentences no claim covered.

        The call is capped by `COVERAGE_MAX_REPAIR_CALLS` (the caller checks it)
        and its proposals are filtered three ways before storage: they must be
        locatable in the answer, they must intersect a repair target, and they
        must not duplicate a claim the run already holds. A failure is an event,
        never a silently improved number.
        """
        targets = report.repair_targets[:settings.COVERAGE_MAX_TARGETS]
        span = self.trace.begin(
            "coverage", "repair", parent=parent,
            prompt_version=REPAIR_PROMPT_VERSION,
            requested_model=settings.BIFROST_MODEL_EXTRACTOR,
            tool_arguments={"targets": len(targets)})
        drops: list[dict[str, Any]] = []
        try:
            claims, _texts, receipt = extract_missing_claims(
                self.bifrost, answer=answer_text, targets=targets, drops=drops)
        except BifrostError as exc:
            report.repair_calls += 1
            self.counters["failed_calls"] += 1
            self.trace.end(span, self.emit("coverage.repair_failed",
                                           {"kind": exc.kind,
                                            "error": str(exc)[:400]}),
                           error_type=exc.kind)
            return report
        except ValueError as exc:
            report.repair_calls += 1
            self.trace.end(span, self.emit("coverage.repair_failed",
                                           {"kind": "bad_json",
                                            "error": str(exc)[:400]}),
                           error_type="bad_json")
            return report
        except Exception as exc:  # noqa: BLE001 — the audit is advisory
            report.repair_calls += 1
            self.counters["failed_calls"] += 1
            self.trace.end(span, self.emit("coverage.repair_failed",
                                           {"kind": "unexpected",
                                            "error": str(exc)[:400]}),
                           error_type="unexpected")
            return report

        self._emit_drops(drops, stage="repair_coverage")
        if receipt.get("status") == "snapshot":
            self.counters["replays"] += 1
            self.emit("model.snapshot", {
                "stage": "repair_coverage",
                "request_id": receipt.get("request_id"),
                "created_at": receipt.get("created_at")})

        known_keys = {self._claim_key(row.get("claim_text")) for row, _ in stored}
        known_spans = [(int(row.get("span_start") or -1),
                        int(row.get("span_end") or -1)) for row, _ in stored]
        accepted: list[ClaimDraft] = []
        for draft in claims:
            key = self._claim_key(draft.claim_text)
            if key in known_keys:
                self.emit("claim.dropped", {"stage": "repair_coverage",
                                            "reason": "duplicate_existing",
                                            "claim_text": draft.claim_text[:300]})
                continue
            if any(start <= draft.span_start and draft.span_end <= end
                   for start, end in known_spans):
                self.emit("claim.dropped", {"stage": "repair_coverage",
                                            "reason": "already_covered",
                                            "claim_text": draft.claim_text[:300]})
                continue
            if len(stored) + len(accepted) >= settings.CLAIM_MAX_PER_ANSWER:
                self.emit("claim.dropped", {"stage": "repair_coverage",
                                            "reason": "claim_limit",
                                            "claim_text": draft.claim_text[:300]})
                break
            known_keys.add(key)
            known_spans.append((draft.span_start, draft.span_end))
            accepted.append(draft)

        for draft in accepted:
            row = self._store_claim(answer_id, len(stored), draft,
                                    extracted_by="coverage_repair")
            stored.append((row, draft))
            self.emit("claim.recovered", {
                "claim_id": row["id"], "claim": draft.claim_text,
                "value": draft.object_raw,
                "span_start": draft.span_start, "span_end": draft.span_end})
        self.counters["coverage_recovered"] += len(accepted)

        fresh = apply_repair(report, answer_text, [row for row, _ in stored],
                             calls=1)
        fresh.repaired_claims = len(accepted)
        self.trace.end(span, self.emit("coverage.repaired", {
            "targets": len(targets), "proposed": len(claims),
            "stored": len(accepted),
            "summary": fresh.summary()}), **model_fields(receipt))
        return fresh

    def _store_coverage(self, answer_id: str, report: Any) -> None:
        stamp = iso(now())
        rows: list[dict] = []
        for item in report.items:
            row = item.as_row(run_id=self.run_id, answer_id=answer_id)
            row["id"] = new_id()
            row["created_at"] = stamp
            rows.append(row)
        try:
            self.store.replace_assertion_spans(self.run_id, rows)
            self.store.put_coverage_audit({
                "run_id": self.run_id, "answer_id": answer_id,
                **report.as_dict(),
                "created_at": stamp, "updated_at": stamp})
        except Exception as exc:  # noqa: BLE001 — a store failure is reported
            self.emit("coverage.store_failed", {"error": str(exc)[:300]})
        self.emit("coverage.recorded", {"summary": report.summary(),
                                        "spans": len(rows)})

    # -- entry point ------------------------------------------------------
    def execute(self) -> None:
        """Run the verification with the run's trace context attached.

        The context is attached here, not in `__init__`, so a Verifier that is
        built and never executed cannot leave a `traceparent` behind for the
        next operation on this thread; it is detached in `finally` so a
        crashing run cannot either. Everything else is `_execute`.
        """
        self._trace_token = self.otel.begin_run_context(self.trace.trace_id)
        try:
            self._execute()
        finally:
            self.otel.end_run_context(self._trace_token)

    def _execute(self) -> None:
        run = self.store.get_run(self.run_id)
        if not run:
            return
        answer = self.store.get_answer(str(run["answer_id"])) or {}
        answer_text = str(answer.get("answer_text") or "")
        generated_at = parse_iso(answer.get("generated_at"))
        started = self.emit("run.started", {
            "run_id": self.run_id, "answer_id": run["answer_id"],
            "rule_version": RULE_VERSION,
            "cache_mode": settings.CACHE_MODE,
            "generated_at": iso(generated_at),
            "gateway": self.bifrost.base_url,
        })
        self.trace.open(event=started, started_at=run.get("started_at"))
        self.store.update_run(self.run_id, status="running", stage="extract")

        # 1. claims
        drops: list[dict[str, Any]] = []
        span = self.trace.begin(
            "extract", "model_call",
            prompt_version=EXTRACT_PROMPT_VERSION,
            requested_model=settings.BIFROST_MODEL_EXTRACTOR)
        try:
            claims, receipt = extract_claims(
                self.bifrost, answer=answer_text,
                original_prompt=str(answer.get("prompt") or ""),
                skip_non_factual=bool(answer.get("skip_non_factual") or False),
                drops=drops)
        except BifrostError as exc:
            self.counters["failed_calls"] += 1
            self.trace.end(span, self.emit("model.failed",
                                           {"stage": "extract_claims",
                                            "kind": exc.kind,
                                            "error": str(exc)[:400]}),
                           error_type=exc.kind)
            self._finish("failed", stage="extract",
                         error={"code": "extraction_failed",
                                "message": str(exc)[:400], "kind": exc.kind})
            return
        except ValueError as exc:
            self.trace.end(span, self.emit("model.failed",
                                           {"stage": "extract_claims",
                                            "kind": "bad_json",
                                            "error": str(exc)[:400]}),
                           error_type="bad_json")
            self._finish("failed", stage="extract",
                         error={"code": "extraction_failed",
                                "message": f"model returned unusable JSON: {exc}"})
            return

        extracted_event = self.emit("claims.extracted", {"count": len(claims),
                                                         "model_call": receipt})
        self.trace.end(span, extracted_event, **model_fields(receipt))
        self._emit_drops(drops, stage="extract_claims")
        if receipt.get("status") == "snapshot":
            # A replayed extraction is not a fresh one. The event rail says so,
            # because a run whose gateway panel shows zero calls would otherwise
            # look like a run where no model was involved.
            self.counters["replays"] += 1
            self.emit("model.snapshot", {
                "stage": "extract_claims",
                "request_id": receipt.get("request_id"),
                "created_at": receipt.get("created_at"),
            })
        if not claims:
            self._finish("partial", stage="extract",
                         error={"code": "no_claims",
                                "message": "the answer contained no checkable "
                                           "factual claims"})
            return

        # 2. store claims in order
        stored: list[tuple[dict, ClaimDraft]] = []
        for ordinal, draft in enumerate(claims):
            stored.append((self._store_claim(run["answer_id"], ordinal, draft,
                                             extracted_by="extractor"), draft))
        self.counters["claims"] = len(stored)

        # 2b. coverage: every material assertion gets a record or a reason.
        # The audit runs before research so the gap is durable even when the
        # research stage later fails; the single repair pass is capped and its
        # claims are settled exactly like the first pass's.
        coverage = self._audit_and_repair(
            str(run["answer_id"]), answer_text, stored,
            budget_mode=str(run.get("budget_mode") or "balanced"))
        self.counters["claims"] = len(stored)

        # 3. research + settle, one subject group at a time. Claims that share a
        # subject share a plan: the queries are asked once and the pages they
        # return are settled against every claim in the group. A group of one is
        # a plan of one, so there is no second path for the common case.
        self.store.update_run(self.run_id, stage="research")
        extracted: dict[str, Any] = {}
        budget_mode = str(run.get("budget_mode") or "balanced")
        for plan in self._plan_groups(stored, budget_mode):
            for claim_row, draft in plan.claims:
                claim_span = self.trace.begin("claim", "verify_claim",
                                              claim_id=str(claim_row["id"]))
                try:
                    self._verify_claim(claim_row, draft, generated_at, extracted,
                                       claim_span, plan=plan)
                except Exception as exc:  # noqa: BLE001 — one claim must not end the run
                    log.warning("claim verification crashed",
                                extra={"data": {"claim_id": claim_row["id"],
                                                "error": str(exc)[:300]}})
                    self.trace.end(claim_span, self.emit("claim.failed", {
                        "claim_id": claim_row["id"],
                        "error": str(exc)[:400],
                        "trace": traceback.format_exc()[-1200:]}),
                        error_type=type(exc).__name__)
                    self.store.update_claim(
                        claim_row["id"], status=ClaimStatus.UNVERIFIABLE.value,
                        reason_code=ReasonCode.EXTRACTION_FAILED.value,
                        last_verified_at=iso(now()))
                    self.fail("settle", "claim verification crashed",
                              claim_id=claim_row["id"])
            self._close_plan(plan)

        # 4. finish
        if self.failures:
            self._finish("partial", stage="done")
        else:
            self._finish("completed", stage="done")

    # -- research plans ---------------------------------------------------
    def _plan_groups(self, stored: list[tuple[dict, ClaimDraft]],
                     budget_mode: str) -> list[ResearchPlan]:
        """Group claims by subject and open one plan per group.

        Grouping is by normalised subject because that is what makes two
        questions the same question: "Acme Pro price" and "Acme Pro costs" are
        one page and one search, not two of each. Claims with no subject are
        not grouped with each other - the only thing two such claims share is
        that nobody said what they are about.
        """
        groups: dict[str, list[tuple[dict, ClaimDraft]]] = {}
        for claim_row, draft in stored:
            key = _normalise_subject(draft.subject)
            if not key:
                key = f"claim:{claim_row['id']}"
            groups.setdefault(key, []).append((claim_row, draft))

        plans: list[ResearchPlan] = []
        for key, members in groups.items():
            queries, dropped = self._plan_queries(members, budget_mode)
            plan = ResearchPlan(
                group_key="" if key.startswith("claim:") else key,
                budget_mode=budget_mode, claims=members, queries=queries,
                queries_dropped=dropped)
            plan.span = self.trace.begin(
                "research", "plan",
                tool_arguments={"group": plan.group_key or "(no subject)",
                                "claims": len(members)})
            plan.event = self.emit("research.planned", {
                "group_key": plan.group_key,
                "claims": len(members),
                "claim_ids": [str(row["id"]) for row, _ in members],
                "queries": queries,
                "queries_dropped": dropped,
                "budget_mode": budget_mode,
            })
            if dropped:
                # The plan was truncated by the budget mode. A run that says so
                # can be judged; a run that quietly asks fewer questions looks
                # like a claim that had fewer questions to ask.
                self.emit("research.budget_exhausted", {
                    "group_key": plan.group_key,
                    "claims": len(members),
                    "queries_dropped": dropped,
                    "budget_mode": budget_mode,
                    "reason": "the budget mode caps queries per claim; the "
                              "refused questions are not asked, not hidden",
                })
            plans.append(plan)
        return plans

    def _plan_queries(self, members: list[tuple[dict, ClaimDraft]],
                      budget_mode: str) -> tuple[list[str], int]:
        """The union of the group's queries, in claim order, asked once each.

        The union can never exceed the sum of the per-claim plans, so sharing
        cannot cost more searches than not sharing. Each claim's own cap is
        still the policy's, because the plan asks *its* claims' questions.
        """
        queries: list[str] = []
        seen: set[str] = set()
        dropped = 0
        for _row, draft in members:
            wanted, refused = plan_queries_detail(draft, budget_mode)
            dropped += refused
            for query in wanted:
                key = query.casefold()
                if key in seen:
                    continue
                seen.add(key)
                queries.append(query)
        return queries, dropped

    def _close_plan(self, plan: ResearchPlan) -> None:
        """Store what the plan did, and close its span with its own event."""
        self.counters["plans"] += 1
        try:
            self.store.add_research_plan({
                "id": new_id(), "run_id": self.run_id,
                "group_key": plan.group_key,
                "claim_ids": [str(row["id"]) for row, _ in plan.claims],
                "queries": plan.queries,
                "queries_asked": plan.asked,
                "queries_dropped": plan.queries_dropped,
                "budget_mode": plan.budget_mode,
                "pairs_examined": plan.pairs_examined,
                "pairs_skipped": plan.pairs_skipped,
                "created_at": iso(now()),
            })
        except Exception as exc:  # noqa: BLE001 — losing the row must not lose the verdicts
            log.warning("research plan not stored",
                        extra={"data": {"group": plan.group_key,
                                        "error": str(exc)[:300]}})
            self.fail("research", "the research plan could not be stored")
        self.trace.end(
            plan.span, plan.event,
            decision_result=(f"{len(plan.asked)} asked · "
                             f"{plan.pairs_examined} examined · "
                             f"{plan.pairs_skipped} skipped"))

    # -- one claim --------------------------------------------------------
    def _verify_claim(self, claim_row: dict, draft: ClaimDraft,
                      generated_at: datetime | None,
                      extracted: dict[str, Any],
                      span: Any = None,
                      *, plan: ResearchPlan | None = None) -> None:
        claim_id = str(claim_row["id"])
        self.store.update_run(self.run_id, stage="research")
        self.emit("claim.started", {"claim_id": claim_id,
                                    "claim": draft.claim_text})

        run_meta = self.store.get_run(self.run_id) or {}
        budget_mode = run_meta.get("budget_mode") or "balanced"
        value_scope = draft.object_raw or ""
        facts: list[EvidenceFact] = []
        settled_revisions: set[str] = set()

        # L5: reuse research we already paid for. Only the locatable evidence
        # rows are copied onto this claim; the *verdict* is always recomputed
        # against this answer's generated_at, because a fact that was true in
        # September can be stale in November. Without the copy the new claim
        # would carry a verdict and no evidence trail.
        cached_evidence = self.cache.get_l5(
            subject=draft.subject,
            predicate=draft.predicate,
            qualifiers=draft.qualifiers,
            value_scope=value_scope)
        reuse_span = None
        reused: list[EvidenceFact] = []
        if cached_evidence:
            reuse_span = self.trace.begin("research", "reuse_evidence",
                                          parent=span, claim_id=claim_id)
            reused = self._reuse_evidence(claim_id, cached_evidence)
        if reused:
            facts.extend(reused)
            self.counters["evidence"] += len(reused)
            self.counters["settled"] += sum(1 for f in reused if f.settled)
            self.trace.end(reuse_span, self.emit("claim.evidence_reused", {
                "claim_id": claim_id, "evidence_count": len(reused),
                "evidence_ids": [f.evidence_id for f in reused],
                "reason": "the same claim was researched before; evidence rows "
                          "were copied and the temporal verdict recomputed"}),
                cache_status="cached",
                tool_result_ref=",".join(f.evidence_id for f in reused)[:200])
            decision = decide(claim_value=draft.object_raw,
                              answer_generated_at=generated_at, facts=facts)
            self._record(claim_row, draft, decision, facts, generated_at,
                         span=span)
            return
        # A cache entry whose evidence rows are gone is not a hit. The miss is
        # written down, because "we looked and there was nothing" and "we never
        # looked" are different claims about the run.
        self.trace.end(reuse_span, None, cache_status="miss")

        queries = plan.queries if plan is not None else plan_queries_detail(
            draft, budget_mode=budget_mode)[0]

        # Existing-source discovery: settle pages we already hold before
        # spending a Tavily credit. This is the first rung of the escalation
        # ladder; search happens only when stored text cannot answer. It runs
        # even in offline mode, where no query is allowed at all.
        existing = find_existing_revisions(
            self.store,
            subject=draft.subject,
            predicate=draft.predicate,
            value_scope=value_scope,
            limit=5)
        existing_ids = [str(ev["revision_id"]) for ev in existing
                        if str(ev["revision_id"]) not in settled_revisions]
        if existing_ids:
            stored_span = self.trace.begin("research", "existing_sources",
                                           parent=span, claim_id=claim_id,
                                           tool_result_ref=",".join(
                                               existing_ids)[:200])
            self.trace.end(stored_span, self.emit("claim.source_reused", {
                "claim_id": claim_id, "sources": len(existing_ids),
                "stage": "existing_sources"}))
            self.counters["sources"] += len(existing_ids)
            facts.extend(self._settle(claim_id, draft, existing_ids, span,
                                      plan=plan))
            settled_revisions.update(existing_ids)
            decision = decide(claim_value=draft.object_raw,
                              answer_generated_at=generated_at, facts=facts)
            if decision.status != ClaimStatus.UNVERIFIABLE:
                # Stored evidence already decides it; escalation would spend
                # credits to learn nothing.
                self._record(claim_row, draft, decision, facts, generated_at,
                             span=span)
                return

        if not queries:
            # No research is permitted (offline, or the claim yields no query).
            # If stored text was examined, report *that* outcome — "the sources
            # we hold do not establish it" is not the same statement as "we
            # never looked".
            if existing_ids:
                decision = decide(claim_value=draft.object_raw,
                                  answer_generated_at=generated_at, facts=facts)
                self._record(claim_row, draft, decision, facts, generated_at,
                             span=span)
            else:
                self._settle_empty(claim_row, draft,
                                   ReasonCode.NO_SOURCE,
                                   "No search query could be derived from this claim.",
                                   span=span)
            return

        revision_ids: list[str] = []
        for query in queries:
            if plan is not None and not plan.still_unasked(query):
                # Another claim in this group already paid for this search.
                self.counters["queries_shared"] += 1
                continue
            self.counters["queries"] += 1
            if plan is not None:
                plan.asked.append(query)
            search_span = self.trace.begin(
                "research", "search", parent=span, claim_id=claim_id,
                tool_name="tavily_search", tool_arguments={"query": query})
            try:
                outcome = self.tavily.search(query)
            except ResearchError as exc:
                self.counters["failed_calls" if exc.kind != "cache_miss" else "failed_sources"] += 1
                self.trace.end(search_span, self.emit("search.failed", {
                    "claim_id": claim_id, "query": query,
                    "kind": exc.kind, "error": str(exc)[:300]}),
                    error_type=exc.kind)
                self.fail("research", f"search failed: {exc}", claim_id=claim_id)
                continue
            self.trace.end(search_span, self.emit("search.done", {
                "claim_id": claim_id, "query": query,
                "status": outcome.status, "hits": len(outcome.hits),
                "request_id": outcome.request_id, "credits": outcome.credits}),
                cache_status=outcome.status,
                tool_result_ref=str(outcome.request_id or ""))
            for failed in outcome.failed:
                self.counters["failed_sources"] += 1
                self.emit("source.failed", {"claim_id": claim_id,
                                            "url": failed.url,
                                            "stage": "search",
                                            "error": failed.error})
            for hit in _dedupe_hits(outcome.hits)[:settings.TAVILY_MAX_URLS_PER_CLAIM]:
                canonical = canonicalize_url(hit.url)
                if canonical in extracted:
                    revision_ids.append(extracted[canonical].revision_id)
                    if (plan is not None
                            and extracted[canonical].revision_id
                            not in plan.revisions):
                        plan.revisions.append(extracted[canonical].revision_id)
                    continue
                if canonical in self._unreadable:
                    # Fetched once already in this run and reported unreadable.
                    # Asking again would buy the same failure twice and count
                    # one page as two sources.
                    continue
                extract_span = self.trace.begin(
                    "research", "extract", parent=span, claim_id=claim_id,
                    tool_name="tavily_extract",
                    tool_arguments={"url": hit.url})
                try:
                    extract = self.tavily.extract([hit.url])
                except ResearchError as exc:
                    self.counters["failed_sources"] += 1
                    self._unreadable.add(canonical)
                    self.trace.end(extract_span, self.emit("source.failed", {
                        "claim_id": claim_id, "url": hit.url,
                        "stage": "extract", "error": str(exc)[:300]}),
                        error_type=exc.kind)
                    self.fail("research", f"extraction failed: {exc}",
                              claim_id=claim_id)
                    continue
                # One page is one failure row. Tavily routinely reports the
                # fetch error *and* returns no text for the same URL, so this
                # used to emit two `source.failed` events for one page and count
                # it twice in `failed_sources`: the rail showed a single URL
                # with two different reasons, and the run claimed more failed
                # sources than it ever asked for. The upstream reason is the
                # cause, our empty read is the symptom, so the symptom is
                # recorded only when Tavily reports nothing.
                upstream = {f.url: f.error for f in extract.failed}
                content = extract.pages.get(hit.url) or next(
                    iter(extract.pages.values()), "")
                if content.strip():
                    # Text arrived: whatever the extraction metadata says, this
                    # page did not fail.
                    upstream.pop(hit.url, None)
                else:
                    upstream[hit.url] = (upstream.pop(hit.url, None)
                                         or "no text returned")
                for url, error in upstream.items():
                    self.counters["failed_sources"] += 1
                    self._unreadable.add(canonicalize_url(url))
                    self.trace.end(
                        extract_span if url == hit.url else None,
                        self.emit("source.failed", {
                            "claim_id": claim_id, "url": url,
                            "stage": "extract", "error": error}),
                        error_type=("empty_document"
                                    if error == "no text returned"
                                    else "extract_failed"))
                if not content.strip():
                    continue
                source = save_source(self.store, hit.url, hit.title)
                revision = save_revision(
                    self.store, source=source, content=content,
                    published_date=_as_date(hit.published_date),
                    published_date_source=("tavily_search_metadata"
                                           if hit.published_date else "unknown"),
                    extraction_method="tavily_extract",
                    tavily_request_id=extract.request_id,
                    fetch_outcome={"search_status": outcome.status,
                                   "extract_status": extract.status,
                                   "credits": outcome.credits + extract.credits})
                # A new source revision invalidates dependent L5 evidence
                # mappings: reuse eligible evidence, but recompute the
                # temporal verdict against the new text.
                if not revision.reused:
                    self.cache.invalidate_for_source_revision(
                        canonical_url=canonical,
                        content_hash=revision.content_hash)
                extracted[canonical] = revision
                revision_ids.append(revision.revision_id)
                if plan is not None and revision.revision_id not in plan.revisions:
                    plan.revisions.append(revision.revision_id)
                self.counters["sources"] += 1
                self.trace.end(extract_span, self.emit("source.stored", {
                    "claim_id": claim_id,
                    "url": revision.source["canonical_url"],
                    "revision_id": revision.revision_id,
                    "chars": len(revision.content),
                    "published_date": revision.revision.get("published_date"),
                    "status": extract.status}),
                    cache_status=extract.status,
                    tool_result_ref=revision.revision_id,
                    input_hash=revision.content_hash)

        # A shared plan's pages are settled against every claim in the group,
        # not only the claim that issued the search: that is the reuse the plan
        # exists for. `settled_revisions` still prevents judging the same page
        # twice for the same claim.
        candidates = plan.revisions if plan is not None else revision_ids
        new_revisions = [rid for rid in candidates
                         if rid not in settled_revisions]
        facts.extend(self._settle(claim_id, draft, new_revisions, span,
                                  plan=plan))
        # L5: Store settled evidence for future claim-evidence reuse
        evidence_list = []
        for f in facts:
            evidence_list.append({
                "evidence_id": f.evidence_id,
                "relationship": f.relationship.value,
                "quote_located": f.quote_located,
                "settled": f.settled,
                "event_date": f.event_date.isoformat() if f.event_date else None,
                "event_date_status": f.event_date_status,
                "candidate_value": f.candidate_value,
                "publication_date": f.publication_date.isoformat() if f.publication_date else None,
                "credibility": f.credibility,
                "retrieved_at": f.retrieved_at.isoformat() if f.retrieved_at else None,
            })
        self.cache.put_l5(
            subject=draft.subject,
            predicate=draft.predicate,
            evidence_list=evidence_list,
            qualifiers=draft.qualifiers,
            value_scope=draft.object_raw or "",
            claim_type=draft.claim_type.value,
            volatility_class=draft.volatility_class.value,
        )
        decision = decide(claim_value=draft.object_raw,
                          answer_generated_at=generated_at, facts=facts)
        self._record(claim_row, draft, decision, facts, generated_at, span=span)


    def _reuse_evidence(self, claim_id: str,
                        cached: list[dict]) -> list[EvidenceFact]:
        """Copy previously settled evidence onto a new claim.

        Reuse is not a shortcut around the gate: the copied rows keep the
        offsets, quote and event date they were settled with, and the caller
        still recomputes the verdict. `model_call_id` is NULL on the copy
        because no model call produced this link in this run — writing the old
        request id would claim a call that did not happen.
        """
        facts: list[EvidenceFact] = []
        for ev in cached:
            old = self.store.get_evidence(str(ev.get("evidence_id") or ""))
            if not old:
                # The referenced evidence is gone (different store, pruned
                # cache). Fall through to live research rather than inventing
                # a fact with no row behind it.
                continue
            relationship = Relationship.parse(ev.get("relationship"))
            settled = bool(ev.get("settled"))
            row = self.store.add_evidence({
                "id": new_id(), "claim_id": claim_id,
                "source_revision_id": old.get("source_revision_id"),
                "quote_text": old.get("quote_text") or "",
                "start_char": int(old.get("start_char")
                                  if old.get("start_char") is not None else -1),
                "end_char": int(old.get("end_char")
                                if old.get("end_char") is not None else -1),
                "event_date": old.get("event_date"),
                "event_date_quote": old.get("event_date_quote"),
                "event_date_status": old.get("event_date_status") or "missing",
                "retrieved_at": old.get("retrieved_at") or iso(now()),
            })
            self.store.add_link({
                "id": new_id(), "claim_id": claim_id,
                "evidence_id": row["id"],
                "relationship_proposed": relationship.value,
                "relationship_settled": relationship.value if settled else None,
                "settlement_status": "accepted" if settled else "rejected",
                "settlement_reason": ("" if settled else
                                      "reused from a prior verification where "
                                      "this proposal was rejected"),
                "settlement_start": old.get("start_char") if settled else None,
                "settlement_end": old.get("end_char") if settled else None,
                "candidate_value_raw": ev.get("candidate_value"),
                "candidate_value_normalized": None,
                "model_call_id": None,
                "created_at": iso(now()),
            })
            event_dt = parse_iso(ev.get("event_date"))
            publication_dt = parse_iso(ev.get("publication_date"))
            facts.append(EvidenceFact(
                evidence_id=str(row["id"]),
                relationship=relationship,
                quote_located=bool(ev.get("quote_located")),
                settled=settled,
                event_date=event_dt.date() if event_dt else None,
                event_date_status=str(ev.get("event_date_status", "missing")),
                candidate_value=ev.get("candidate_value"),
                publication_date=(publication_dt.date()
                                  if publication_dt else None),
                credibility=str(ev.get("credibility", "secondary")),
                retrieved_at=parse_iso(ev.get("retrieved_at")),
            ))
        return facts

    def _settle(self, claim_id: str, draft: ClaimDraft,
                revision_ids: list[str], parent: Any = None,
                *, plan: ResearchPlan | None = None) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        seen: set[str] = set()
        terms = _claim_terms(draft)
        for revision_id in revision_ids:
            if revision_id in seen:
                continue
            seen.add(revision_id)
            revision = self.store.get_revision(revision_id)
            if not revision:
                continue
            source = self.store.get_source(str(revision["source_id"])) or {}
            content = str(revision.get("content") or "")
            url = str(source.get("canonical_url") or "")

            # Deterministic pre-filter, before the model is asked anything. It
            # may only skip a pair whose text shares no term with the claim's
            # subject or value; every skip is counted, traced and reported, so
            # a cheaper run cannot look like a run that examined everything.
            if not _shares_term(content, terms):
                self.counters["pairs_skipped"] += 1
                if plan is not None:
                    plan.pairs_skipped += 1
                skip_span = self.trace.begin(
                    "research", "prefilter", parent=parent, claim_id=claim_id,
                    tool_name="stored_revision", tool_result_ref=revision_id)
                self.trace.end(skip_span, self.emit("evidence.prefiltered", {
                    "claim_id": claim_id, "url": url,
                    "revision_id": revision_id,
                    "reason": "the document shares no term with the claim's "
                              "subject or value, so no model call was made"}),
                    decision_rule="prefilter", decision_result="skipped")
                continue

            self.counters["pairs_examined"] += 1
            if plan is not None:
                plan.pairs_examined += 1
            span = self.trace.begin(
                "research", "judge_evidence", parent=parent, claim_id=claim_id,
                prompt_version=PROPOSE_PROMPT_VERSION,
                requested_model=settings.BIFROST_MODEL_JUDGE,
                tool_name="stored_revision", tool_result_ref=revision_id,
                input_hash=str(revision.get("content_hash") or ""))
            proposal = propose(self.bifrost, claim=draft, content=content,
                               url=url, title=str(source.get("title") or ""),
                               claim_id=claim_id)
            if proposal.model_call.get("status") == "snapshot":
                self.counters["replays"] += 1
                # The rail must be able to say *when* this answer came from. A
                # replay labelled only "snapshot" tells a reader the run is not
                # live, but not that the evidence behind it is a day old.
                self.emit("model.snapshot", {
                    "claim_id": claim_id, "stage": "propose_relationship",
                    "created_at": proposal.model_call.get("created_at"),
                    "provider": proposal.model_call.get("provider"),
                    "model": proposal.model_call.get("model"),
                    "request_id": proposal.model_call.get("request_id")})
            if proposal.error.startswith("model_failed"):
                self.counters["failed_calls"] += 1
                self.trace.end(span, self.emit("model.failed", {
                    "claim_id": claim_id, "stage": "propose_relationship",
                    "error": proposal.error[:300]}), error_type="model_failed")
                self.fail("settle", proposal.error, claim_id=claim_id)

            if proposal.relationship == Relationship.SILENT:
                self.trace.end(span, self.emit("evidence.skipped", {
                    "claim_id": claim_id, "url": url,
                    "reason": proposal.error or "the document does not address it"}),
                    **model_fields(proposal.model_call),
                    error_type=(proposal.error or "")[:80])
                continue

            settlement = None
            if proposal.relationship == Relationship.SUPPORTS:
                settlement = settle_assertion(
                    subject=draft.subject, predicate=draft.predicate,
                    value_raw=draft.object_raw, quote=proposal.quote,
                    source_text=content)
            elif proposal.relationship == Relationship.CONTRADICTS:
                if proposal.candidate_value_raw:
                    settlement = settle_assertion(
                        subject=draft.subject, predicate=draft.predicate,
                        value_raw=proposal.candidate_value_raw,
                        quote=proposal.quote, source_text=content,
                        must_name_subject=True, object_text=draft.object_raw or "")
                else:
                    # Contradiction by explicit negation: settle the negation
                    # itself, so the contradiction is still evidence-backed.
                    lowered = proposal.quote.lower()
                    cue = next((c for c in _NEGATIONS if c in lowered), "")
                    if cue:
                        settlement = settle_assertion(
                            subject=draft.subject, predicate=draft.predicate,
                            value_raw=cue, quote=proposal.quote,
                            source_text=content, must_name_subject=True,
                            object_text=draft.object_raw or "")
                    else:
                        settlement = None

            settled = bool(settlement and settlement.ok)
            published = _as_date(revision.get("published_date"))
            event = event_date_from_quote(proposal.quote, content, published)
            start = settlement.start if settled else proposal.start
            end = settlement.end if settled else proposal.end
            quote_text = (content[start:end] if start is not None and end is not None
                          else proposal.quote)
            evidence = self.store.add_evidence({
                "id": new_id(), "claim_id": claim_id,
                "source_revision_id": revision_id,
                "quote_text": quote_text,
                "start_char": int(start if start is not None else -1),
                "end_char": int(end if end is not None else -1),
                "event_date": event.value.isoformat() if event.value else None,
                "event_date_quote": event.quote,
                "event_date_status": event.status,
                "retrieved_at": revision.get("retrieved_at") or iso(now()),
            })
            self.store.add_link({
                "id": new_id(), "claim_id": claim_id,
                "evidence_id": evidence["id"],
                "relationship_proposed": proposal.relationship.value,
                # The two columns answer two questions, and neither of them is
                # "was this any good": `relationship_settled` is *which*
                # relationship survived the gate (NULL when none did), and
                # `settlement_status` is whether the gate accepted the proposal.
                # They used to hold "settled"/"unsettled" and "settled"/
                # "not_settled", which put a settlement word in a relationship
                # column and a bare "settled" in both - so a reader querying
                # `relationship_settled = 'contradicts'` (the schema's own
                # vocabulary) matched nothing. The word `verified` is
                # deliberately not used here: it names the promotion chokepoint
                # on a cell, not the fate of a proposal.
                "relationship_settled": (proposal.relationship.value
                                         if settled else None),
                "settlement_status": "accepted" if settled else "rejected",
                "settlement_reason": ("" if settled else
                                     (settlement.reason if settlement else
                                      proposal.error or "the quote does not "
                                      "establish the asserted value")),
                "settlement_start": settlement.start if settled else None,
                "settlement_end": settlement.end if settled else None,
                "candidate_value_raw": proposal.candidate_value_raw or None,
                "candidate_value_normalized": None,
                "model_call_id": proposal.model_call.get("request_id") or None,
                "created_at": iso(now()),
            })
            self.counters["evidence"] += 1
            self.counters["settled"] += int(settled)
            self.trace.end(span, self.emit("evidence.settled", {
                "claim_id": claim_id, "evidence_id": evidence["id"],
                "url": url,
                "relationship": proposal.relationship.value,
                "settled": settled,
                "reason": "" if settled else (settlement.reason if settlement
                                              else "not established"),
                "event_date": event.value.isoformat() if event.value else None,
                "event_date_status": event.status,
                "quote": quote_text[:280]}),
                **model_fields(proposal.model_call),
                decision_rule=("gate" if settled else "gate_refused"),
                decision_result=proposal.relationship.value if settled else
                (settlement.reason if settlement else proposal.error or
                 "not_established")[:120])
            facts.append(EvidenceFact(
                evidence_id=str(evidence["id"]),
                relationship=proposal.relationship,
                quote_located=proposal.located,
                settled=settled,
                event_date=event.value,
                event_date_status=event.status,
                candidate_value=(proposal.candidate_value_raw
                                 if proposal.relationship == Relationship.CONTRADICTS
                                 else None),
                publication_date=published,
                credibility=_credibility(domain_of(url), draft.subject),
                retrieved_at=parse_iso(revision.get("retrieved_at")),
            ))
        return facts

    # -- recording --------------------------------------------------------
    def _record(self, claim_row: dict, draft: ClaimDraft, decision: Any,
                facts: list[EvidenceFact], generated_at: datetime | None,
                *, span: Any = None) -> None:
        claim_id = str(claim_row["id"])
        stamp = now()
        status = decision.status.value
        self.store.update_claim(
            claim_id, status=status, reason_code=decision.reason.value,
            last_verified_at=iso(stamp),
            next_check_at=iso(next_check_at(draft.volatility_class, stamp)))
        self.store.add_history({
            "id": new_id(), "claim_id": claim_id, "run_id": self.run_id,
            "previous_status": claim_row.get("status") or "pending",
            "new_status": status, "reason_code": decision.reason.value,
            "rule_version": RULE_VERSION, "decided_at": iso(stamp),
        })
        self.store.add_assertion({
            "id": new_id(), "claim_id": claim_id, "value_raw": draft.object_raw,
            "value_normalized": draft.object_normalized,
            "valid_from": iso(generated_at) if generated_at else None,
            "valid_until": None,
            "evidence_id": (decision.supporting[0] if decision.supporting else None),
            "is_original": True,
        })
        if status == ClaimStatus.CHANGED.value and decision.replacement_id:
            replacement = next((f for f in facts
                                if f.evidence_id == decision.replacement_id), None)
            if replacement is not None:
                self.store.add_assertion({
                    "id": new_id(), "claim_id": claim_id,
                    "value_raw": str(decision.material.get("replacement_value") or ""),
                    "value_normalized": {},
                    "valid_from": (replacement.event_date.isoformat()
                                   if replacement.event_date else None),
                    "valid_until": None,
                    "evidence_id": replacement.evidence_id,
                    "is_original": False,
                })
        settled_event = self.emit("claim.settled", {
            "claim_id": claim_id, "status": status,
            "reason_code": decision.reason.value, "rule": decision.rule,
            "explanation": decision.explanation,
            "supporting": list(decision.supporting),
            "contradicting": list(decision.contradicting),
            # A contradiction dropped at the scope gate is still worth telling
            # the operator about: it is why a claim with a disagreeing passage
            # can legitimately read `supported`.
            "scope_blocked": list(decision.material.get("scope_blocked") or []),
            "replacement_evidence_id": decision.replacement_id,
            "freshness_index": freshness_index(draft.volatility_class, stamp),
            "rule_version": RULE_VERSION,
        })
        # Corroboration is reported, never decisive. A reader should know that
        # three agreeing URLs are three copies of one press release before they
        # read "supported" as three independent confirmations.
        from app.verify.corroboration import assess_claim
        corroboration = assess_claim(self.store, claim_id, draft.subject)
        if corroboration["independence"]["source_count"]:
            self.emit("claim.corroboration", {"claim_id": claim_id,
                                              **corroboration})
        # The claim's span closes last, so its cost includes the corroboration
        # read and its `decision_rule` is the rule that actually produced the
        # status rather than the rule that was considered.
        self.trace.end(span, settled_event, decision_rule=decision.rule,
                       decision_result=status)

    def _settle_empty(self, claim_row: dict, draft: ClaimDraft,
                      reason: ReasonCode, explanation: str,
                      *, span: Any = None) -> None:
        from app.verify.decision import Decision
        decision = Decision(ClaimStatus.UNVERIFIABLE, reason, "R1", explanation)
        self._record(claim_row, draft, decision, [], None, span=span)

    # -- completion -------------------------------------------------------
    def _finish(self, status: str, *, stage: str,
                error: dict[str, Any] | None = None) -> None:
        stamp = now()
        self.store.update_run(self.run_id, status=status, stage=stage,
                              completed_at=iso(stamp), error=error)
        payload: dict[str, Any] = {
            "run_id": self.run_id, "status": status,
            "counters": dict(self.counters),
            "failures": self.failures[:50],
            "rule_version": RULE_VERSION,
            "completed_at": iso(stamp),
        }
        # The terminal event carries the reason the run ended. Without it a
        # `partial` run with no stage failures read as "finished partially, for
        # no stated reason", and a run that never produced a single claim looked
        # identical to one that merely lost a source.
        if error:
            payload["error"] = error
        try:
            coverage = self.store.get_coverage_audit(self.run_id)
        except Exception:  # noqa: BLE001 — a report field must not fail a run
            coverage = None
        if coverage:
            payload["coverage"] = {
                "total_assertions": coverage.get("total_assertions"),
                "checked": coverage.get("checked"),
                "coverage": coverage.get("coverage"),
                "material_coverage": coverage.get("material_coverage"),
                "uncovered": (coverage.get("dispositions") or {}).get(
                    "not_checked", 0),
                #: Sentences the deterministic scan could not read. The ratio
                #: cannot speak for them, so the terminal event names them.
                "unscanned": coverage.get("unscanned") or 0,
                "repair_calls": coverage.get("repair_calls"),
                "repaired_claims": coverage.get("repaired_claims"),
            }
        if status == "completed":
            terminal = self.emit("run.completed", payload)
        elif status == "failed":
            # A hard failure is not a partial run: nothing was verified at all.
            terminal = self.emit("run.failed", payload)
        else:
            terminal = self.emit("run.partial", payload)
        self.trace.finish(status, event=terminal)
        log.info("verification run finished",
                 extra={"data": {"run_id": self.run_id, "status": status,
                                 **self.counters}})
