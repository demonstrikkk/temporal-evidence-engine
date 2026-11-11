"""Deterministic citation and grounding validator for synthesized answers.

Validates that:
1. Every cited evidence ID exists in the compiled evidence pack.
2. No citation is invented or hallucinatory.
3. Numerical values in verified claims are preserved without alteration.
4. Calculates Grounding Score and Claim Coverage Score without an LLM judge.
5. Detects unsupported assertions: factual statements without citations.
6. Calculates Answer Completeness (C) and Grounding (G) scores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CITATION_RE = re.compile(r"\[(e_[a-zA-Z0-9_\-]+)\]")
_NUMERIC_RE = re.compile(r"\b(?:\$|€|£)?\s*[\d,]+(?:\.\d+)?\b")
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{4}\b",
    re.I,
)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    citations_used: list[str] = field(default_factory=list)
    grounding_score: float = 1.0
    coverage_score: float = 1.0
    completeness_score: float = 1.0
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    unsupported_assertions: list[str] = field(default_factory=list)


def _split_sentences(text: str) -> list[str]:
    """Split answer into sentences for unsupported-assertion detection."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _has_factual_content(sentence: str) -> bool:
    """A sentence is factual if it contains a number, date, currency, or
    capitalized entity reference."""
    return bool(
        _NUMERIC_RE.search(sentence)
        or _DATE_RE.search(sentence)
        or re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", sentence)
    )


def _numeric_tokens(text: str) -> set[str]:
    """Numbers as written, with currency and thousands separators removed.

    Substring matching (`"99" in "$199"`) is how a hallucinated price passes a
    check that was supposed to catch it, so tokens are compared exactly.
    """
    tokens: set[str] = set()
    for match in _NUMERIC_RE.findall(text or ""):
        cleaned = match.replace(",", "").strip(" $€£")
        if cleaned:
            tokens.add(cleaned)
    return tokens


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
    unverified_claims = evidence_context.get("unverifiable_claims", [])

    # 1. Validate citation existence
    invalid_citations = []
    for cid in found_citations:
        if cid not in known_sources:
            invalid_citations.append(cid)
            errors.append(f"Citation [{cid}] does not exist in compiled evidence pack.")

    # 2. Validate numeric consistency
    # Ensure numerical values from verified claims are not mangled
    answer_numbers = _numeric_tokens(answer_text)
    numeric_mismatches: list[str] = []
    for claim in verified_claims:
        raw_val = claim.get("value_raw") or ""
        for num in _numeric_tokens(raw_val):
            if num not in answer_numbers:
                # Value from verified claim missing from answer
                numeric_mismatches.append(num)
                errors.append(f"Expected numerical value '{num}' from claim '{claim['text']}' not found in answer.")

    # 3. Detect unsupported assertions: factual sentences without citations
    sentences = _split_sentences(answer_text)
    unsupported = [
        s for s in sentences
        if _has_factual_content(s) and not _CITATION_RE.search(s)
    ]
    for s in unsupported:
        errors.append(f"Unsupported factual statement: '{s[:100]}...'")

    # 4. Compute metrics
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

    # Answer completeness C: material questions addressed / material questions identified
    # We approximate: all verified claims should be addressed; unverifiable claims
    # should be acknowledged as unknown.
    material_claims = len(verified_claims) + len(unverified_claims)
    addressed = covered_claims
    completeness_score = round(addressed / material_claims, 3) if material_claims > 0 else 1.0

    is_valid = len(invalid_citations) == 0 and not numeric_mismatches

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        citations_used=found_citations,
        grounding_score=grounding_score,
        coverage_score=coverage_score,
        completeness_score=completeness_score,
        quality_metrics={
            "grounding_score": grounding_score,
            "coverage_score": coverage_score,
            "completeness_score": completeness_score,
            "total_citations": total_citations,
            "valid_citations": valid_citations,
            "invalid_citations": invalid_citations,
            "numeric_mismatches": numeric_mismatches,
            "verified_claims_count": total_claims,
            "covered_claims_count": covered_claims,
            "unverified_claims_count": len(unverified_claims),
            "unsupported_assertions": unsupported,
        },
        unsupported_assertions=unsupported,
    )
