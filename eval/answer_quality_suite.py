"""Suite 4 — Answer Quality.

Measures material-claim coverage, citation correctness, and the
unsupported-assertion rate. Uses the labelled assertion sets from
eval/assertion_gold.jsonl, with supporting proposals synthesized from each
claim's own text so some claims are verified and citation can be measured.

Reported separately — never blended with other suites.
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

from run_eval import ScriptedGateway, ScriptedTavily  # noqa: E402
from app.verify.orchestrator import Verifier, create_run  # noqa: E402
from app.verify.schemas import AnswerIn  # noqa: E402
from app.verify.store import LocalStore  # noqa: E402
from app.verify.coverage import audit as audit_coverage  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@dataclass
class AnswerQualityResult:
    answer_id: str
    total_assertions: int
    checked: int
    material_assertions: int
    material_coverage: float
    verified_claims: int
    cited_claims: int
    uncited_verified: int
    not_checked: int

    @property
    def citation_rate(self) -> float:
        return self.cited_claims / self.verified_claims if self.verified_claims else 1.0

    @property
    def unsupported_rate(self) -> float:
        return self.uncited_verified / self.verified_claims if self.verified_claims else 0.0


def _supporting_proposals(case: dict) -> list[dict]:
    """One supporting proposal per expected claim, quoting the claim text.

    The source URL is a per-case placeholder; the quote is the claim text
    itself, which the candidate finder will locate in the answer. This makes
    the claim `supported` with a locatable quote — the citation-correct case.
    """
    proposals = []
    for index, claim in enumerate(case.get("expected_claims", [])):
        proposals.append({
            "claim": index,
            "claim_text": claim["claim_text"],
            "url": f"https://source.example/{case['id']}-{index}",
            "relationship": "supports",
            "quote": claim["claim_text"],
            "candidate_value": "",
            "reasoning": "same text",
        })
    # One source per proposal, containing the full answer so quotes resolve.
    sources = []
    for index, claim in enumerate(case.get("expected_claims", [])):
        sources.append({
            "url": f"https://source.example/{case['id']}-{index}",
            "title": f"source {index}",
            "published_date": "2026-09-01",
            "content": case["answer"],
        })
    return proposals, sources


def run_case(case: dict) -> AnswerQualityResult:
    answer_id = case["id"]
    store = LocalStore(f"/tmp/tev-aq-{answer_id}")
    created = create_run(store, AnswerIn(
        answer=case["answer"], prompt=case.get("prompt") or "",
        generated_at=GEN, generated_at_source="user_supplied"))

    proposals, sources = _supporting_proposals(case)
    gateway = ScriptedGateway(case.get("expected_claims", []), proposals)
    tavily = ScriptedTavily(sources)
    Verifier(store, created.run_id, bifrost=gateway, tavily=tavily).execute()

    # Coverage audit from the deterministic layer.
    claims = store.list_claims(created.run_id)
    report = audit_coverage(case["answer"], claims)

    # Citation correctness: every claim with a settled verdict must carry a
    # locatable quote.
    verified_claims = 0
    cited_claims = 0
    for claim in claims:
        status = str(claim.get("status"))
        if status in ("supported", "changed", "contradicted"):
            verified_claims += 1
            evidence = store.list_evidence(str(claim["id"]))
            has_quote = any(
                bool(item.get("quote_text")) for item in evidence)
            if has_quote:
                cited_claims += 1

    return AnswerQualityResult(
        answer_id=answer_id,
        total_assertions=report.total_assertions,
        checked=report.checked,
        material_assertions=report.material_assertions,
        material_coverage=report.material_coverage,
        verified_claims=verified_claims,
        cited_claims=cited_claims,
        uncited_verified=verified_claims - cited_claims,
        not_checked=report.uncovered,
    )


def run_all(path: Path = ROOT / "eval" / "assertion_gold.jsonl"
            ) -> list[AnswerQualityResult]:
    results: list[AnswerQualityResult] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            results.append(run_case(case))
    return results


def report(results: list[AnswerQualityResult]) -> str:
    lines: list[str] = []
    lines.append("# Suite 4 — Answer Quality\n")
    lines.append("| answer | total | checked | material | coverage | verified | cited | "
                 "uncited | not_checked | citation |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(f"| {r.answer_id} | {r.total_assertions} | {r.checked} | "
                     f"{r.material_assertions} | {r.material_coverage:.1%} | "
                     f"{r.verified_claims} | {r.cited_claims} | {r.uncited_verified} | "
                     f"{r.not_checked} | {r.citation_rate:.1%} |")
    lines.append("")

    total = sum(r.total_assertions for r in results)
    checked = sum(r.checked for r in results)
    material = sum(r.material_assertions for r in results)
    verified = sum(r.verified_claims for r in results)
    cited = sum(r.cited_claims for r in results)
    uncited = sum(r.uncited_verified for r in results)
    not_checked = sum(r.not_checked for r in results)

    lines.append("| **Total** | " + " | ".join([
        str(total), str(checked), str(material),
        f"{checked/material:.1%}" if material else "—",
        str(verified), str(cited), str(uncited), str(not_checked),
        f"{cited/verified:.1%}" if verified else "—", "|"]))
    lines.append("")

    # Gate: every verified claim must carry a locatable quote.
    if uncited:
        lines.append(f"FAIL: {uncited} verified claim(s) have no citable quote.")
    else:
        lines.append("PASS: every verified claim carries a citable quote.")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = run_all()
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
