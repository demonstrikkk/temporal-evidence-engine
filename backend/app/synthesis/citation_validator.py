"""Deterministic citation and grounding validator for synthesized answers.

Validates that:
1. Every cited evidence ID exists in the compiled evidence pack.
2. No citation is invented or hallucinatory.
3. Numerical values in verified claims are preserved without alteration.
4. Calculates Grounding Score and Claim Coverage Score without an LLM judge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CITATION_RE = re.compile(r"\[(e_[a-zA-Z0-9_\-]+)\]")
_NUMERIC_RE = re.compile(r"\b(?:\$|€|£)?\s*[\d,]+(?:\.\d+)?\b")


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    citations_used: list[str] = field(default_factory=list)
    grounding_score: float = 1.0
    coverage_score: float = 1.0
    quality_metrics: dict[str, Any] = field(default_factory=dict)


def validate_citations(
    answer_text: str,
    evidence_context: dict[str, Any],
) -> ValidationResult:
    """Deterministically validate answer citations and numerical consistency."""
    errors: list[str] = []
    found_citations = _CITATION_RE.findall(answer_text)

    # Valid evidence ID lookup
    known_sources = {s["evidence_id"]: s for s in evidence_context.get("sources", [])}
    verified_claims = evidence_context.get("verified_claims", [])

    # 1. Validate citation existence
    invalid_citations = []
    for cid in found_citations:
        if cid not in known_sources:
            invalid_citations.append(cid)
            errors.append(f"Citation [{cid}] does not exist in compiled evidence pack.")

    # 2. Validate numeric consistency
    # Ensure numerical values from verified claims are not mangled
    for claim in verified_claims:
        raw_val = claim.get("value_raw") or ""
        numbers = _NUMERIC_RE.findall(raw_val)
        for num in numbers:
            clean_num = num.strip(" $€£,")
            if clean_num and clean_num not in answer_text:
                # Value from verified claim missing from answer
                errors.append(f"Expected numerical value '{num}' from claim '{claim['text']}' not found in answer.")

    # 3. Compute metrics
    total_citations = len(found_citations)
    valid_citations = total_citations - len(invalid_citations)
    grounding_score = round(valid_citations / total_citations, 3) if total_citations > 0 else 0.0

    total_claims = len(verified_claims)
    # Check claim coverage: how many verified claims have at least one of their evidence IDs cited
    covered_claims = 0
    for claim in verified_claims:
        ev_ids = set(claim.get("evidence_ids", []))
        if ev_ids.intersection(found_citations):
            covered_claims += 1
        elif any(term.lower() in answer_text.lower() for term in (claim.get("subject"), claim.get("predicate")) if term):
            covered_claims += 1

    coverage_score = round(covered_claims / total_claims, 3) if total_claims > 0 else 1.0
    is_valid = len(invalid_citations) == 0

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        citations_used=found_citations,
        grounding_score=grounding_score,
        coverage_score=coverage_score,
        quality_metrics={
            "grounding_score": grounding_score,
            "coverage_score": coverage_score,
            "total_citations": total_citations,
            "valid_citations": valid_citations,
            "invalid_citations": invalid_citations,
            "verified_claims_count": total_claims,
            "covered_claims_count": covered_claims,
        },
    )
