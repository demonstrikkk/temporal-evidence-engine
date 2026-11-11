"""The evidence pack: the same rows the UI shows, as JSON and Markdown.

An export that re-asks a model to summarise the run is not an export. Everything
here comes from `claims`, `evidence_spans`, `source_revisions` and
`verification_events`, so the Markdown can be checked against the database line
by line.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.verify.store import VerifyStore, parse_iso

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
        "markdown": render_markdown(run, answer, claim_views,
                                    store.gateway_stats(run_id),
                                    coverage=coverage, spans=spans),
    }


def render_markdown(run: dict[str, Any], answer: dict[str, Any],
                    claims: list[dict[str, Any]], gateway: dict[str, Any],
                    *, coverage: dict[str, Any] | None = None,
                    spans: list[dict[str, Any]] | None = None) -> str:
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

    lines.append("## Gateway")
    lines.append("")
    lines.append(f"- Calls: {gateway.get('calls', 0)}")
    lines.append(f"- Input tokens: {gateway.get('input_tokens', 0)}")
    lines.append(f"- Output tokens: {gateway.get('output_tokens', 0)}")
    lines.append(f"- Cost: ${float(gateway.get('cost_usd') or 0):.4f}")
    lines.append(f"- Fallback attempts: {gateway.get('fallback_attempts', 0)}")
    providers = gateway.get("providers") or []
    lines.append(f"- Providers: {', '.join(providers) if providers else '—'}")
    lines.append("")
    lines.append("---")
    lines.append("Generated by Temporal Evidence Engine. A verdict is an "
                 "assessment of the cited sources, not a statement of absolute "
                 "truth.")
    return "\n".join(lines)
