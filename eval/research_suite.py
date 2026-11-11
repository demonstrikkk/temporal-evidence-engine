"""Suite 2 — Research.

Measures evidence reuse: one search per subject group, one extract per page,
and what the pre-filter skipped. Reports credits per accepted evidence, unique
useful sources, and duplicate extraction count.

Hermetic replay; no keys, no network.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import ScriptedGateway, ScriptedTavily, run_hermetic_case  # noqa: E402
from app.verify.orchestrator import Verifier, create_run  # noqa: E402
from app.verify.schemas import AnswerIn  # noqa: E402
from app.verify.store import LocalStore  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@dataclass
class ResearchResult:
    case_id: str
    searches: int
    extracts: int
    unique_urls: int
    duplicate_extracts: int
    accepted_evidence: int
    credits: int
    prefiltered: int
    judge_calls: int
    solos_searches: int
    sharing_saved: int

    @property
    def credits_per_evidence(self) -> float:
        return self.credits / self.accepted_evidence if self.accepted_evidence else 0.0

    @property
    def duplicate_rate(self) -> float:
        return self.duplicate_extracts / self.extracts if self.extracts else 0.0


def _solo_searches(case: dict) -> int:
    """How many searches the same claims would cost run solo (fresh store each)."""
    total = 0
    for index, claim in enumerate(case["claims"]):
        store = LocalStore(f"/tmp/tev-research-solo-{case['id']}-{index}")
        created = create_run(store, AnswerIn(
            answer=claim["claim_text"], prompt=case.get("prompt") or "",
            generated_at=GEN, generated_at_source="user_supplied"))
        proposals = [p for p in case.get("proposals", [])
                     if p.get("claim") == index]
        proposals = [{**p, "claim_text": claim["claim_text"]} for p in proposals]
        gateway = ScriptedGateway([claim], proposals)
        tavily = ScriptedTavily(case.get("sources", []))
        Verifier(store, created.run_id, bifrost=gateway, tavily=tavily).execute()
        total += len(tavily.searches)
    return total


def run_case(case: dict) -> ResearchResult:
    case_id = case["id"]
    store = LocalStore(f"/tmp/tev-research-{case_id}")
    created = create_run(store, AnswerIn(
        answer=case["answer"], prompt=case.get("prompt") or "",
        generated_at=GEN, generated_at_source="user_supplied"))
    # Proposals need claim_text matched to the claim text (same convention as
    # run_eval.run_hermetic_case).
    proposals = []
    for p in case.get("proposals", []):
        claim_index = int(p.get("claim", 0))
        proposals.append({**p, "claim_text": case["claims"][claim_index]["claim_text"]})
    gateway = ScriptedGateway(case["claims"], proposals)
    tavily = ScriptedTavily(case.get("sources", []))
    Verifier(store, created.run_id, bifrost=gateway, tavily=tavily).execute()

    # Count extracts and unique URLs from the fake Tavily.
    extracted_urls: list[str] = []
    for call in tavily.extracts:
        extracted_urls.extend(call["urls"])
    unique_urls = len(set(extracted_urls))
    duplicate_extracts = len(extracted_urls) - unique_urls

    # Count accepted evidence (settled links) from stored rows.
    accepted = 0
    for claim in store.list_claims(created.run_id):
        for item in store.list_evidence(str(claim["id"])):
            link = item.get("link") or {}
            if link.get("settlement_status") == "accepted":
                accepted += 1

    # Count prefiltered events and judge spans.
    events = store.list_events(created.run_id)
    prefiltered = sum(1 for e in events if e["event_type"] == "evidence.prefiltered")

    # Count search credits (each search = 1 credit in the fake).
    credits = len(tavily.searches)

    # Solo comparison.
    solos = _solo_searches(case)
    sharing_saved = solos - len(tavily.searches)

    # Judge calls: count judge_evidence spans.
    header = store.get_execution_trace(created.run_id)
    judge_calls = 0
    if header:
        spans = store.list_trace_spans(str(header["trace_id"]))
        judge_calls = sum(1 for s in spans if s["operation"] == "judge_evidence")

    return ResearchResult(
        case_id=case_id, searches=len(tavily.searches), extracts=len(tavily.extracts),
        unique_urls=unique_urls, duplicate_extracts=duplicate_extracts,
        accepted_evidence=accepted, credits=credits, prefiltered=prefiltered,
        judge_calls=judge_calls, solos_searches=solos, sharing_saved=sharing_saved)


def run_all(path: Path = ROOT / "eval" / "research_gold.jsonl") -> list[ResearchResult]:
    results: list[ResearchResult] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            results.append(run_case(case))
    return results


def report(results: list[ResearchResult]) -> str:
    lines: list[str] = []
    lines.append("# Suite 2 — Research\n")
    lines.append("| case | searches | extracts | unique_urls | duplicate_extracts | "
                 "accepted_evidence | credits | credits/evidence | prefiltered | judge_calls | sharing_saved |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(f"| {r.case_id} | {r.searches} | {r.extracts} | {r.unique_urls} | "
                     f"{r.duplicate_extracts} | {r.accepted_evidence} | {r.credits} | "
                     f"{r.credits_per_evidence:.2f} | {r.prefiltered} | {r.judge_calls} | "
                     f"{r.sharing_saved} |")
    lines.append("")

    total_searches = sum(r.searches for r in results)
    total_extracts = sum(r.extracts for r in results)
    total_unique = sum(r.unique_urls for r in results)
    total_dupes = sum(r.duplicate_extracts for r in results)
    total_accepted = sum(r.accepted_evidence for r in results)
    total_credits = sum(r.credits for r in results)
    total_prefiltered = sum(r.prefiltered for r in results)
    total_judge = sum(r.judge_calls for r in results)
    total_solos = sum(r.solos_searches for r in results)
    total_saved = sum(r.sharing_saved for r in results)

    lines.append("| **Total** | " + " | ".join([
        str(total_searches), str(total_extracts), str(total_unique),
        str(total_dupes), str(total_accepted), str(total_credits),
        f"{total_credits/total_accepted:.2f}" if total_accepted else "—",
        str(total_prefiltered), str(total_judge), str(total_saved), "|"]))
    lines.append("")

    # Gate: zero duplicate extractions (each page extracted once per run).
    if total_dupes:
        lines.append(f"FAIL: {total_dupes} duplicate extractions — each page "
                     f"should be extracted once per run.")
    else:
        lines.append("PASS: zero duplicate extractions — each page extracted once per run.")

    # Gate: sharing must save searches (or at least not cost more).
    if total_saved < 0:
        lines.append(f"FAIL: sharing cost {abs(total_saved)} extra searches.")
    elif total_saved == 0:
        lines.append("INFO: sharing saved 0 searches (no shared subjects in this set).")
    else:
        lines.append(f"PASS: sharing saved {total_saved} searches vs solo runs "
                     f"({total_solos} -> {total_searches}).")

    # Gate: every accepted evidence must have been examined (judge or prefilter).
    examined = total_judge + total_prefiltered
    if examined < total_accepted:
        lines.append(f"FAIL: {total_accepted} accepted evidence but only "
                     f"{examined} examined/prefiltered pairs.")
    else:
        lines.append(f"PASS: all {total_accepted} accepted evidence were examined "
                     f"({total_judge} judged + {total_prefiltered} prefiltered).")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = run_all()
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
