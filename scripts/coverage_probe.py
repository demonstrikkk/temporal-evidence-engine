"""Assertion-coverage probe: what the audit sees against what a reader sees.

The extraction suite (`eval/extraction_suite.py`) measures the candidate finder
against `eval/assertion_gold.jsonl` and reports 100% recall. That number is
true and it is also circular: every expected assertion in that file is a value
(money, measure, date, version, percent, number) and the finder is a set of
regexes for exactly those kinds. It cannot tell us what the audit is blind to,
because nothing blind was ever put in front of it.

This probe does the opposite. It takes a human-reviewed taxonomy of assertion
shapes - including the ones the finder was not built for - and asks, per case:

* how many assertions did a reader find?
* how many did the deterministic scan find?
* how many material assertions are invisible to the audit?
* what coverage ratio does the audit report, and what would an honest
  assertion-level ratio be?

It runs three ways per case, because the answer changes with what the extractor
returned:

    no claims       the worst case: the model returned nothing
    one claim       a single compound claim spanning the whole answer
    gold claims     the hand-written expected claims, when the case has them

That second run is the one that matters for compound claims: the audit calls a
value "checked" when a stored claim span *contains* it, so one over-broad claim
marks every value inside it as covered without ever verifying the clause.

Hermetic: no model, no network, no database. Reads the two JSONL files and
imports the audit. Exit code is 0 always - this is a measurement, not a gate.

Usage (from the repository root):

    backend\\.venv\\Scripts\\python.exe scripts\\coverage_probe.py
    backend\\.venv\\Scripts\\python.exe scripts\\coverage_probe.py --json
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.verify.assertions import candidates  # noqa: E402
from app.verify.coverage import audit  # noqa: E402
from app.verify.segment import segment  # noqa: E402

TAXONOMY = ROOT / "eval" / "coverage_taxonomy.jsonl"
GOLD = ROOT / "eval" / "assertion_gold.jsonl"


@dataclass
class CaseResult:
    case_id: str
    category: str
    human_assertions: int
    human_material: int
    scanned: int
    visible: int
    blind_material: int
    no_claims_total: int
    no_claims_checked: int
    no_claims_coverage: float
    one_claim_total: int
    one_claim_checked: int
    one_claim_coverage: float
    honest_coverage: float
    blind_assertions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "category": self.category,
            "human_assertions": self.human_assertions,
            "human_material": self.human_material,
            "scanned": self.scanned,
            "visible": self.visible,
            "blind_material": self.blind_material,
            "audit_no_claims": {
                "total": self.no_claims_total,
                "checked": self.no_claims_checked,
                "coverage": round(self.no_claims_coverage, 4),
            },
            "audit_one_compound_claim": {
                "total": self.one_claim_total,
                "checked": self.one_claim_checked,
                "coverage": round(self.one_claim_coverage, 4),
            },
            "honest_material_coverage": round(self.honest_coverage, 4),
            "blind_assertions": self.blind_assertions,
            "notes": self.notes,
        }


def _load(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _span(answer: str, text: str) -> tuple[int, int] | None:
    start = answer.find(text)
    if start < 0:
        return None
    return start, start + len(text)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and a[1] > b[0]


def _ratio(checked: int, total: int) -> float:
    return 1.0 if not total else checked / total


def _audit_shape(answer: str, claims: list[dict[str, Any]]) -> tuple[int, int, float]:
    report = audit(answer, claims)
    return report.total_assertions, report.checked, report.coverage


def probe_case(case: dict[str, Any]) -> CaseResult:
    answer = case["answer"]
    assertions = case.get("assertions") or []
    scanned = candidates(answer)
    scanned_spans = [(c.start, c.end) for c in scanned]

    visible = 0
    blind: list[str] = []
    blind_material = 0
    notes: list[str] = []
    for item in assertions:
        span = _span(answer, item["text"])
        if span is None:
            notes.append(f"ASSERTION TEXT NOT FOUND: {item['text']!r}")
            blind_material += 1 if item.get("material") else 0
            blind.append(item["text"])
            continue
        seen = any(_overlaps(span, s) for s in scanned_spans)
        if seen:
            visible += 1
        else:
            blind.append(item["text"])
            if item.get("material"):
                blind_material += 1

    human_total = len(assertions)
    human_material = sum(1 for item in assertions if item.get("material"))

    no_total, no_checked, no_cov = _audit_shape(answer, [])

    # One compound claim spanning the whole answer: the shape that makes a
    # containment test report coverage the run never achieved.
    compound = [{"id": "compound", "span_start": 0, "span_end": len(answer)}]
    one_total, one_checked, one_cov = _audit_shape(answer, compound)

    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        human_assertions=human_total,
        human_material=human_material,
        scanned=len(scanned),
        visible=visible,
        blind_material=blind_material,
        no_claims_total=no_total,
        no_claims_checked=no_checked,
        no_claims_coverage=no_cov,
        one_claim_total=one_total,
        one_claim_checked=one_checked,
        one_claim_coverage=one_cov,
        honest_coverage=_ratio(visible, human_total),
        blind_assertions=blind,
        notes=notes,
    )


def run() -> list[CaseResult]:
    return [probe_case(case) for case in _load(TAXONOMY)]


def gold_circularity() -> dict[str, Any]:
    """How much of the 100%-recall gold set is a test of the scanner's vocabulary."""
    rows = _load(GOLD)
    kinds: dict[str, int] = {}
    for row in rows:
        for item in row.get("expected_assertions") or []:
            kind = item.get("kind", "?")
            kinds[kind] = kinds.get(kind, 0) + 1
    known = {"money", "percent", "measure", "date", "version", "number"}
    expected = sum(kinds.values())
    covered_by_design = sum(count for kind, count in kinds.items() if kind in known)
    return {
        "cases": len(rows),
        "expected_assertions": expected,
        "kinds": dict(sorted(kinds.items())),
        "kinds_the_scanner_was_built_for": sorted(k for k in kinds if k in known),
        "fraction_of_gold_in_scanner_vocabulary": round(
            covered_by_design / expected, 4) if expected else 1.0,
        "note_drift": [
            {"case": row["id"], "note": row.get("note", ""),
             "expected_assertions": len(row.get("expected_assertions") or [])}
            for row in rows
            if "11 assertions" in str(row.get("note", ""))
        ],
    }


def totals(results: list[CaseResult]) -> dict[str, Any]:
    human = sum(r.human_assertions for r in results)
    material = sum(r.human_material for r in results)
    visible = sum(r.visible for r in results)
    blind_material = sum(r.blind_material for r in results)
    with_candidates = [r for r in results if r.no_claims_total > 0]
    audited = sum(r.no_claims_total for r in with_candidates)
    audited_checked = sum(r.no_claims_checked for r in with_candidates)
    return {
        "cases": len(results),
        "human_assertions": human,
        "human_material": material,
        "scanner_visible": visible,
        "blind_material": blind_material,
        "audit_visible_rate": round(_ratio(visible, human), 4),
        "audit_visible_rate_material": round(
            _ratio(visible, material), 4) if material else 1.0,
        "cases_with_a_scannable_value": len(with_candidates),
        "cases_with_no_scannable_value": len(results) - len(with_candidates),
        "audit_reported_coverage_where_it_had_a_value_to_check": round(
            _ratio(audited_checked, audited), 4),
        "cases_reporting_100pct_with_nothing_scanned": [
            r.case_id for r in results if r.no_claims_total == 0],
        "cases_where_audit_reports_100pct_but_is_blind": [
            r.case_id for r in results
            if r.no_claims_coverage >= 0.999 and r.blind_material > 0],
    }


def main(argv: list[str]) -> int:
    results = run()
    payload = {
        "taxonomy": [r.as_dict() for r in results],
        "totals": totals(results),
        "gold": gold_circularity(),
    }
    if "--json" in argv:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("Assertion-coverage probe")
    print("=" * 78)
    print(f"{'case':8} {'category':28} {'human':>5} {'scan':>5} {'blind':>5} "
          f"{'audit0':>7} {'compound':>8} {'honest':>7}")
    for result in results:
        print(f"{result.case_id:8} {result.category:28} "
              f"{result.human_assertions:5} {result.scanned:5} "
              f"{result.blind_material:5} "
              f"{result.no_claims_coverage * 100:6.1f}% "
              f"{result.one_claim_coverage * 100:7.1f}% "
              f"{result.honest_coverage * 100:6.1f}%")
    total = payload["totals"]
    print("-" * 78)
    print(f"human assertions                 {total['human_assertions']}")
    print(f"material assertions              {total['human_material']}")
    print(f"visible to the deterministic scan{total['scanner_visible']:>5}")
    print(f"material assertions it cannot see{total['blind_material']:>5}")
    print(f"audit-visible rate               "
          f"{total['audit_visible_rate_material'] * 100:.1f}%")
    print(f"audit coverage where it had a value to check "
          f"{total['audit_reported_coverage_where_it_had_a_value_to_check'] * 100:.1f}%")
    print(f"cases with nothing to scan       "
          f"{total['cases_with_no_scannable_value']} "
          f"({', '.join(total['cases_reporting_100pct_with_nothing_scanned'])})")
    print(f"cases reporting 100% while blind "
          f"{', '.join(total['cases_where_audit_reports_100pct_but_is_blind']) or 'none'}")
    gold = payload["gold"]
    print()
    print("Gold-set circularity")
    print(f"  expected assertions            {gold['expected_assertions']}")
    print(f"  kinds present                  {gold['kinds']}")
    print(f"  fraction in scanner vocabulary "
          f"{gold['fraction_of_gold_in_scanner_vocabulary'] * 100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
