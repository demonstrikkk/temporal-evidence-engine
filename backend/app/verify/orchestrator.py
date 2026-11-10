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
from datetime import date, datetime, timezone
from typing import Any

from app.bifrost.client import BifrostClient, BifrostError
from app.core.config import RULE_VERSION, settings
from app.core.logging import log
from app.research.planner import plan_queries
from app.research.sources import (canonicalize_url, domain_of, save_revision,
                                  save_source)
from app.research.tavily import ResearchError, TavilyClient
from app.verify.decision import EvidenceFact, decide
from app.verify.extract import extract_claims
from app.verify.freshness import freshness_index, next_check_at
from app.verify.relationship import Proposal, propose
from app.verify.schemas import (AnswerIn, ClaimDraft, ClaimStatus, ReasonCode,
                                Relationship, RunCreated)
from app.verify.settlement import settle_assertion
from app.verify.store import VerifyStore, build_store, iso, new_id, now, parse_iso
from app.verify.temporal import event_date_from_quote
from app.cache.coordinator import CacheCoordinator
from app.cache.keys import claim_fingerprint

#: Negation cues that can contradict a claim without naming a replacement value.
_NEGATIONS = ("no longer", "not available", "no longer available",
              "discontinued", "has ended", "has been discontinued",
              "was discontinued", "shut down", "has shut down", "deprecated",
              "removed from", "closed down", "out of stock", "sold out")

_ACTIVE_RUNS: set[str] = set()
_LOCK = threading.Lock()


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

def create_run(store: VerifyStore, payload: AnswerIn) -> RunCreated:
    answer_id = new_id()
    run_id = new_id()
    stamp = now()
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
    })
    store.create_run({
        "id": run_id, "answer_id": answer_id, "status": "queued",
        "stage": "queued", "rule_version": RULE_VERSION,
        "cache_mode": settings.CACHE_MODE, "started_at": iso(stamp),
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
        self.counters: dict[str, int] = {
            "claims": 0, "queries": 0, "sources": 0, "evidence": 0,
            "settled": 0, "failed_sources": 0, "failed_calls": 0,
            #: Model calls answered from the cache instead of the gateway. A run
            #: that replayed everything spent nothing and touched no provider,
            #: and saying so is the difference between a snapshot and a live run.
            "replays": 0,
        }

    # -- plumbing ---------------------------------------------------------
    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self.store.append_event(self.run_id, event_type, payload or {})
        except Exception as exc:  # noqa: BLE001 — an event failure is logged, not thrown
            log.warning("could not append run event",
                        extra={"data": {"event": event_type, "error": str(exc)[:200]}})

    def fail(self, stage: str, message: str, **detail: Any) -> None:
        entry = {"stage": stage, "message": message, **detail}
        self.failures.append(entry)
        self.emit("run.failure", entry)
        log.warning("verification stage failed",
                    extra={"data": {"run_id": self.run_id, **entry}})

    # -- entry point ------------------------------------------------------
    def execute(self) -> None:
        run = self.store.get_run(self.run_id)
        if not run:
            return
        answer = self.store.get_answer(str(run["answer_id"])) or {}
        answer_text = str(answer.get("answer_text") or "")
        generated_at = parse_iso(answer.get("generated_at"))
        self.emit("run.started", {
            "run_id": self.run_id, "answer_id": run["answer_id"],
            "rule_version": RULE_VERSION,
            "cache_mode": settings.CACHE_MODE,
            "generated_at": iso(generated_at),
            "gateway": self.bifrost.base_url,
        })
        self.store.update_run(self.run_id, status="running", stage="extract")

        # 1. claims
        try:
            claims, receipt = extract_claims(
                self.bifrost, answer=answer_text,
                original_prompt=str(answer.get("prompt") or ""),
                skip_non_factual=bool(answer.get("skip_non_factual") or False))
        except BifrostError as exc:
            self.counters["failed_calls"] += 1
            self.emit("model.failed", {"stage": "extract_claims",
                                       "kind": exc.kind, "error": str(exc)[:400]})
            self._finish("failed", stage="extract",
                         error={"code": "extraction_failed",
                                "message": str(exc)[:400], "kind": exc.kind})
            return
        except ValueError as exc:
            self._finish("failed", stage="extract",
                         error={"code": "extraction_failed",
                                "message": f"model returned unusable JSON: {exc}"})
            return

        self.emit("claims.extracted", {"count": len(claims),
                                       "model_call": receipt})
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
            row = {
                "id": new_id(), "answer_id": run["answer_id"],
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
            }
            stored.append((self.store.add_claim(row), draft))
        self.counters["claims"] = len(stored)

        # 3. research + settle, one claim at a time
        self.store.update_run(self.run_id, stage="research")
        extracted: dict[str, Any] = {}
        for claim_row, draft in stored:
            try:
                self._verify_claim(claim_row, draft, generated_at, extracted)
            except Exception as exc:  # noqa: BLE001 — one claim must not end the run
                log.warning("claim verification crashed",
                            extra={"data": {"claim_id": claim_row["id"],
                                            "error": str(exc)[:300]}})
                self.emit("claim.failed", {"claim_id": claim_row["id"],
                                           "error": str(exc)[:400],
                                           "trace": traceback.format_exc()[-1200:]})
                self.store.update_claim(
                    claim_row["id"], status=ClaimStatus.UNVERIFIABLE.value,
                    reason_code=ReasonCode.EXTRACTION_FAILED.value,
                    last_verified_at=iso(now()))
                self.fail("settle", "claim verification crashed",
                          claim_id=claim_row["id"])

        # 4. finish
        if self.failures:
            self._finish("partial", stage="done")
        else:
            self._finish("completed", stage="done")

    # -- one claim --------------------------------------------------------
    def _verify_claim(self, claim_row: dict, draft: ClaimDraft,
                      generated_at: datetime | None,
                      extracted: dict[str, Any]) -> None:
        claim_id = str(claim_row["id"])
        self.store.update_run(self.run_id, stage="research")
        self.emit("claim.started", {"claim_id": claim_id,
                                    "claim": draft.claim_text})

        run_meta = self.store.get_run(self.run_id) or {}
        budget_mode = run_meta.get("budget_mode") or "balanced"
        queries = plan_queries(draft, budget_mode=budget_mode)
        if not queries:
            self._settle_empty(claim_row, draft,
                               ReasonCode.NO_SOURCE,
                               "No search query could be derived from this claim.")
            return

        # L5: Check claim-evidence reuse cache before Tavily search
        value_scope = draft.object_raw or ""
        fp_key = claim_fingerprint(
            subject=draft.subject,
            predicate=draft.predicate,
            qualifiers=draft.qualifiers,
            value_scope=value_scope)
        cached_evidence = self.cache.get_l5(
            subject=draft.subject,
            predicate=draft.predicate,
            qualifiers=draft.qualifiers,
            value_scope=value_scope)
        revision_ids: list[str] = []
        facts: list[EvidenceFact] = []
        if cached_evidence:
            # Reuse cached evidence; recompute temporal verdict for this generated_at
            for ev in cached_evidence:
                try:
                    ev_date = None
                    if ev.get("event_date"):
                        ev_date = parse_iso(ev["event_date"])
                    pub_date = None
                    if ev.get("publication_date"):
                        pub_date = parse_iso(ev["publication_date"])
                    facts.append(EvidenceFact(
                        evidence_id=str(ev["evidence_id"]),
                        relationship=Relationship.parse(ev.get("relationship")),
                        quote_located=bool(ev.get("quote_located")),
                        settled=bool(ev.get("settled")),
                        event_date=ev_date,
                        event_date_status=str(ev.get("event_date_status", "missing")),
                        candidate_value=ev.get("candidate_value"),
                        publication_date=pub_date,
                        credibility=str(ev.get("credibility", "secondary")),
                        retrieved_at=parse_iso(ev.get("retrieved_at")),
                    ))
                    revision_ids.append(str(ev["evidence_id"]))
                except Exception:
                    continue
            self.emit("claim.evidence_reused", {"claim_id": claim_id,
                                                "evidence_count": len(facts)})
            self.counters["evidence"] += len(facts)
            decision = decide(claim_value=draft.object_raw,
                              answer_generated_at=generated_at, facts=facts)
            self._record(claim_row, draft, decision, facts, generated_at)
            return

        revision_ids: list[str] = []
        for query in queries:
            self.counters["queries"] += 1
            try:
                outcome = self.tavily.search(query)
            except ResearchError as exc:
                self.counters["failed_calls" if exc.kind != "cache_miss" else "failed_sources"] += 1
                self.emit("search.failed", {"claim_id": claim_id, "query": query,
                                            "kind": exc.kind, "error": str(exc)[:300]})
                self.fail("research", f"search failed: {exc}", claim_id=claim_id)
                continue
            self.emit("search.done", {
                "claim_id": claim_id, "query": query,
                "status": outcome.status, "hits": len(outcome.hits),
                "request_id": outcome.request_id, "credits": outcome.credits})
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
                    continue
                try:
                    extract = self.tavily.extract([hit.url])
                except ResearchError as exc:
                    self.counters["failed_sources"] += 1
                    self.emit("source.failed", {"claim_id": claim_id,
                                                "url": hit.url,
                                                "stage": "extract",
                                                "error": str(exc)[:300]})
                    self.fail("research", f"extraction failed: {exc}",
                              claim_id=claim_id)
                    continue
                for failed in extract.failed:
                    self.counters["failed_sources"] += 1
                    self.emit("source.failed", {"claim_id": claim_id,
                                                "url": failed.url,
                                                "stage": "extract",
                                                "error": failed.error})
                content = extract.pages.get(hit.url) or next(
                    iter(extract.pages.values()), "")
                if not content.strip():
                    self.emit("source.failed", {"claim_id": claim_id,
                                                "url": hit.url,
                                                "stage": "extract",
                                                "error": "no text returned"})
                    self.counters["failed_sources"] += 1
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
                extracted[canonical] = revision
                revision_ids.append(revision.revision_id)
                self.counters["sources"] += 1
                self.emit("source.stored", {
                    "claim_id": claim_id, "url": revision.source["canonical_url"],
                    "revision_id": revision.revision_id,
                    "chars": len(revision.content),
                    "published_date": revision.revision.get("published_date"),
                    "status": extract.status})

        facts = self._settle(claim_id, draft, revision_ids)
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
        self._record(claim_row, draft, decision, facts, generated_at)

    def _settle(self, claim_id: str, draft: ClaimDraft,
                revision_ids: list[str]) -> list[EvidenceFact]:
        facts: list[EvidenceFact] = []
        seen: set[str] = set()
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
                self.emit("model.failed", {"claim_id": claim_id,
                                           "stage": "propose_relationship",
                                           "error": proposal.error[:300]})
                self.fail("settle", proposal.error, claim_id=claim_id)

            if proposal.relationship == Relationship.SILENT:
                self.emit("evidence.skipped", {
                    "claim_id": claim_id, "url": url,
                    "reason": proposal.error or "the document does not address it"})
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
                        must_name_subject=True)
                else:
                    # Contradiction by explicit negation: settle the negation
                    # itself, so the contradiction is still evidence-backed.
                    lowered = proposal.quote.lower()
                    cue = next((c for c in _NEGATIONS if c in lowered), "")
                    if cue:
                        settlement = settle_assertion(
                            subject=draft.subject, predicate=draft.predicate,
                            value_raw=cue, quote=proposal.quote,
                            source_text=content, must_name_subject=True)
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
            self.emit("evidence.settled", {
                "claim_id": claim_id, "evidence_id": evidence["id"],
                "url": url,
                "relationship": proposal.relationship.value,
                "settled": settled,
                "reason": "" if settled else (settlement.reason if settlement
                                              else "not established"),
                "event_date": event.value.isoformat() if event.value else None,
                "event_date_status": event.status,
                "quote": quote_text[:280]})
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
                facts: list[EvidenceFact], generated_at: datetime | None) -> None:
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
        self.emit("claim.settled", {
            "claim_id": claim_id, "status": status,
            "reason_code": decision.reason.value, "rule": decision.rule,
            "explanation": decision.explanation,
            "supporting": list(decision.supporting),
            "contradicting": list(decision.contradicting),
            "replacement_evidence_id": decision.replacement_id,
            "freshness_index": freshness_index(draft.volatility_class, stamp),
            "rule_version": RULE_VERSION,
        })

    def _settle_empty(self, claim_row: dict, draft: ClaimDraft,
                      reason: ReasonCode, explanation: str) -> None:
        from app.verify.decision import Decision
        decision = Decision(ClaimStatus.UNVERIFIABLE, reason, "R1", explanation)
        self._record(claim_row, draft, decision, [], None)

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
        if status == "completed":
            self.emit("run.completed", payload)
        elif status == "failed":
            # A hard failure is not a partial run: nothing was verified at all.
            self.emit("run.failed", payload)
        else:
            self.emit("run.partial", payload)
        log.info("verification run finished",
                 extra={"data": {"run_id": self.run_id, "status": status,
                                 **self.counters}})
