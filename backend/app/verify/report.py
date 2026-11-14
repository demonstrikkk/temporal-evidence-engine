"""The evidence pack: the same rows the UI shows, as JSON and Markdown.

An export that re-asks a model to summarise the run is not an export. Everything
here comes from `claims`, `evidence_spans`, `source_revisions`,
`verification_events` and the execution trace, so the Markdown can be checked
against the database line by line.

Cost gets its own section because a single number cannot say what it is: a
gateway bill, a catalogue estimate and an unpriced span are three different
facts, and adding them produces a figure that is none of them.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.verify.store import VerifyStore, parse_iso
from app.verify.trace import summarise
from app.verify.freshness import freshness_index, next_check_at as _next_check_at

_STATUS_LABEL = {
    "supported": "Supported",
    "changed": "Changed",
    "contradicted": "Contradicted",
    "unverifiable": "Unverifiable",
    "pending": "Pending",
}


def _date_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = parse_iso(value)
    return parsed.date().isoformat() if parsed else str(value)[:10]


def evidence_view(row: dict[str, Any], store: VerifyStore) -> dict[str, Any]:
    """One shape for two adapters.

    The local adapter returns nested `link`/`revision`/`source`; the Postgres
    adapter returns one flattened join. Normalising here keeps the API and the
    exporter from learning both.
    """
    link = row.get("link") if isinstance(row.get("link"), dict) else row
    revision = row.get("revision") if isinstance(row.get("revision"), dict) else row
    source = row.get("source") if isinstance(row.get("source"), dict) else row
    revision_id = row.get("source_revision_id") or (revision or {}).get("id")
    if not source.get("canonical_url") and revision_id:
        full = store.get_revision(str(revision_id))
        if full:
            source = store.get_source(str(full.get("source_id"))) or {}
    return {
        "id": row.get("id"),
        "claim_id": row.get("claim_id"),
        "source_revision_id": revision_id,
        "quote_text": row.get("quote_text") or "",
        "start_char": row.get("start_char"),
        "end_char": row.get("end_char"),
        "event_date": row.get("event_date"),
        "event_date_quote": row.get("event_date_quote"),
        "event_date_status": row.get("event_date_status") or "missing",
        "source_url": source.get("canonical_url") or row.get("canonical_url") or "",
        "source_title": source.get("title") or row.get("title") or "",
        "publisher": source.get("domain") or row.get("domain") or "",
        "published_date": revision.get("published_date") or row.get("published_date"),
        "published_date_source": (revision.get("published_date_source")
                                  or row.get("published_date_source") or "unknown"),
        "retrieved_at": (row.get("retrieved_at")
                         or row.get("revision_retrieved_at")
                         or revision.get("retrieved_at")),
        "extraction_method": (revision.get("extraction_method")
                              or row.get("extraction_method") or ""),
        "relationship_proposed": str(link.get("relationship_proposed") or ""),
        "relationship_settled": link.get("relationship_settled"),
        "settlement_status": link.get("settlement_status"),
        "settlement_reason": link.get("settlement_reason"),
        "candidate_value_raw": link.get("candidate_value_raw"),
    }


def _coverage_lines(coverage: dict[str, Any], spans: list[dict[str, Any]],
                    answer_text: str) -> list[str]:
    lines = ["## Coverage", ""]
    total = int(coverage.get("total_assertions") or 0)
    checked = int(coverage.get("checked") or 0)
    ratio = float(coverage.get("coverage") or 0.0)
    material = float(coverage.get("material_coverage") or 0.0)
    lines.append(f"- Assertions: {checked} of {total} checked "
                 f"({ratio * 100:.1f}%)")
    lines.append(f"- Material coverage: {material * 100:.1f}% "
                 f"(opinions and predictions excluded)")
    lines.append(f"- Sentences: {coverage.get('sentences') or 0}")
    unscanned = int(coverage.get("unscanned") or 0)
    if unscanned:
        # Said next to the ratio, because the ratio does not include them.
        lines.append(f"- Not visible to the deterministic scan: {unscanned} "
                     f"sentence(s) with no checkable value and no claim")
    dispositions = coverage.get("dispositions") or {}
    if dispositions:
        lines.append("- Dispositions: " + ", ".join(
            f"`{name}` {count}" for name, count in sorted(dispositions.items())))
    if coverage.get("repair_calls"):
        lines.append(f"- Coverage repair: {coverage.get('repair_calls')} call(s), "
                     f"{coverage.get('repaired_claims') or 0} claim(s) recovered")
    uncovered = [span for span in spans
                 if span.get("disposition") == "not_checked"]
    if uncovered:
        lines.append("")
        lines.append(f"### Not checked ({len(uncovered)})")
        lines.append("")
        for span in uncovered[:20]:
            start = int(span.get("start_char") or 0)
            end = int(span.get("end_char") or 0)
            sentence = answer_text[start:end].strip() or str(span.get("text") or "")
            lines.append(f"- `{span.get('text')}` — “{sentence[:180]}”")
        if len(uncovered) > 20:
            lines.append(f"- …and {len(uncovered) - 20} more")
    unscanned_items = coverage.get("unscanned_sentences") or []
    if unscanned_items:
        lines.append("")
        lines.append(f"### Not visible to the scan ({len(unscanned_items)})")
        lines.append("")
        lines.append("These sentences contain no value the deterministic scanner "
                     "recognises and produced no claim. The ratios above do not "
                     "include them; they cannot say whether these sentences "
                     "assert anything.")
        lines.append("")
        for item in unscanned_items[:20]:
            text = str(item.get("text") or "").strip()
            lines.append(f"- “{text[:180]}”")
        if len(unscanned_items) > 20:
            lines.append(f"- …and {len(unscanned_items) - 20} more")
    lines.append("")
    return lines


def _cost_lines(cost: dict[str, Any]) -> list[str]:
    """Where the money went, and what kind of number each total is.

    A stage with no priced span prints an em dash. Writing `$0.0000` there would
    claim the work was free, when all that is known is that nobody priced it.
    """
    lines = ["## Cost", ""]
    totals = cost.get("totals") or {}
    if not cost.get("attributed"):
        lines.append("- No execution trace was recorded for this run, so cost "
                     "cannot be attributed by stage. The gateway total below "
                     "still applies.")
        lines.append("")
        return lines

    billed = float(totals.get("gateway_usd") or 0.0)
    estimated = float(totals.get("estimated_usd") or 0.0)
    gateway_spans = int(totals.get("gateway_spans") or 0)
    catalogue_spans = int(totals.get("catalogue_spans") or 0)
    unpriced = int(totals.get("unpriced_spans") or 0)
    if gateway_spans:
        lines.append(f"- Billed by the gateway: ${billed:.4f} "
                     f"({gateway_spans} priced span"
                     f"{'' if gateway_spans == 1 else 's'})")
    else:
        lines.append("- Billed by the gateway: none — no call in this run was "
                     "priced by a gateway")
    if catalogue_spans:
        lines.append(f"- Estimated from the catalogue: ${estimated:.4f} "
                     f"({catalogue_spans} priced span"
                     f"{'' if catalogue_spans == 1 else 's'})")
    else:
        lines.append("- Estimated from the catalogue: none — no call needed a "
                     "catalogue estimate")
    lines.append(f"- Unpriced spans: {unpriced} of "
                 f"{int(totals.get('spans') or 0)} — no price was recorded, "
                 f"which is not the same as free")
    if totals.get("snapshots"):
        lines.append(f"- Replayed from cache: {int(totals['snapshots'])}")
    lines.append("")

    stages = cost.get("stages") or []
    if stages:
        lines.append("| Stage | Spans | Tokens in | Tokens out | Billed | Estimated |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for bucket in stages:
            billed_cell = (f"${float(bucket.get('gateway_usd') or 0.0):.4f}"
                           if int(bucket.get("gateway_spans") or 0) else "—")
            estimated_cell = (f"${float(bucket.get('estimated_usd') or 0.0):.4f}"
                              if int(bucket.get("catalogue_spans") or 0) else "—")
            lines.append(
                f"| {bucket.get('stage')} | {int(bucket.get('spans') or 0)} | "
                f"{int(bucket.get('input_tokens') or 0)} | "
                f"{int(bucket.get('output_tokens') or 0)} | "
                f"{billed_cell} | {estimated_cell} |")
        lines.append("")
    lines.append("Cost is attributed by the execution trace: each span carries the "
                 "receipt of the model or tool call that caused it. Stage latency "
                 "is not printed because nested spans overlap, and a sum of them "
                 "is not the time the run took.")
    lines.append("")
    return lines


def _research_lines(research: dict[str, Any]) -> list[str]:
    """What was asked once, and what the pre-filter refused to send.

    Both counts are printed. "Examined 11" without "skipped 3" would read as a
    run that looked at everything, which is exactly the impression a cheaper
    run must not be able to give.
    """
    lines = ["## Research plan", ""]
    plans = research.get("plans") or []
    if not plans:
        lines.append("- No research plan was recorded for this run.")
        lines.append("")
        return lines

    asked = int(research.get("queries_asked") or 0)
    planned = int(research.get("queries_planned") or 0)
    examined = int(research.get("pairs_examined") or 0)
    skipped = int(research.get("pairs_skipped") or 0)
    lines.append(f"- Groups: {len(plans)}")
    lines.append(f"- Search queries asked: {asked} of {planned} planned"
                 + (" — the remainder were not run because every claim in the "
                    "group already had a verdict, or the budget refused them"
                    if asked < planned else ""))
    lines.append(f"- Claim/evidence pairs examined: {examined}")
    lines.append(f"- Pairs skipped by the pre-filter: {skipped} "
                 f"(a page sharing no term with the claim's subject or value "
                 f"is not sent to the model)")
    lines.append("")
    lines.append("| Group | Claims | Queries asked | Planned | Dropped | Examined | Skipped |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for plan in plans:
        lines.append(
            f"| {plan.get('group_key') or '(no subject)'} | "
            f"{len(plan.get('claim_ids') or [])} | "
            f"{len(plan.get('queries_asked') or [])} | "
            f"{len(plan.get('queries') or [])} | "
            f"{int(plan.get('queries_dropped') or 0)} | "
            f"{int(plan.get('pairs_examined') or 0)} | "
            f"{int(plan.get('pairs_skipped') or 0)} |")
    lines.append("")
    return lines


def _freshness_lines(claims: list[dict[str, Any]],
                     now: datetime | None = None) -> list[str]:
    """Per-claim freshness: index, next check, and staleness disclosure.

    Freshness is not a verdict — it says how old the evidence is relative to
    the claim's volatility class. A stale claim is not wrong; it is due for a
    recheck. The `now` parameter is injected so the report is reproducible.
    """
    from datetime import timezone
    now = now or datetime.now(timezone.utc)
    lines = ["## Freshness", ""]
    if not claims:
        lines.append("- No claims to assess.")
        lines.append("")
        return lines

    lines.append("| Claim | Volatility | Freshness | Next check | Due for recheck |")
    lines.append("| --- | --- | --- | --- | --- |")
    due_count = 0
    for claim in claims:
        claim_id = str(claim.get("id") or "")
        volatility = str(claim.get("volatility_class") or "moderate")
        last_verified_raw = claim.get("last_verified_at")
        last_verified = parse_iso(last_verified_raw) if last_verified_raw else None
        next_check = claim.get("next_check_at")

        index = freshness_index(volatility, last_verified, now=now)
        # `freshness_index` returns 0 for a claim that was never settled, but
        # "never checked" and "checked until worthless" are different states and
        # the report must not print a zero for the first one.
        index_text = ("never checked" if last_verified is None
                      else f"{index}")
        due = (next_check and parse_iso(next_check) <= now) if next_check else False
        if due:
            due_count += 1
        lines.append(
            f"| {claim_id[:8]}… | {volatility} | {index_text} | "
            f"{_date_text(next_check) if next_check else '—'} | "
            f"{'yes' if due else 'no'} |")

    if due_count:
        lines.append("")
        lines.append(f"- {due_count} claim(s) are due for a recheck: their evidence "
                     f"age has passed the next-check time for their volatility "
                     f"class. A stale claim is not wrong — it is due for a "
                     f"reverify.")
    lines.append("")
    return lines


def build_report(store: VerifyStore, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise KeyError(run_id)
    answer = store.get_answer(str(run["answer_id"])) or {}
    claims = store.list_claims(run_id)
    events = store.list_events(run_id)
    coverage = store.get_coverage_audit(run_id)
    spans = store.list_assertion_spans(run_id)
    trace_header = store.get_execution_trace(run_id)
    trace_spans = (store.list_trace_spans(str(trace_header["trace_id"]))
                   if trace_header else [])
    summary = summarise(trace_spans)
    cost = {
        "attributed": bool(trace_header),
        "trace_status": (trace_header or {}).get("status"),
        "totals": summary["totals"],
        "stages": summary["stages"],
    }
    plans = store.list_research_plans(run_id)
    research = {
        "plans": plans,
        "queries_planned": sum(len(p.get("queries") or []) for p in plans),
        "queries_asked": sum(len(p.get("queries_asked") or []) for p in plans),
        "queries_dropped": sum(int(p.get("queries_dropped") or 0) for p in plans),
        "pairs_examined": sum(int(p.get("pairs_examined") or 0) for p in plans),
        "pairs_skipped": sum(int(p.get("pairs_skipped") or 0) for p in plans),
    }
    claim_views = []
    for claim in claims:
        evidence = [evidence_view(row, store)
                    for row in store.list_evidence(str(claim["id"]))]
        claim_views.append({
            "id": claim["id"],
            "ordinal": claim.get("ordinal"),
            "claim_text": claim.get("claim_text"),
            "subject": claim.get("subject"),
            "predicate": claim.get("predicate"),
            "object_raw": claim.get("object_raw"),
            "object_normalized": claim.get("object_normalized") or {},
            "claim_type": claim.get("claim_type"),
            "time_sensitive": claim.get("time_sensitive"),
            "volatility_class": claim.get("volatility_class"),
            "status": claim.get("status"),
            "reason_code": claim.get("reason_code"),
            "extracted_by": claim.get("extracted_by") or "extractor",
            "span_start": claim.get("span_start"),
            "span_end": claim.get("span_end"),
            "last_verified_at": claim.get("last_verified_at"),
            "next_check_at": claim.get("next_check_at"),
            "evidence": evidence,
        })

    freshness = {
        "claims": claim_views,
    }

    return {
        "run": {
            "id": run["id"], "status": run.get("status"),
            "stage": run.get("stage"), "rule_version": run.get("rule_version"),
            "cache_mode": run.get("cache_mode"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "error": run.get("error"),
        },
        "answer": {
            "id": answer.get("id"), "prompt": answer.get("prompt"),
            "answer_text": answer.get("answer_text"),
            "generated_at": answer.get("generated_at"),
            "generated_at_source": answer.get("generated_at_source"),
        },
        "claims": claim_views,
        "coverage": {
            **(coverage or {}),
            "spans": [
                {
                    "kind": row.get("kind"),
                    "text": row.get("text"),
                    "start_char": row.get("start_char"),
                    "end_char": row.get("end_char"),
                    "disposition": row.get("disposition"),
                    "claim_id": row.get("claim_id"),
                }
                for row in spans
            ],
        },
        "events": events,
        "gateway": store.gateway_stats(run_id),
        "cost": cost,
        "research": research,
        "freshness": freshness,
        "markdown": render_markdown(run, answer, claim_views,
                                    store.gateway_stats(run_id),
                                    coverage=coverage, spans=spans,
                                    cost=cost, research=research,
                                    freshness=freshness),
    }


def render_markdown(run: dict[str, Any], answer: dict[str, Any],
                    claims: list[dict[str, Any]], gateway: dict[str, Any],
                    *, coverage: dict[str, Any] | None = None,
                    spans: list[dict[str, Any]] | None = None,
                    cost: dict[str, Any] | None = None,
                    research: dict[str, Any] | None = None,
                    freshness: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    lines.append("# Claim verification report")
    lines.append("")
    lines.append(f"- Run: `{run.get('id')}`")
    lines.append(f"- Status: **{run.get('status')}**")
    lines.append(f"- Rule version: `{run.get('rule_version')}`")
    lines.append(f"- Cache mode: `{run.get('cache_mode')}`")
    lines.append(f"- Answer generated: {answer.get('generated_at') or 'unknown'}")
    lines.append("")
    if answer.get("prompt"):
        lines.append("## Original question")
        lines.append("")
        lines.append(f"> {str(answer['prompt']).strip()[:2000]}")
        lines.append("")

    counts: dict[str, int] = {}
    for claim in claims:
        counts[str(claim.get("status"))] = counts.get(str(claim.get("status")), 0) + 1
    lines.append("## Summary")
    lines.append("")
    for key in ("supported", "changed", "contradicted", "unverifiable", "pending"):
        if key in counts:
            lines.append(f"- {_STATUS_LABEL.get(key, key)}: {counts[key]}")
    lines.append("")

    if coverage:
        lines.extend(_coverage_lines(coverage, spans or [],
                                     str(answer.get("answer_text") or "")))

    lines.append("## Claims")
    lines.append("")
    for index, claim in enumerate(claims, 1):
        status = str(claim.get("status"))
        lines.append(f"### {index}. {_STATUS_LABEL.get(status, status)} — "
                     f"`{claim.get('reason_code') or 'no reason code'}`")
        lines.append("")
        lines.append(f"> {claim.get('claim_text')}")
        lines.append("")
        lines.append(f"- Claimed value: `{claim.get('object_raw')}`")
        lines.append(f"- Verified at: {claim.get('last_verified_at') or '—'}")
        lines.append(f"- Next recommended check: {claim.get('next_check_at') or '—'}")
        lines.append("")
        evidence = claim.get("evidence") or []
        if not evidence:
            lines.append("_No source was attached to this claim._")
            lines.append("")
            continue
        for item in evidence:
            fate = item.get("settlement_status") or "rejected"
            relationship = item.get("relationship_settled") or "none"
            lines.append(f"**{item.get('source_title') or item.get('source_url')}** "
                         f"— {item.get('publisher')} — {fate} (`{relationship}`)")
            lines.append("")
            lines.append(f"- Proposal: `{item.get('relationship_proposed')}`")
            if item.get("settlement_reason"):
                lines.append(f"- Settlement: {item.get('settlement_reason')}")
            lines.append(f"- Published: {_date_text(item.get('published_date'))} "
                         f"({item.get('published_date_source')})")
            status_text = item.get("event_date_status") or "missing"
            lines.append(f"- Event date: {_date_text(item.get('event_date'))} "
                         f"({status_text})")
            lines.append(f"- Retrieved: {item.get('retrieved_at') or '—'}")
            lines.append(f"- Source: {item.get('source_url')}")
            if item.get("candidate_value_raw"):
                lines.append(f"- Replacement value: `{item.get('candidate_value_raw')}`")
            quote = str(item.get("quote_text") or "").strip()
            if quote:
                lines.append("")
                lines.append(f"> {quote}")
            lines.append("")

    if cost is not None:
        lines.extend(_cost_lines(cost))

    if research is not None:
        lines.extend(_research_lines(research))

    if freshness is not None:
        lines.extend(_freshness_lines(freshness.get("claims") or []))

    lines.append("## Gateway")
    lines.append("")
    lines.append(f"- Calls: {gateway.get('calls', 0)}")
    lines.append(f"- Input tokens: {gateway.get('input_tokens', 0)}")
    lines.append(f"- Output tokens: {gateway.get('output_tokens', 0)}")
    cost_line = f"- Cost: ${float(gateway.get('cost_usd') or 0):.4f}"
    if gateway.get("estimated_calls"):
        cost_line += (f" (includes {int(gateway['estimated_calls'])} call(s) "
                      f"priced from the model catalogue, which is an estimate "
                      f"rather than a bill)")
    lines.append(cost_line)
    lines.append(f"- Fallback attempts: {gateway.get('fallback_attempts', 0)}")
    providers = gateway.get("providers") or []
    lines.append(f"- Providers: {', '.join(providers) if providers else '—'}")
    lines.append("")
    lines.append("---")
    lines.append("Generated by Temporal Evidence Engine. A verdict is an "
                 "assessment of the cited sources, not a statement of absolute "
                 "truth.")
    return "\n".join(lines)
