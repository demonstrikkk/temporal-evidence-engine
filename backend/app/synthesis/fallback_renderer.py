"""Deterministic fallback renderer for answer synthesis.

Renders an honest, well-structured evidence summary directly from verified claims
and exact source quotes when model generation is unavailable or fails validation.
"""
from __future__ import annotations

from typing import Any


def render_deterministic_fallback(context: dict[str, Any]) -> str:
    """Deterministically assemble an answer directly from the evidence pack."""
    user_q = context.get("user_question") or "Research Summary"
    verified = context.get("verified_claims") or []
    unverifiable = context.get("unverifiable_claims") or []
    sources = {s["evidence_id"]: s for s in context.get("sources") or []}

    lines: list[str] = []

    if verified:
        lines.append("### Verified Findings")
        for c in verified:
            ev_markers = "".join(f" [{eid}]" for eid in c.get("evidence_ids", []) if eid in sources)
            status_tag = f"**[{c['status'].upper()}]**" if c.get("status") else ""
            lines.append(f"- {status_tag} {c['text']}{ev_markers}")
        lines.append("")

    if unverifiable:
        lines.append("### Unresolved or Unverifiable Statements")
        for c in unverifiable:
            reason = f" ({c['reason_code']})" if c.get("reason_code") else ""
            lines.append(f"- *{c['text']}*{reason}")
        lines.append("")

    if sources:
        lines.append("### Evidence Trail")
        for eid, s in sources.items():
            date_info = f" (Event date: {s['event_date']})" if s.get("event_date") else ""
            lines.append(f"- **[{eid}]** *\"{s['quote']}\"*{date_info} — [{s['source_title']}]({s['url']})")

    return "\n".join(lines).strip()
