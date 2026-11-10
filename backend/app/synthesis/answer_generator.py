"""Evidence-grounded answer generator calling Bifrost with validation and repair.

Transforms researched claims and settled quotes into a coherent, responsive answer
with inline citation markers, guarded by deterministic validation and safe fallback.
"""
from __future__ import annotations

import json
from typing import Any

from app.bifrost.client import BifrostClient, BifrostError
from app.core.config import settings
from app.core.logging import log
from app.synthesis.citation_validator import ValidationResult, validate_citations
from app.synthesis.context_compiler import compile_evidence_context
from app.synthesis.fallback_renderer import render_deterministic_fallback

_SYNTHESIS_SYSTEM = """You are a senior factual research assistant.
You produce concise, well-structured, evidence-grounded answers based STRICTLY
on the provided evidence pack.

Operating rules:
1. Answer the user's specific question directly in the opening paragraph.
2. Every factual statement MUST cite its evidence ID using bracket format, e.g. [e_001].
3. ONLY cite evidence IDs that are explicitly listed in the Evidence Sources below.
4. Preserve numbers, dates, and currency units exactly as given.
5. If a fact changed over time, state the effective date from the evidence.
6. If a claim is unverifiable or missing evidence, state clearly what is unknown.
7. NEVER invent citations, URLs, dates, or numbers.
"""


def _build_synthesis_prompt(context: dict[str, Any]) -> str:
    question = context.get("user_question") or "Research Question"
    as_of = context.get("answer_as_of") or "current"
    verified = context.get("verified_claims") or []
    unverifiable = context.get("unverifiable_claims") or []
    sources = context.get("sources") or []

    prompt_lines = [
        f"USER QUESTION: {question}",
        f"AS OF DATE: {as_of}",
        "\nVERIFIED CLAIMS TO GROUND ANSWER ON:",
    ]
    for c in verified:
        ev_str = ", ".join(c.get("evidence_ids", []))
        prompt_lines.append(f"- [{c['status'].upper()}] {c['text']} (Available evidence IDs: {ev_str})")

    if unverifiable:
        prompt_lines.append("\nUNVERIFIABLE CLAIMS (Do not state as established fact):")
        for c in unverifiable:
            prompt_lines.append(f"- {c['text']} (Reason: {c.get('reason_code')})")

    prompt_lines.append("\nEVIDENCE SOURCES (Only cite these IDs):")
    for s in sources:
        prompt_lines.append(
            f"[{s['evidence_id']}] Quote: \"{s['quote']}\" | Event Date: {s.get('event_date') or 'unspecified'} | Source: {s['source_title']}"
        )

    prompt_lines.append("\nProvide a clear, cohesive answer citing the evidence IDs in brackets:")
    return "\n".join(prompt_lines)


def generate_grounded_answer(
    bifrost: BifrostClient,
    context: dict[str, Any],
) -> tuple[str, ValidationResult]:
    """Synthesize an evidence-backed answer with validation and single repair."""
    prompt = _build_synthesis_prompt(context)

    # 1. Primary synthesis call
    try:
        result = bifrost.complete(
            prompt,
            stage="synthesize_answer",
            system=_SYNTHESIS_SYSTEM,
            temperature=0.1,
            max_tokens=1500,
            json_mode=False,
        )
        answer_text = result.text.strip()
    except BifrostError as exc:
        log.warning("synthesis model call failed; using deterministic fallback",
                    extra={"data": {"error": str(exc)[:200]}})
        fallback = render_deterministic_fallback(context)
        val = validate_citations(fallback, context)
        return fallback, val

    # 2. Deterministic citation validation
    val = validate_citations(answer_text, context)
    if val.is_valid:
        return answer_text, val

    # 3. Targeted repair attempt (single budget-capped repair)
    log.info("synthesis validation failed; attempting single repair",
             extra={"data": {"errors": val.errors}})
    repair_prompt = (
        f"Your previous answer contained citation errors:\n"
        f"{json.dumps(val.errors, indent=2)}\n\n"
        f"PREVIOUS DRAFT:\n{answer_text}\n\n"
        f"Please rewrite the answer so that EVERY citation strictly references one of: "
        f"{[s['evidence_id'] for s in context.get('sources', [])]}. "
        f"Do not invent any other citation IDs."
    )
    try:
        repaired = bifrost.complete(
            repair_prompt,
            stage="repair_answer",
            system=_SYNTHESIS_SYSTEM,
            temperature=0.0,
            max_tokens=1500,
            json_mode=False,
        )
        repaired_text = repaired.text.strip()
        repaired_val = validate_citations(repaired_text, context)
        if repaired_val.is_valid:
            return repaired_text, repaired_val
    except Exception as exc:
        log.warning("synthesis repair failed", extra={"data": {"error": str(exc)[:200]}})

    # 4. Safe fallback if repair failed
    log.info("using safe deterministic fallback response")
    fallback = render_deterministic_fallback(context)
    fallback_val = validate_citations(fallback, context)
    return fallback, fallback_val
