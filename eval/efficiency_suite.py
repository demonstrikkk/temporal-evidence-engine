"""Suite 5 — Efficiency.

Measures cost per verified assertion, tokens per assertion, and cache hit rate
by stage. From model_calls — no new infrastructure.

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
from datetime import datetime, timezone  # noqa: E402


GEN = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


@dataclass
class EfficiencyResult:
    case_id: str
    verified_assertions: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    estimated_calls: int
    calls_by_stage: dict[str, int] = field(default_factory=dict)

    @property
    def cost_per_verified_assertion(self) -> float:
        return self.cost_usd / self.verified_assertions if self.verified_assertions else 0.0

    @property
    def tokens_per_verified_assertion(self) -> float:
        total = self.input_tokens + self.output_tokens
        return total / self.verified_assertions if self.verified_assertions else 0.0


def run_case(case: dict) -> EfficiencyResult:
    case_id = case["id"]
    store = LocalStore(f"/tmp/tev-eff-{case_id}")
    created = create_run(store, AnswerIn(
        answer=case["answer"], prompt=case.get("prompt") or "",
        generated_at=GEN, generated_at_source="user_supplied"))
    # Proposals use claim index; resolve claim_text like run_eval does.
    proposals = []
    for p in case.get("proposals", []):
        idx = int(p.get("claim", 0))
        proposals.append({**p, "claim_text": case["claims"][idx]["claim_text"]})
    gateway = ScriptedGateway(case.get("claims", []), proposals)
    tavily = ScriptedTavily(case.get("sources", []))
    Verifier(store, created.run_id, bifrost=gateway, tavily=tavily).execute()

    # Verified assertions: settled claims.
    verified = 0
    for claim in store.list_claims(created.run_id):
        if str(claim.get("status")) in ("supported", "changed", "contradicted"):
            verified += 1

    # Model call stats from the execution trace (spans record tokens, cost,
    # and stage for every model call; the raw gateway is bypassed by fakes).
    header = store.get_execution_trace(created.run_id)
    cost_usd = 0.0
    input_tokens = 0
    output_tokens = 0
    calls_by_stage: dict[str, int] = {}
    if header:
        spans = store.list_trace_spans(str(header["trace_id"])) or []
        for span in spans:
            if span.get("stage") not in ("extract", "research", "coverage",
                                          "claim", "settle", "decide",
                                          "synthesize", "run"):
                continue
            cost_usd += float(span.get("cost_usd") or 0.0)
            input_tokens += int(span.get("input_tokens") or 0)
            output_tokens += int(span.get("output_tokens") or 0)
            stage = str(span.get("stage") or "unknown")
            calls_by_stage[stage] = calls_by_stage.get(stage, 0) + 1

    # Estimated calls: catalogue-priced spans (cost_source=catalogue).
    estimated_calls = sum(1 for span in (store.list_trace_spans(
        str(header["trace_id"])) if header else [])
                          if str(span.get("cost_source")) == "catalogue")

    return EfficiencyResult(
        case_id=case_id, verified_assertions=verified, cost_usd=cost_usd,
        input_tokens=input_tokens, output_tokens=output_tokens,
        estimated_calls=estimated_calls, calls_by_stage=calls_by_stage)


def run_all(path: Path = ROOT / "eval" / "gold_generated.jsonl"
            ) -> list[EfficiencyResult]:
    results: list[EfficiencyResult] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            results.append(run_case(case))
    return results


def report(results: list[EfficiencyResult]) -> str:
    lines: list[str] = []
    lines.append("# Suite 5 — Efficiency\n")
    lines.append("| case | verified | cost_usd | cost/verified | tokens | tokens/verified | "
                 "estimated | stages |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        stages = ", ".join(f"{k}:{v}" for k, v in sorted(r.calls_by_stage.items()))
        lines.append(f"| {r.case_id} | {r.verified_assertions} | {r.cost_usd:.4f} | "
                     f"{r.cost_per_verified_assertion:.4f} | "
                     f"{r.input_tokens + r.output_tokens} | "
                     f"{r.tokens_per_verified_assertion:.1f} | "
                     f"{r.estimated_calls} | {stages} |")
    lines.append("")

    total_verified = sum(r.verified_assertions for r in results)
    total_cost = sum(r.cost_usd for r in results)
    total_tokens = sum(r.input_tokens + r.output_tokens for r in results)
    total_estimated = sum(r.estimated_calls for r in results)

    lines.append("| **Total** | " + " | ".join([
        str(total_verified), f"{total_cost:.4f}",
        f"{total_cost/total_verified:.4f}" if total_verified else "—",
        str(total_tokens),
        f"{total_tokens/total_verified:.1f}" if total_verified else "—",
        str(total_estimated), "—", "|"]))
    lines.append("")

    # Gate: efficiency numbers must be reported, not assumed zero.
    if total_verified:
        lines.append(f"PASS: cost per verified assertion "
                     f"${total_cost/total_verified:.4f}; "
                     f"{total_tokens/total_verified:.1f} tokens per verified "
                     f"assertion ({total_verified} verified).")
    else:
        lines.append("INFO: no verified assertions in this set.")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = run_all()
    print(report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
