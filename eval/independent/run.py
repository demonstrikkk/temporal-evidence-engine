"""Independent assertion-coverage evaluation.

This suite measures one thing and refuses to blend it with the other two
measurements the repository already has:

* **verdict accuracy** — `eval/run_eval.py` (do the rules assign the expected
  status to the claims it is given?);
* **scanner-visible coverage** — `scripts/coverage_probe.py` (how much of the
  answer does the deterministic value scanner see?);
* **independent assertion recall** — this file (how many material assertions
  found by a human reader does the scanner see, including assertions the
  scanner was never built for?).

The ground truth in `v1/annotations.jsonl` was written from the sentence, not
from scanner output. It has no `kind`, no expected disposition and no expected
match field. The runner imports the scanner only to measure it.

Hermetic: no model, no Tavily, no database, no sockets. Cost is zero. The run
is a measurement, not a release gate: it exits 0 on a valid dataset.

Usage (from the repository root):

    backend\\.venv\\Scripts\\python.exe eval\\independent\\run.py
    backend\\.venv\\Scripts\\python.exe eval\\independent\\run.py --json
    backend\\.venv\\Scripts\\python.exe eval\\independent\\run.py --out result.json
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import RULE_VERSION  # noqa: E402
from app.verify.assertions import (  # noqa: E402
    candidates as all_candidates,
    disposition_for,
    sentence_candidates,
)
from app.verify.coverage import audit  # noqa: E402
from app.verify.segment import segment  # noqa: E402

VERSION = (HERE / "VERSION").read_text(encoding="utf-8").strip()
DATASET = HERE / "v1" / "annotations.jsonl"

#: The twelve shapes the milestone asked for. Every one must appear as a case
#: category, otherwise the benchmark is narrower than it claims.
REQUIRED_CASE_CATEGORIES: tuple[str, ...] = (
    "dates-historical",
    "measurements-quantities",
    "currency-units",
    "named-entities",
    "relationships-ownership",
    "comparisons",
    "rankings-superlatives",
    "compound",
    "non-numeric",
    "subjective",
    "predictions",
    "ambiguous",
)

#: Controlled vocabulary for the semantic value of one assertion. These are
#: what the annotation says the value *is*, not what the scanner calls it.
VALUE_TYPES: tuple[str, ...] = (
    "date", "measurement", "quantity", "currency", "money", "percentage",
    "count", "duration", "version", "none",
)

ASSERTION_CATEGORIES: tuple[str, ...] = (
    "date", "measurement", "quantity", "currency", "percentage", "count",
    "duration", "version", "entity", "relationship", "ownership", "location",
    "comparison", "ranking", "property",
)

ASSERTION_TYPES: tuple[str, ...] = (
    "factual", "subjective", "predictive", "ambiguous",
)

#: A scanner kind is a syntactic guess; a value type is semantic. A bare
#: `number` is compatible with a year, a count or an unlabelled quantity, so
#: those agreements are not overstated as failures. `version` is compatible
#: only with a version, which is why `1.5 million` is a real mismatch.
_KIND_COMPATIBLE: dict[str, frozenset[str]] = {
    "money": frozenset({"money", "currency"}),
    "percent": frozenset({"percentage"}),
    "measure": frozenset({"measurement", "quantity", "duration"}),
    "date": frozenset({"date"}),
    "version": frozenset({"version"}),
    "number": frozenset({"date", "count", "quantity", "duration"}),
}

_SCALE_WORDS = ("thousand", "million", "billion", "trillion")
_WORD_NUMBERS = ("one", "two", "three", "four", "five", "six", "seven",
                 "eight", "nine", "ten", "eleven", "twelve", "twenty",
                 "thirty", "forty", "fifty", "hundred")

#: D-031's four documented shapes. `documented` is copied from DECISIONS.md,
#: not from the scanner: this is the observation the suite checks.
D031: tuple[dict[str, str], ...] = (
    {"text": "7 million", "documented": "no candidate",
     "annotation_id": "ind-19-a02"},
    {"text": "1.5 million", "documented": "version: 1.5",
     "annotation_id": "ind-20-a01"},
    {"text": "3 billion", "documented": "no candidate",
     "annotation_id": "ind-21-a01"},
    {"text": "10,100 tonnes", "documented": "measure: 10,100 tonnes",
     "annotation_id": "ind-22-a01"},
)

#: Pinned production measurements. These are the numbers the regression test
#: also pins, so the report and the test cannot describe different behaviour.
PRODUCTION_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "name": "gold_eiffel_two_claims",
        "answer": ("The Eiffel Tower is 330 metres tall. It was completed in "
                   "1889 for the World's Fair in Paris."),
        "claims": [
            {"id": "c1", "span_start": 0, "span_end": 40},
            {"id": "c2", "span_start": 41, "span_end": 72},
        ],
    },
    {
        "name": "uncovered_measure",
        "answer": "The tower is 330 metres tall.",
        "claims": [],
    },
    {
        "name": "no_value_no_claim",
        "answer": "The tower is in Paris.",
        "claims": [],
    },
    {
        "name": "unscanned_sentence",
        "answer": "The tower is 330 metres tall. It is in Paris.",
        "claims": [{"id": "c1", "span_start": 0, "span_end": 29}],
    },
)


# ---------------------------------------------------------------------------
# dataset loading and validation
# ---------------------------------------------------------------------------

def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{number}: not JSON: {exc}") from exc
    if not cases:
        raise ValueError(f"{path}: no cases")
    errors: list[str] = []
    for case in cases:
        errors.extend(validate_case(case))
    if errors:
        raise ValueError("\n".join(errors))
    return cases


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_case(case: dict[str, Any]) -> list[str]:
    """Every declaration must resolve against the answer exactly.

    The validator deliberately checks only the annotation's own consistency.
    It never compares an annotation with a scanner candidate: doing that here
    would make dataset construction depend on the thing being measured.
    """
    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(f"{case.get('id', '<no id>')}: {message}")

    if not isinstance(case.get("id"), str) or not case["id"]:
        fail("missing id")
    if case.get("dataset_version") != VERSION:
        fail(f"dataset_version {case.get('dataset_version')!r} != {VERSION!r}")
    if case.get("category") not in REQUIRED_CASE_CATEGORIES:
        fail(f"unknown case category {case.get('category')!r}")
    provenance = case.get("provenance")
    if not isinstance(provenance, dict):
        fail("missing provenance")
    else:
        for key in ("authored", "basis", "independence"):
            if not provenance.get(key):
                fail(f"provenance.{key} is empty")
        if provenance.get("scanner_output_used") is not False:
            fail("provenance must state scanner_output_used: false")

    answer = case.get("answer")
    if not isinstance(answer, str) or not answer:
        fail("missing answer")
        return errors
    sentences = segment(answer)
    assertions = case.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        fail("missing assertions")
        return errors

    seen: set[str] = set()
    for assertion in assertions:
        label = assertion.get("id", "<no id>")
        if not isinstance(assertion.get("id"), str) or not assertion["id"]:
            fail("assertion without id")
            continue
        if assertion["id"] in seen:
            fail(f"duplicate assertion id {assertion['id']}")
        seen.add(assertion["id"])
        if assertion.get("input_id") != case["id"]:
            fail(f"{label}: input_id does not name this case")
        for key in ("sentence", "text", "subject", "property", "normalized",
                    "rationale", "category", "value_type", "assertion_type"):
            if not isinstance(assertion.get(key), str) or not assertion[key].strip():
                fail(f"{label}: {key} is empty")
        if assertion.get("category") not in ASSERTION_CATEGORIES:
            fail(f"{label}: unknown category {assertion.get('category')!r}")
        if assertion.get("value_type") not in VALUE_TYPES:
            fail(f"{label}: unknown value_type {assertion.get('value_type')!r}")
        if assertion.get("assertion_type") not in ASSERTION_TYPES:
            fail(f"{label}: unknown assertion_type {assertion.get('assertion_type')!r}")
        if not isinstance(assertion.get("material"), bool):
            fail(f"{label}: material must be boolean")
        if not isinstance(assertion.get("in_scope"), bool):
            fail(f"{label}: in_scope must be boolean")

        index = assertion.get("sentence_index")
        if not _is_int(index) or not 0 <= index < len(sentences):
            fail(f"{label}: sentence_index {index!r} out of range")
            continue
        if assertion.get("sentence") != sentences[index].text:
            fail(f"{label}: sentence does not match segmentation at index {index}")

        start, end = assertion.get("span_start"), assertion.get("span_end")
        if not _is_int(start) or not _is_int(end) or not 0 <= start < end <= len(answer):
            fail(f"{label}: span {start!r}:{end!r} is invalid")
            continue
        if answer[start:end] != assertion.get("text"):
            fail(f"{label}: span does not resolve to the declared text")
        sentence = sentences[index]
        if not (sentence.start <= start and end <= sentence.end):
            fail(f"{label}: span is not inside the declared sentence")

        value_text = assertion.get("value_text")
        value_start = assertion.get("value_span_start")
        value_end = assertion.get("value_span_end")
        if value_text is None:
            if value_start is not None or value_end is not None:
                fail(f"{label}: value span without value_text")
            if assertion.get("value_type") != "none":
                fail(f"{label}: value_type {assertion.get('value_type')!r} without value_text")
        else:
            if not isinstance(value_text, str) or not value_text:
                fail(f"{label}: value_text is empty")
                continue
            if not _is_int(value_start) or not _is_int(value_end):
                fail(f"{label}: missing value span")
                continue
            if not (start <= value_start < value_end <= end):
                fail(f"{label}: value span is not inside the assertion span")
                continue
            if answer[value_start:value_end] != value_text:
                fail(f"{label}: value span does not resolve to value_text")
            if assertion.get("value_type") == "none":
                fail(f"{label}: value_text present with value_type 'none'")
        if assertion.get("unit") is not None and not isinstance(assertion["unit"], str):
            fail(f"{label}: unit must be a string or null")
        if assertion.get("value_type") in {"money", "measurement", "percentage",
                                           "count", "duration", "quantity"}:
            if not assertion.get("unit"):
                fail(f"{label}: unit missing for {assertion.get('value_type')}")
    return errors


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def _intersects(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and a[1] > b[0]


def _contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def relation(candidate: tuple[int, int],
             value: tuple[int, int] | None) -> str:
    """`exact`, `partial`, or `none`.

    `partial` covers both directions of proper containment, because both are
    wrong in the same way: a value the reader asserted is only partly captured.
    `$19` inside `$19 billion` and `1.5` inside `1.5 million` are partial.
    Two spans that overlap without containing each other are also partial; the
    suite never awards exact credit for an offset approximation.
    """
    if value is None:
        return "none"
    if candidate == value:
        return "exact"
    if _contains(value, candidate) or _contains(candidate, value):
        return "partial"
    if _intersects(candidate, value):
        return "partial"
    return "none"


@dataclass(frozen=True)
class AnnotationView:
    raw: dict[str, Any]
    span: tuple[int, int]
    value_span: tuple[int, int] | None
    sentence: str
    sentence_index: int


@dataclass(frozen=True)
class CandidateView:
    kind: str
    text: str
    start: int
    end: int
    sentence_index: int

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


def _annotation_views(case: dict[str, Any]) -> list[AnnotationView]:
    return [
        AnnotationView(
            raw=assertion,
            span=(assertion["span_start"], assertion["span_end"]),
            value_span=(
                (assertion["value_span_start"], assertion["value_span_end"])
                if assertion["value_text"] is not None else None),
            sentence=assertion["sentence"],
            sentence_index=assertion["sentence_index"],
        )
        for assertion in case["assertions"]
    ]


def _candidate_views(answer: str) -> list[CandidateView]:
    found: list[CandidateView] = []
    for index, sentence in enumerate(segment(answer)):
        for candidate in sentence_candidates(sentence.start, sentence.end,
                                             sentence.text):
            found.append(CandidateView(
                kind=candidate.kind, text=candidate.text,
                start=candidate.start, end=candidate.end,
                sentence_index=index))
    return found


def _kind_agrees(kind: str, value_type: str) -> bool:
    return value_type in _KIND_COMPATIBLE.get(kind, frozenset())


def _has_scale_word(text: str | None) -> bool:
    lowered = (text or "").casefold()
    return any(word in lowered for word in _SCALE_WORDS)


def _has_word_number(text: str | None) -> bool:
    lowered = (text or "").casefold()
    return any(word in lowered.split() for word in _WORD_NUMBERS)


def _digits_only(text: str) -> bool:
    return bool(text) and all(character.isdigit() or character in ",." for
                              character in text)


def classify_failure(annotation: AnnotationView, match: str,
                     partial: list[CandidateView], merged: bool,
                     disposition: str) -> str | None:
    """One deterministic class per missed or partial annotation.

    The order is the diagnosis order, not a severity order. Every class names
    an observable fact (no candidate, a version kind on a magnitude, a missing
    unit) so a reader can disagree with the label and see why it was chosen.
    """
    raw = annotation.raw
    if not raw["in_scope"]:
        return "not_a_failure"
    if match == "exact":
        return None
    if merged:
        return "compound_splitting"
    if raw["material"] and disposition in {"subjective", "prediction"}:
        return "materiality_classification"
    if match == "partial":
        if any(candidate.kind == "version" for candidate in partial) and \
                _has_scale_word(raw["value_text"]):
            return "version_vs_quantity"
        if _has_scale_word(raw["value_text"]):
            return "numeric_recognition"
        if any(candidate.kind == "version" for candidate in partial) and \
                raw["value_type"] in {"measurement", "quantity", "count",
                                      "money", "currency"}:
            return "version_vs_quantity"
        if any(_digits_only(candidate.text) for candidate in partial) and \
                raw["unit"]:
            return "unit_normalization"
        return "numeric_recognition"

    if raw["value_text"] is None:
        if raw["category"] == "entity":
            return "entity_recognition"
        if raw["category"] in {"relationship", "ownership", "location"}:
            return "relationship_extraction"
        if raw["category"] in {"comparison", "ranking", "property"}:
            return "property_classification"
        return "property_classification"

    if _has_scale_word(raw["value_text"]) or _has_word_number(raw["value_text"]) \
            or any(character.isdigit() for character in raw["value_text"]):
        return "numeric_recognition"
    return "property_classification"


# ---------------------------------------------------------------------------
# per-case analysis
# ---------------------------------------------------------------------------

def analyse_case(case: dict[str, Any]) -> dict[str, Any]:
    answer = case["answer"]
    annotations = _annotation_views(case)
    scanner = _candidate_views(answer)

    # Pairwise relations first, so a candidate can be exact for one annotation
    # and partial for an overlapping one without losing either fact.
    exact_for_candidate: dict[tuple[int, int], list[str]] = {}
    partial_for_candidate: dict[tuple[int, int], list[str]] = {}
    for annotation in annotations:
        for candidate in scanner:
            rel = relation(candidate.span, annotation.value_span)
            if rel == "exact":
                exact_for_candidate.setdefault(candidate.span, []).append(
                    annotation.raw["id"])
            elif rel == "partial":
                partial_for_candidate.setdefault(candidate.span, []).append(
                    annotation.raw["id"])

    value_spans = [a.value_span for a in annotations if a.value_span is not None]
    merged_spans: set[tuple[int, int]] = set()
    candidate_rows: list[dict[str, Any]] = []
    for candidate in scanner:
        exact = exact_for_candidate.get(candidate.span, [])
        partial = partial_for_candidate.get(candidate.span, [])
        if exact:
            verdict = "exact"
        elif partial:
            verdict = "partial"
        else:
            verdict = "false"
        merged = False
        if not exact:
            touched_values = [span for span in value_spans
                              if _intersects(candidate.span, span)]
            merged = len(set(touched_values)) >= 2
            if merged:
                merged_spans.add(candidate.span)
        candidate_rows.append({
            "kind": candidate.kind,
            "text": candidate.text,
            "span_start": candidate.start,
            "span_end": candidate.end,
            "sentence_index": candidate.sentence_index,
            "verdict": verdict,
            "exact_for": exact,
            "partial_for": partial,
            "merged": merged,
            "shared_value": len(exact) > 1,
        })

    annotation_rows: list[dict[str, Any]] = []
    for annotation in annotations:
        raw = annotation.raw
        related = [
            (candidate, relation(candidate.span, annotation.value_span))
            for candidate in scanner
            if relation(candidate.span, annotation.value_span) != "none"
        ]
        exact = [candidate for candidate, rel in related if rel == "exact"]
        partial = [candidate for candidate, rel in related if rel == "partial"]
        touched = [candidate for candidate in scanner
                   if _intersects(candidate.span, annotation.span)]
        if exact:
            match = "exact"
        elif partial:
            match = "partial"
        elif touched:
            match = "touched"
        else:
            match = "miss"

        disposition = disposition_for(annotation.sentence, covered=False)
        merged = any(candidate.span in merged_spans for candidate in touched)
        failure = classify_failure(annotation, match, partial, merged, disposition)
        shared = any(len(exact_for_candidate.get(candidate.span, [])) > 1
                     for candidate in exact)
        kind_mismatch = False
        if match == "exact" and exact:
            kind_mismatch = not _kind_agrees(exact[0].kind, raw["value_type"])
            if kind_mismatch and failure is None:
                failure = "context_interpretation"

        annotation_rows.append({
            "id": raw["id"],
            "input_id": raw["input_id"],
            "case_category": case["category"],
            "sentence_index": annotation.sentence_index,
            "sentence": annotation.sentence,
            "text": raw["text"],
            "span_start": annotation.span[0],
            "span_end": annotation.span[1],
            "value_text": raw["value_text"],
            "value_type": raw["value_type"],
            "unit": raw["unit"],
            "category": raw["category"],
            "material": raw["material"],
            "in_scope": raw["in_scope"],
            "assertion_type": raw["assertion_type"],
            "match": match,
            "failure_class": failure,
            "candidates_seen": [
                {"kind": candidate.kind, "text": candidate.text,
                 "relation": ("overlap" if annotation.value_span is None
                              else relation(candidate.span, annotation.value_span))}
                for candidate in touched],
            "scanner_kind": exact[0].kind if exact else (
                partial[0].kind if partial else (
                    touched[0].kind if touched else None)),
            "value_type_agreement": (
                _kind_agrees(exact[0].kind, raw["value_type"])
                if match == "exact" and exact else None),
            "shared_value": shared,
            "compound_context": False,
            "disposition_if_no_claim": disposition,
            "false_excuse": bool(raw["in_scope"] and raw["material"]
                                 and disposition in {"subjective", "prediction"}
                                 and match != "exact"),
        })

    # Compound context is a property of the sentence, not of the annotation:
    # "this one was missed while a sibling value was found".
    by_sentence: dict[int, list[dict[str, Any]]] = {}
    for row in annotation_rows:
        by_sentence.setdefault(row["sentence_index"], []).append(row)
    for rows in by_sentence.values():
        material = [row for row in rows if row["material"] and row["in_scope"]]
        if len(material) < 2:
            continue
        any_exact = any(row["match"] == "exact" for row in material)
        for row in material:
            if row["match"] != "exact" and any_exact:
                row["compound_context"] = True

    material_rows = [row for row in annotation_rows
                     if row["material"] and row["in_scope"]]
    exact_rows = [row for row in material_rows if row["match"] == "exact"]
    partial_rows = [row for row in material_rows if row["match"] == "partial"]
    touched_rows = [row for row in material_rows if row["match"] == "touched"]
    missed_rows = [row for row in material_rows if row["match"] == "miss"]

    exact_candidates = [row for row in candidate_rows if row["verdict"] == "exact"]
    partial_candidates = [row for row in candidate_rows if row["verdict"] == "partial"]
    false_candidates = [row for row in candidate_rows if row["verdict"] == "false"]
    shared_candidates = [row for row in candidate_rows if row["shared_value"]]

    no_claims = audit(answer, [])
    compound_claim = [{"id": "compound", "span_start": 0, "span_end": len(answer)}]
    one_claim = audit(answer, compound_claim)

    sentences_with_material = sorted({row["sentence_index"] for row in material_rows})
    fully_covered = [
        index for index in sentences_with_material
        if all(row["match"] == "exact" for row in material_rows
               if row["sentence_index"] == index)
    ]
    touched_sentences = [
        index for index in sentences_with_material
        if any(row["match"] != "miss" for row in material_rows
               if row["sentence_index"] == index)
    ]

    compound_sentences = {
        index: rows for index, rows in by_sentence.items()
        if len([row for row in rows if row["material"] and row["in_scope"]]) >= 2
    }
    value_sentences: dict[int, list[dict[str, Any]]] = {}
    for index, rows in compound_sentences.items():
        value_rows = [row for row in rows
                      if row["material"] and row["in_scope"]
                      and row["value_text"] is not None]
        if len(value_rows) >= 2:
            value_sentences[index] = value_rows

    split_ok = 0
    for index, rows in value_sentences.items():
        merged_here = any(candidate["merged"] for candidate in candidate_rows
                          if candidate["sentence_index"] == index)
        if not merged_here and all(row["match"] == "exact" for row in rows):
            split_ok += 1

    return {
        "id": case["id"],
        "category": case["category"],
        "note": case["note"],
        "answer": answer,
        "assertions": annotation_rows,
        "candidates": candidate_rows,
        "totals": {
            "assertions": len(annotation_rows),
            "material_in_scope": len(material_rows),
            "exact": len(exact_rows),
            "partial": len(partial_rows),
            "touched_only": len(touched_rows),
            "miss": len(missed_rows),
            "value_bearing": sum(1 for row in annotation_rows
                                 if row["value_text"] is not None),
            "candidates": len(candidate_rows),
            "exact_candidates": len(exact_candidates),
            "partial_candidates": len(partial_candidates),
            "false_candidates": len(false_candidates),
            "shared_value_candidates": len(shared_candidates),
            "merged_candidates": sum(1 for row in candidate_rows if row["merged"]),
            "expected_exclusions": sum(1 for row in annotation_rows
                                       if not row["in_scope"]),
            "false_excuses": sum(1 for row in annotation_rows if row["false_excuse"]),
        },
        "sentence_level": {
            "with_material": len(sentences_with_material),
            "fully_covered": len(fully_covered),
            "touched": len(touched_sentences),
        },
        "compound": {
            "sentences": len(compound_sentences),
            "fully_covered": sum(
                1 for index in compound_sentences
                if all(row["match"] == "exact" for row in material_rows
                       if row["sentence_index"] == index)),
            "value_sentences": len(value_sentences),
            "value_split_ok": split_ok,
        },
        "coverage": {
            "no_claims": {
                "total_assertions": no_claims.total_assertions,
                "checked": no_claims.checked,
                "coverage": round(no_claims.coverage, 4),
                "material_coverage": round(no_claims.material_coverage, 4),
                "unscanned": no_claims.unscanned,
                "unscanned_sentences": [row["text"] for row in
                                        no_claims.unscanned_sentences],
            },
            "one_compound_claim": {
                "total_assertions": one_claim.total_assertions,
                "checked": one_claim.checked,
                "coverage": round(one_claim.coverage, 4),
            },
            "reported_complete_while_missed": bool(
                no_claims.coverage >= 0.999 and missed_rows),
            "missed_or_partial_ids": [row["id"]
                                      for row in missed_rows + partial_rows],
        },
    }


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _ratio(numerator: int, denominator: int) -> float | None:
    return None if not denominator else numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _group_recall(rows: Iterable[dict[str, Any]], key: str,
                  keys: Iterable[str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in keys}
    for row in rows:
        if row["material"] and row["in_scope"]:
            groups.setdefault(row[key], []).append(row)
    out: dict[str, Any] = {}
    for name in keys:
        found = groups[name]
        exact = sum(1 for row in found if row["match"] == "exact")
        out[name] = {
            "material_in_scope": len(found),
            "exact": exact,
            "recall": round(_ratio(exact, len(found)), 4)
            if _ratio(exact, len(found)) is not None else None,
            "missed": [row["id"] for row in found if row["match"] != "exact"],
        }
    return out


def d031_probe() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in D031:
        found = all_candidates(entry["text"])
        if not found:
            observed = "no candidate"
            classification = ("scale_word_unrecognized"
                              if _has_scale_word(entry["text"]) else "other")
        else:
            observed = "; ".join(f"{candidate.kind}: {candidate.text}"
                                 for candidate in found)
            if any(candidate.kind == "version" for candidate in found) and \
                    _has_scale_word(entry["text"]):
                classification = "version_vs_quantity"
            elif any(candidate.kind == "measure" for candidate in found):
                classification = "recognized_measure"
            else:
                classification = "other"
        rows.append({
            "text": entry["text"],
            "annotation_id": entry["annotation_id"],
            "candidates": [{"kind": candidate.kind, "text": candidate.text,
                            "span_start": candidate.start,
                            "span_end": candidate.end}
                           for candidate in found],
            "observed": observed,
            "documented": entry["documented"],
            "matches_documented": observed == entry["documented"],
            "classification": classification,
        })
    return rows


def production_fixtures() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in PRODUCTION_FIXTURES:
        report = audit(fixture["answer"], fixture["claims"])
        rows.append({
            "name": fixture["name"],
            "total_assertions": report.total_assertions,
            "checked": report.checked,
            "candidates": report.candidates,
            "claims_without_candidate": report.claims_without_candidate,
            "coverage": round(report.coverage, 4),
            "material_coverage": round(report.material_coverage, 4),
            "unscanned": report.unscanned,
        })
    return rows


def build_payload(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    cases = cases if cases is not None else load_cases()
    results = [analyse_case(case) for case in cases]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    annotation_rows = [row for result in results for row in result["assertions"]]
    candidate_rows = [row for result in results for row in result["candidates"]]
    material = [row for row in annotation_rows
                if row["material"] and row["in_scope"]]
    exact = [row for row in material if row["match"] == "exact"]
    partial = [row for row in material if row["match"] == "partial"]
    touched = [row for row in material if row["match"] == "touched"]
    missed = [row for row in material if row["match"] == "miss"]

    exact_candidates = [row for row in candidate_rows if row["verdict"] == "exact"]
    partial_candidates = [row for row in candidate_rows if row["verdict"] == "partial"]
    false_candidates = [row for row in candidate_rows if row["verdict"] == "false"]

    recall = _ratio(len(exact), len(material))
    precision = _ratio(len(exact_candidates), len(candidate_rows))
    failure_counts: dict[str, list[str]] = {}
    for row in material:
        if row["failure_class"] and row["failure_class"] != "not_a_failure":
            failure_counts.setdefault(row["failure_class"], []).append(row["id"])

    sentences_with_material = sum(result["sentence_level"]["with_material"]
                                  for result in results)
    sentences_fully = sum(result["sentence_level"]["fully_covered"]
                          for result in results)
    sentences_touched = sum(result["sentence_level"]["touched"]
                            for result in results)
    compound_sentences = sum(result["compound"]["sentences"] for result in results)
    compound_fully = sum(result["compound"]["fully_covered"] for result in results)
    compound_value_sentences = sum(result["compound"]["value_sentences"]
                                   for result in results)
    compound_split_ok = sum(result["compound"]["value_split_ok"]
                            for result in results)

    complete_while_missed = [
        {
            "case": result["id"],
            "coverage": result["coverage"]["no_claims"]["coverage"],
            "missed": result["coverage"]["missed_or_partial_ids"],
            "unscanned": result["coverage"]["no_claims"]["unscanned"],
        }
        for result in results
        if result["coverage"]["reported_complete_while_missed"]
    ]

    exact_agreements = [row for row in exact
                        if row["value_type_agreement"] is not None]
    agreement = sum(1 for row in exact_agreements if row["value_type_agreement"])

    payload: dict[str, Any] = {
        "version": VERSION,
        "dataset": {
            "path": str(DATASET.relative_to(ROOT)).replace("\\", "/"),
            "cases": len(results),
            "assertions": len(annotation_rows),
            "material_in_scope": len(material),
            "value_bearing": sum(1 for row in annotation_rows
                                 if row["value_text"] is not None),
            "case_categories": sorted({result["category"] for result in results}),
        },
        "overall": {
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(_f1(precision, recall), 4)
            if _f1(precision, recall) is not None else None,
            "omission_rate": round(1 - recall, 4) if recall is not None else None,
            "partial_recall": round(_ratio(len(exact) + len(partial), len(material)), 4)
            if _ratio(len(exact) + len(partial), len(material)) is not None else None,
            "touch_rate": round(_ratio(len(exact) + len(partial) + len(touched),
                                       len(material)), 4)
            if _ratio(len(exact) + len(partial) + len(touched), len(material)) is not None else None,
            "material_in_scope": len(material),
            "exact": len(exact),
            "partial": len(partial),
            "touched_only": len(touched),
            "miss": len(missed),
            "candidates": len(candidate_rows),
            "exact_candidates": len(exact_candidates),
            "partial_candidates": len(partial_candidates),
            "false_candidates": len(false_candidates),
            "false_candidate_rate": round(_ratio(len(false_candidates),
                                                 len(candidate_rows)), 4)
            if _ratio(len(false_candidates), len(candidate_rows)) is not None else None,
            "partial_candidate_rate": round(_ratio(len(partial_candidates),
                                                   len(candidate_rows)), 4)
            if _ratio(len(partial_candidates), len(candidate_rows)) is not None else None,
            "value_type_agreement": round(_ratio(agreement, len(exact_agreements)), 4)
            if _ratio(agreement, len(exact_agreements)) is not None else None,
            "false_excuses": sum(result["totals"]["false_excuses"]
                                 for result in results),
        },
        "duplicates": {
            "repeated_candidates": 0,
            "duplicate_rate": 0.0 if candidate_rows else None,
            "shared_value_candidates": sum(
                result["totals"]["shared_value_candidates"] for result in results),
            "note": ("candidates() deduplicates overlapping pattern matches, so "
                     "the scanner cannot emit the same span twice. This column "
                     "measures many-to-one value sharing, not repeated emission; "
                     "claim-level duplication is outside this suite."),
        },
        "sentence_level": {
            "sentences_with_material": sentences_with_material,
            "fully_covered": sentences_fully,
            "touched": sentences_touched,
            "fully_covered_rate": round(_ratio(sentences_fully,
                                               sentences_with_material), 4)
            if _ratio(sentences_fully, sentences_with_material) is not None else None,
            "touched_rate": round(_ratio(sentences_touched,
                                         sentences_with_material), 4)
            if _ratio(sentences_touched, sentences_with_material) is not None else None,
        },
        "compound": {
            "sentences": compound_sentences,
            "fully_covered": compound_fully,
            "fully_covered_rate": round(_ratio(compound_fully, compound_sentences), 4)
            if _ratio(compound_fully, compound_sentences) is not None else None,
            "value_sentences": compound_value_sentences,
            "value_split_ok": compound_split_ok,
            "value_split_rate": round(_ratio(compound_split_ok,
                                             compound_value_sentences), 4)
            if _ratio(compound_split_ok, compound_value_sentences) is not None else None,
        },
        "category_recall": {
            "by_case_category": _group_recall(
                annotation_rows, "case_category", REQUIRED_CASE_CATEGORIES),
            "by_assertion_category": _group_recall(
                annotation_rows, "category", ASSERTION_CATEGORIES),
        },
        "failure_taxonomy": {
            name: {"count": len(ids), "annotations": ids}
            for name, ids in sorted(failure_counts.items())
        },
        "complete_coverage_while_missed": complete_while_missed,
        "false_excuses": [
            {
                "id": row["id"],
                "case": row["input_id"],
                "text": row["text"],
                "disposition": row["disposition_if_no_claim"],
                "failure_class": row["failure_class"],
                "sentence": row["sentence"],
            }
            for row in material if row["false_excuse"]
        ],
        "misses": [
            {
                "id": row["id"],
                "case": row["input_id"],
                "category": row["category"],
                "text": row["text"],
                "sentence": row["sentence"],
                "span_start": row["span_start"],
                "span_end": row["span_end"],
                "match": row["match"],
                "failure_class": row["failure_class"],
                "compound_context": row["compound_context"],
                "disposition_if_no_claim": row["disposition_if_no_claim"],
                "candidates_seen": row["candidates_seen"],
            }
            for row in material if row["match"] != "exact"
        ],
        "expected_exclusions": [
            {
                "id": row["id"],
                "case": row["input_id"],
                "assertion_type": row["assertion_type"],
                "text": row["text"],
                "match": row["match"],
            }
            for row in annotation_rows if not row["in_scope"]
        ],
        "d031": d031_probe(),
        "regression": {
            "rule_version": RULE_VERSION,
            "production_audit_fixtures": production_fixtures(),
        },
        "cost": {
            "model_calls": 0,
            "tavily_calls": 0,
            "network_calls": 0,
            "database_queries": 0,
            "estimated_usd": 0.0,
            "note": ("offline measurement; no provider, search, or database "
                     "client is imported by this runner"),
        },
        "run": {
            "elapsed_ms": elapsed_ms,
            "cases": len(results),
            "assertions": len(annotation_rows),
        },
        "cases": results,
    }
    return payload


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Independent assertion-coverage evaluation - {payload['version']}")
    lines.append("=" * 92)
    dataset = payload["dataset"]
    lines.append(f"dataset  {dataset['path']}  "
                 f"({dataset['cases']} cases, {dataset['assertions']} assertions, "
                 f"{dataset['material_in_scope']} material in scope)")
    lines.append("")

    overall = payload["overall"]
    lines.append("Overall")
    lines.append(f"  material in-scope recall   {overall['exact']}/"
                 f"{overall['material_in_scope']} = {_pct(overall['recall'])}")
    lines.append(f"  partial recall             "
                 f"{_pct(overall['partial_recall'])} (exact + truncated/wrong-kind)")
    lines.append(f"  touch rate                 {_pct(overall['touch_rate'])}")
    lines.append(f"  precision                  "
                 f"{overall['exact_candidates']}/{overall['candidates']} = "
                 f"{_pct(overall['precision'])}")
    lines.append(f"  F1                         {_pct(overall['f1'])}")
    lines.append(f"  omission rate              {_pct(overall['omission_rate'])}")
    lines.append(f"  false-candidate rate       {_pct(overall['false_candidate_rate'])}")
    lines.append(f"  partial-candidate rate     {_pct(overall['partial_candidate_rate'])}")
    lines.append(f"  value-type agreement       {_pct(overall['value_type_agreement'])}")
    lines.append(f"  false excuses              {overall['false_excuses']}")
    duplicates = payload["duplicates"]
    lines.append(f"  duplicate rate             {_pct(duplicates['duplicate_rate'])}"
                 f" (shared-value candidates: {duplicates['shared_value_candidates']})")
    lines.append("")

    sentences = payload["sentence_level"]
    lines.append("Sentence level")
    lines.append(f"  sentences with material assertions {sentences['sentences_with_material']}")
    lines.append(f"  fully covered                     {sentences['fully_covered']} "
                 f"({_pct(sentences['fully_covered_rate'])})")
    lines.append(f"  touched (any match)               {sentences['touched']} "
                 f"({_pct(sentences['touched_rate'])})")
    compound = payload["compound"]
    lines.append(f"  compound sentences                {compound['sentences']}; "
                 f"fully covered {compound['fully_covered']} "
                 f"({_pct(compound['fully_covered_rate'])})")
    lines.append(f"  value-level splits                {compound['value_split_ok']}/"
                 f"{compound['value_sentences']} "
                 f"({_pct(compound['value_split_rate'])})")
    lines.append("")

    lines.append("Per case")
    lines.append(f"  {'case':8} {'category':24} {'mat':>4} {'exact':>5} "
                 f"{'part':>5} {'tch':>4} {'miss':>5} {'prec':>7} {'cov0':>7} "
                 f"{'blind100':>8}")
    for result in payload["cases"]:
        totals = result["totals"]
        exact_candidates = totals["exact_candidates"]
        candidates = totals["candidates"]
        precision = _ratio(exact_candidates, candidates)
        coverage = result["coverage"]["no_claims"]["coverage"]
        lines.append(
            f"  {result['id']:8} {result['category']:24} "
            f"{totals['material_in_scope']:4} {totals['exact']:5} "
            f"{totals['partial']:5} {totals['touched_only']:4} "
            f"{totals['miss']:5} "
            f"{_pct(precision):>7} {_pct(coverage):>7} "
            f"{'yes' if result['coverage']['reported_complete_while_missed'] else '-':>8}")
    lines.append("")

    lines.append("Recall by case category")
    for name, row in payload["category_recall"]["by_case_category"].items():
        lines.append(f"  {name:26} {row['exact']:>3}/{row['material_in_scope']:<3} "
                     f"{_pct(row['recall']):>7}")
    lines.append("")

    lines.append("Recall by assertion category")
    for name, row in payload["category_recall"]["by_assertion_category"].items():
        lines.append(f"  {name:26} {row['exact']:>3}/{row['material_in_scope']:<3} "
                     f"{_pct(row['recall']):>7}")
    lines.append("")

    lines.append("Failure taxonomy")
    for name, row in payload["failure_taxonomy"].items():
        lines.append(f"  {name:30} {row['count']:>3}")
    lines.append(f"  {'expected exclusions':30} "
                 f"{len(payload['expected_exclusions']):>3}")
    lines.append("")

    lines.append("In-scope assertions excused by sentence wording")
    if payload["false_excuses"]:
        for row in payload["false_excuses"]:
            lines.append(f"  {row['id']} ({row['case']}): {row['text']!r} "
                         f"excused as {row['disposition']} — {row['sentence']}")
    else:
        lines.append("  none")
    lines.append("")

    lines.append("Complete coverage reported while assertions were missed")
    if payload["complete_coverage_while_missed"]:
        for row in payload["complete_coverage_while_missed"]:
            lines.append(f"  {row['case']}: coverage {_pct(row['coverage'])}, "
                         f"missed {', '.join(row['missed'])}")
    else:
        lines.append("  none")
    lines.append("")

    lines.append("D-031 examples (measured, not repaired)")
    for row in payload["d031"]:
        lines.append(f"  {row['text']:16} -> {row['observed']:38} "
                     f"{row['classification']} "
                     f"({'as documented' if row['matches_documented'] else 'DIFFERS'})")
    lines.append("")

    lines.append("Regression pins")
    regression = payload["regression"]
    lines.append(f"  RULE_VERSION {regression['rule_version']}")
    for row in regression["production_audit_fixtures"]:
        lines.append(f"  {row['name']:24} total={row['total_assertions']} "
                     f"checked={row['checked']} coverage={_pct(row['coverage'])} "
                     f"material={_pct(row['material_coverage'])} "
                     f"unscanned={row['unscanned']}")
    lines.append("")

    lines.append("Cost and latency")
    cost = payload["cost"]
    lines.append(f"  model calls {cost['model_calls']}, Tavily calls "
                 f"{cost['tavily_calls']}, network calls {cost['network_calls']}, "
                 f"database queries {cost['database_queries']}, "
                 f"estimated USD {cost['estimated_usd']:.2f}")
    lines.append(f"  runner wall clock {payload['run']['elapsed_ms']:.1f} ms "
                 f"(excludes interpreter startup)")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    try:
        payload = build_payload()
    except ValueError as exc:
        print(f"invalid dataset:\n{exc}", file=sys.stderr)
        return 2

    if "--json" in argv:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if "--out" in argv:
        index = argv.index("--out")
        try:
            destination = Path(argv[index + 1])
        except IndexError:
            print("--out needs a path", file=sys.stderr)
            return 2
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")

    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
