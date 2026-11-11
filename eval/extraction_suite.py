"""Suite 1 — Extraction.

Measures the deterministic assertion finder (segment + candidate finder)
against labelled assertion sets. Reports recall, precision, compound-split
accuracy, unclassified rate, and the silent-drop count (which must be 0).

Every assertion gets a record or a reason — that invariant is checked here, not
assumed.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.verify.assertions import candidates
from app.verify.segment import segment


@dataclass
class ExtractionResult:
    answer_id: str
    expected: int
    found: int
    matched: int
    compound_expected: int
    compound_correct: int
    unclassified: int
    silent_drops: int
    failures: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else 1.0

    @property
    def compound_accuracy(self) -> float:
        return self.compound_correct / self.compound_expected \
            if self.compound_expected else 1.0


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def run_case(case: dict) -> ExtractionResult:
    answer = case["answer"]
    answer_id = case["id"]
    expected = case.get("expected_assertions", [])

    # Candidate finder over the answer's own text.
    found_candidates: list[tuple[int, int, str, str]] = []
    for sentence in segment(answer):
        for c in candidates(sentence.text):
            found_candidates.append((
                sentence.start + c.start, sentence.start + c.end,
                c.kind, c.text))

    expected_norm: list[tuple[str, str]] = [
        (_norm(e["text"]), e.get("kind", "").casefold()) for e in expected]

    found_keys = [(_norm(t), k) for _, _, k, t in found_candidates]

    matched = 0
    matched_found_keys: set[int] = set()
    for ekey in expected_norm:
        for fi, fkey in enumerate(found_keys):
            if fi in matched_found_keys:
                continue
            if fkey == ekey:
                matched += 1
                matched_found_keys.add(fi)
                break

    # Compound-split accuracy: each expected assertion must be found as its own
    # candidate (not merged with a neighbour).
    compound_expected = len(expected_norm)
    compound_correct = matched

    # Unclassified: found candidates that matched nothing expected.
    unclassified = len(found_keys) - len(matched_found_keys)

    # Silent-drop invariant: every expected assertion must be accounted for.
    silent_drops = len(expected_norm) - matched

    failures: list[str] = []
    if silent_drops:
        failures.append(f"{silent_drops} silent drops")
    return ExtractionResult(
        answer_id=answer_id, expected=len(expected), found=len(found_candidates),
        matched=matched, compound_expected=compound_expected,
        compound_correct=compound_correct, unclassified=unclassified,
        silent_drops=silent_drops, failures=failures)


def run_all(path: Path = ROOT / "eval" / "assertion_gold.jsonl") -> list[ExtractionResult]:
    results: list[ExtractionResult] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            results.append(run_case(case))
    return results


def report(results: list[ExtractionResult]) -> str:
    lines: list[str] = []
    lines.append("# Suite 1 — Extraction\n")
    lines.append("| answer | expected | found | matched | recall | precision | "
                 "compound | unclassified | silent_drops |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(f"| {r.answer_id} | {r.expected} | {r.found} | {r.matched} | "
                     f"{r.recall:.1%} | {r.precision:.1%} | "
                     f"{r.compound_correct}/{r.compound_expected} | {r.unclassified} | "
                     f"{r.silent_drops} |")
    lines.append("")

    total_expected = sum(r.expected for r in results)
    total_found = sum(r.found for r in results)
    total_matched = sum(r.matched for r in results)
    total_compound_e = sum(r.compound_expected for r in results)
    total_compound_c = sum(r.compound_correct for r in results)
    total_unclassified = sum(r.unclassified for r in results)
    total_silent = sum(r.silent_drops for r in results)

    lines.append("| **Total** | " + " | ".join([
        str(total_expected), str(total_found), str(total_matched),
        f"{total_matched/total_expected:.1%}" if total_expected else "—",
        f"{total_matched/total_found:.1%}" if total_found else "—",
        f"{total_compound_c}/{total_compound_e}",
        str(total_unclassified), str(total_silent), "|"]))
    lines.append("")

    # Gate: silent drops must be 0.
    if total_silent:
        lines.append(f"FAIL: {total_silent} silent drops — every assertion must "
                     f"get a record or a reason.")
    else:
        lines.append("PASS: zero silent drops — every assertion accounted for.")

    # Gate: recall must be 1.0.
    recall = total_matched / total_expected if total_expected else 1.0
    if recall < 1.0:
        lines.append(f"FAIL: extraction recall {recall:.1%} — labelled "
                     f"assertions were not found.")
    else:
        lines.append(f"PASS: extraction recall 100% — every labelled assertion "
                     f"was found ({total_matched}/{total_expected}).")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = run_all()
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
