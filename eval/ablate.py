"""Ablations: what do the deterministic layers actually buy?

The gold set is fixed and the model's proposals are fixed (they come from the
gold file). So the only thing that changes between arms is the code between the
proposal and the verdict. That isolates the thing this project claims is
load-bearing, and removes model quality from the comparison.

Four arms:

| arm | reading | can express `changed`? |
|---|---|---|
| `baseline` | the newest page that says anything about the claim is the answer; no quote check | yes, whenever a contradicts proposal carries a different value |
| `baseline-collapsed` | the same reading, but with no notion of a changed fact | no - a refutation is always `contradicted` |
| `evidence-gated` | the same reading, but a proposal counts only if its quote is a literal substring of the stored page and its numbers appear in that quote | yes, same naive rule |
| `full` | the real orchestrator: extraction, quote location, settlement, event dates, decision table | yes, only with a settled replacement dated after generation |

Both baseline variants are reported because picking only the version that scores
worse is how an ablation becomes a sales document. `baseline` is the charitable
one: reading the newest result first is what a search-plus-model pipeline
actually does, and it gets every `changed` case right. That is the point - the
naive pipeline is not wrong about the common case, it is wrong about publication
time versus event time, scope, and conflicting sources.

What the ablations cannot tell you: whether a real model would propose the same
relationships. It is given the same proposals as the full pipeline on purpose.

Run from the repo root:

    python eval/ablate.py
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))  # sibling harness, same directory
sys.path.insert(0, str(ROOT / "backend"))

from run_eval import CaseResult, load_cases, run_hermetic_case  # noqa: E402

DECISIVE = {"supported", "changed", "contradicted"}


# ---------------------------------------------------------------------------
# the two degraded pipelines
# ---------------------------------------------------------------------------

def _number_tokens(value: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?", value or "")


def _numbers_in(value: str, quote: str) -> bool:
    """A deliberately plain check: every number must appear as a whole token."""
    for number in _number_tokens(value):
        pattern = r"(?<![\d.,])" + re.escape(number) + r"(?![\d.,])"
        if not re.search(pattern, quote or ""):
            return False
    return True


def _values_differ(left: str, right: str) -> bool:
    """Naive inequality, on the number rather than the formatted string."""
    left_tokens = [token.replace(",", "") for token in _number_tokens(left)]
    right_tokens = [token.replace(",", "") for token in _number_tokens(right)]
    if left_tokens and right_tokens:
        return left_tokens != right_tokens
    return (left or "").strip() != (right or "").strip()


def _names_a_replacement(claim: dict, proposal: dict) -> bool:
    """A `changed` verdict has to name the new value. A bare negation does not."""
    candidate = proposal.get("candidate_value") or ""
    if not candidate.strip():
        return False
    return _values_differ(candidate, claim.get("object_raw") or "")


def _accepted_by_evidence(claim: dict, proposal: dict,
                          pages: dict[str, str]) -> bool:
    """The evidence-gated arm: the quote must exist, and so must the numbers."""
    content = pages.get(proposal.get("url") or "")
    quote = proposal.get("quote") or ""
    if not content or not quote or quote not in content:
        return False
    relationship = proposal.get("relationship")
    if relationship == "supports":
        return _numbers_in(claim.get("object_raw") or "", quote)
    if relationship == "contradicts":
        candidate = proposal.get("candidate_value") or ""
        # A negation names no value; there is nothing to check but the cue.
        return not candidate or _numbers_in(candidate, quote)
    return True


def _status_from_proposals(
    claim: dict,
    proposals: list[dict],
    *,
    collapse_changed: bool,
    require_evidence: bool,
    pages: dict[str, str],
    published: dict[str, str],
) -> str:
    """The naive reading: the newest page that speaks about this claim wins.

    Reading the newest page first is what a search-plus-LLM baseline actually
    does, and it is a defensible policy - it is simply wrong in the cases this
    project exists for, where the newest *publication* restates an older *fact*
    or states a differently-scoped number. Treating "today's top result" as the
    answer is the thing being ablated, so the baseline gets its best form.
    """
    ordered = sorted(proposals,
                     key=lambda p: published.get(p.get("url") or "", ""),
                     reverse=True)
    chosen = None
    for proposal in ordered:
        if require_evidence and not _accepted_by_evidence(claim, proposal, pages):
            continue
        if proposal.get("relationship") in ("supports", "contradicts"):
            chosen = proposal
            break
        if chosen is None:
            chosen = proposal

    if chosen is None:
        return "unverifiable"
    relationship = chosen.get("relationship")
    if relationship == "supports":
        return "supported"
    if relationship == "contradicts":
        if collapse_changed:
            return "contradicted"
        return "changed" if _names_a_replacement(claim, chosen) else "contradicted"
    return "unverifiable"


def run_degraded_case(case: dict, *, collapse_changed: bool,
                      require_evidence: bool) -> CaseResult:
    pages = {source["url"]: source["content"]
             for source in case.get("sources", [])}
    published = {source["url"]: str(source.get("published_date") or "")
                 for source in case.get("sources", [])}
    grouped: dict[int, list[dict]] = {}
    for proposal in case.get("proposals", []):
        grouped.setdefault(int(proposal["claim"]), []).append(proposal)

    result = CaseResult(case_id=case["id"], note=case.get("note", ""),
                        run_status="completed",
                        claims_total=len(case["claims"]),
                        split=str(case.get("split", "dev")))
    for index, gold in enumerate(case["claims"]):
        expected = gold.get("expect") or {}
        actual = _status_from_proposals(
            gold, grouped.get(index, []),
            collapse_changed=collapse_changed,
            require_evidence=require_evidence,
            pages=pages, published=published)
        ok_status = actual == expected.get("status")
        result.rows.append({
            "claim": gold["claim_text"],
            "expected": expected.get("status"),
            "expected_reason": expected.get("reason_code"),
            "actual": actual,
            "actual_reason": "",
            "ok_status": ok_status,
            "ok_reason": True,
        })
        if not ok_status:
            result.failures.append(
                f"{gold['claim_text']!r}: expected {expected.get('status')}, "
                f"got {actual}")
    # No stored rows and no event dates exist in these arms: the cells stay
    # zero, and the report prints `—` rather than a flattering 100%.
    result.quote_total = 0
    result.event_total = 0
    return result


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

@dataclass
class Arm:
    name: str
    description: str
    results: list[CaseResult] = field(default_factory=list)
    quotes: int = 0
    quotes_valid: int = 0
    events: int = 0
    events_correct: int = 0

    def score(self) -> dict[str, Any]:
        rows = [row for result in self.results for row in result.rows]
        total = len(rows)
        correct = sum(1 for row in rows if row["ok_status"])
        false_supported = sum(1 for row in rows if row["actual"] == "supported"
                              and row["expected"] != "supported")
        false_changed = sum(1 for row in rows if row["actual"] == "changed"
                            and row["expected"] != "changed")
        abstain_expected = sum(1 for row in rows
                               if row["expected"] == "unverifiable")
        abstain_actual = sum(1 for row in rows
                             if row["expected"] == "unverifiable"
                             and row["actual"] == "unverifiable")
        over_abstain = sum(1 for row in rows if row["expected"] in DECISIVE
                           and row["actual"] == "unverifiable")
        return {
            "claims": total,
            "status_accuracy": correct / total if total else 0.0,
            "false_supported": false_supported,
            "false_changed": false_changed,
            "abstention_recall": (abstain_actual / abstain_expected
                                  if abstain_expected else 1.0),
            "over_abstention": over_abstain,
        }


def _status_table(arms: list[Arm]) -> None:
    """Correct/total per expected status. This is where an arm's blind spot shows."""
    statuses = ["supported", "changed", "contradicted", "unverifiable"]
    print("| expected | claims | " + " | ".join(f"`{arm.name}`" for arm in arms)
          + " |")
    print("|---|---|" + "---|" * len(arms))
    for status in statuses:
        cells = []
        claims = 0
        for arm in arms:
            rows = [row for result in arm.results for row in result.rows
                    if row["expected"] == status]
            claims = max(claims, len(rows))
            correct = sum(1 for row in rows if row["ok_status"])
            cells.append(f"{correct}/{len(rows)}")
        print(f"| `{status}` | {claims} | " + " | ".join(cells) + " |")
    print()


def _error_table(arms: list[Arm]) -> None:
    """The mistakes, not just their count: expected -> actual, most common first."""
    print("| pipeline | expected → actual | claims |")
    print("|---|---|---|")
    for arm in arms[:-1]:
        pairs: dict[tuple[str, str], int] = {}
        for result in arm.results:
            for row in result.rows:
                if row["ok_status"]:
                    continue
                key = (str(row["expected"]), str(row["actual"]))
                pairs[key] = pairs.get(key, 0) + 1
        for (expected, actual), count in sorted(
                pairs.items(), key=lambda item: (-item[1], item[0])):
            print(f"| `{arm.name}` | {expected} → {actual} | {count} |")
    print()


def build_arms(cases: list[dict], base: Path) -> list[Arm]:
    arms = [
        Arm("baseline",
            "newest page that mentions the claim is the answer; a contradicts "
            "proposal that names a different value becomes `changed`"),
        Arm("baseline-collapsed",
            "same reading, with no `changed` status: every refutation is "
            "`contradicted`"),
        Arm("evidence-gated",
            "same reading, but a proposal counts only if its quote is a "
            "literal substring of the stored page and its numbers appear in "
            "that quote; still no event dates"),
        Arm("full",
            "real orchestrator: settlement, event dates, decision table"),
    ]
    for case in cases:
        arms[0].results.append(run_degraded_case(
            case, collapse_changed=False, require_evidence=False))
        arms[1].results.append(run_degraded_case(
            case, collapse_changed=True, require_evidence=False))
        arms[2].results.append(run_degraded_case(
            case, collapse_changed=False, require_evidence=True))
        full = run_hermetic_case(case, base)
        arms[3].results.append(full)
        arms[3].quotes += full.quote_total
        arms[3].quotes_valid += full.quote_valid
        arms[3].events += full.event_total
        arms[3].events_correct += full.event_correct
    return arms


def _table(arms: list[Arm]) -> None:
    print("| pipeline | claims | status accuracy | false supported | "
          "false changed | abstention recall | over-abstention |")
    print("|---|---|---|---|---|---|---|")
    for arm in arms:
        score = arm.score()
        print(f"| `{arm.name}` | {score['claims']} | "
              f"{score['status_accuracy']:.1%} | {score['false_supported']} | "
              f"{score['false_changed']} | {score['abstention_recall']:.1%} | "
              f"{score['over_abstention']} |")
    print()
    for arm in arms:
        if arm.quotes:
            print(f"- `{arm.name}` quote validity: "
                  f"{arm.quotes_valid}/{arm.quotes}")
            print(f"- `{arm.name}` event dates: "
                  f"{arm.events_correct}/{arm.events}")
    print()


def _status_table(arms: list[Arm]) -> None:
    """Correct/total per expected status. This is where an arm's blind spot shows."""
    statuses = ["supported", "changed", "contradicted", "unverifiable"]
    print("| expected | claims | " + " | ".join(f"`{arm.name}`" for arm in arms)
          + " |")
    print("|---|---|" + "---|" * len(arms))
    for status in statuses:
        cells = []
        claims = 0
        for arm in arms:
            rows = [row for result in arm.results for row in result.rows
                    if row["expected"] == status]
            claims = max(claims, len(rows))
            correct = sum(1 for row in rows if row["ok_status"])
            cells.append(f"{correct}/{len(rows)}")
        print(f"| `{status}` | {claims} | " + " | ".join(cells) + " |")
    print()


def _error_table(arms: list[Arm]) -> None:
    """The mistakes, not just their count: expected -> actual, most common first."""
    print("| pipeline | expected → actual | claims |")
    print("|---|---|---|")
    for arm in arms[:-1]:
        pairs: dict[tuple[str, str], int] = {}
        for result in arm.results:
            for row in result.rows:
                if row["ok_status"]:
                    continue
                key = (str(row["expected"]), str(row["actual"]))
                pairs[key] = pairs.get(key, 0) + 1
        for (expected, actual), count in sorted(
                pairs.items(), key=lambda item: (-item[1], item[0])):
            print(f"| `{arm.name}` | {expected} → {actual} | {count} |")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", default=None)
    parser.add_argument("--split", choices=["all", "dev", "heldout"],
                        default="all")
    args = parser.parse_args()

    paths = ([Path(item) for item in args.cases] if args.cases else
             [ROOT / "eval" / "gold.jsonl",
              ROOT / "eval" / "gold_generated.jsonl"])
    cases: list[dict] = []
    for path in paths:
        if not path.is_file():
            print(f"case file not found: {path}", file=sys.stderr)
            return 2
        cases.extend(load_cases(path, None, args.split))
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

    base = Path(tempfile.mkdtemp(prefix="tev-ablate-"))
    try:
        arms = build_arms(cases, base)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("# Ablation — identical model proposals, different gates\n")
    print(f"{len(cases)} cases, split `{args.split}`.\n")
    for arm in arms:
        print(f"- `{arm.name}`: {arm.description}")
    print()
    _table(arms)
    _status_table(arms)
    _error_table(arms)
    full = arms[-1]
    if full.quotes:
        print(f"**Full pipeline quotes resolve: {full.quotes_valid}/{full.quotes}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
