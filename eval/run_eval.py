"""Gold-set evaluation for the Temporal Evidence Engine.

Two modes, for two different questions.

**Hermetic** (default) answers "is the decision and settlement layer correct?".
The claim list and the model's relationship proposals come from the gold file,
so extraction quality is held constant; what is measured is the part that must
never be probabilistic: quote location, the settlement gate, event-date reading,
rule precedence, and the final status. It needs no keys, no network, no database,
and it is byte-for-byte reproducible.

**Live** (`--live`) answers "does the whole thing work against the real world?".
It calls the configured Bifrost gateway and Tavily against `eval/gold_live.jsonl`,
whose cases name real, checkable entities. It costs money and its result depends
on what the web said that morning, which is exactly why the hermetic mode exists
as the regression gate.

Metrics that fail the run:

* hermetic: `status_accuracy` below 100% — a gold case is a rule, not a trend;
* both modes: any false `supported` — the most expensive error a verifier can
  make;
* both modes: any false `changed` — a new article misread as a new fact;
* both modes: quote validity below 100% — every stored quote must resolve at its
  offsets;
* both modes: a claim the extractor dropped, or a run that ended `failed`;
* hermetic only: event-date accuracy below 100% where the gold case states dates.

Live status accuracy is printed and is not a gate: the gold case describes the
world at authoring time, and a gate that fails when a price moves would be
measuring the news. What live mode does gate is everything the product controls.

Run:

    python eval/run_eval.py
    python eval/run_eval.py --split heldout
    python eval/run_eval.py --json
    python eval/run_eval.py --live --cases eval/gold_live.jsonl

Hermetic mode scores `eval/gold.jsonl` and `eval/gold_generated.jsonl` when both
exist. `--split` narrows to `dev` or `heldout`; the hand-written cases are `dev`
by definition because they shaped the rules, so the held-out number is measured
on generated instances only.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.bifrost.client import BifrostResult  # noqa: E402
from app.research.tavily import (ExtractOutcome, FailedUrl, SearchHit,  # noqa: E402
                                 SearchOutcome)
from app.verify.orchestrator import Verifier, create_run  # noqa: E402
from app.verify.schemas import AnswerIn  # noqa: E402
from app.verify.store import LocalStore, now  # noqa: E402

DECISIVE = {"supported", "changed", "contradicted"}


# ---------------------------------------------------------------------------
# scripted external services (hermetic mode)
# ---------------------------------------------------------------------------

class ScriptedGateway:
    """A gateway made of the gold file. It is still a real interface.

    The relationship prompt embeds the claim as a JSON line after `CLAIM:`, and
    the document windows after it. Matching a proposal against the whole prompt
    looked correct and was not: the first claim's sentence also lives in the
    stored source text, so the second claim on the same page was answered with
    the first claim's proposal. The claim is read from its own line instead.
    """

    base_url = "scripted://bifrost"

    def __init__(self, claims: list[dict], proposals: list[dict]) -> None:
        self.claims = claims
        self.proposals = proposals
        self.calls: list[str] = []

    @staticmethod
    def _claimed(prompt: str) -> dict[str, Any]:
        match = re.search(r"^CLAIM:\n(.+)$", prompt or "", re.MULTILINE)
        if not match:
            raise RuntimeError("relationship prompt has no CLAIM block")
        payload = json.loads(match.group(1))
        if not isinstance(payload, dict) or not payload.get("claim"):
            raise RuntimeError(f"CLAIM block is not a claim: {match.group(1)[:120]}")
        return payload

    def complete(self, prompt: str, *, stage: str, system: str = "",
                 model: str | None = None, claim_id: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1400,
                 json_mode: bool = True) -> BifrostResult:
        self.calls.append(stage)
        if stage == "extract_claims":
            payload: dict[str, Any] = {"claims": self.claims}
        elif stage == "repair_coverage":
            # Hermetic mode holds extraction constant: the gold file defines
            # the claims, so a repair pass must propose nothing. It is still a
            # real call on the rail, and still costs nothing here.
            payload = {"claims": []}
        else:
            claim_text = self._claimed(prompt)["claim"]
            payload = {"relationship": "silent", "quote": "",
                       "candidate_value": "", "reasoning": "no scripted proposal"}
            for proposal in self.proposals:
                # The URL must end where it ends in the prompt: `/pricing` is a
                # prefix of `/pricing-update`, and a naive substring match fed
                # the support proposal to the *update* document, which then
                # failed to locate a quote that was never in it.
                if (proposal["claim_text"] == claim_text
                        and re.search(re.escape(proposal["url"]) + r"(?![\w.\-/])",
                                      prompt)):
                    payload = {
                        "relationship": proposal["relationship"],
                        "quote": proposal["quote"],
                        "candidate_value": proposal.get("candidate_value", ""),
                        "reasoning": "scripted",
                    }
                    break
        return BifrostResult(
            text=json.dumps(payload), provider="scripted",
            model=model or "scripted/gold", request_id=f"req-{len(self.calls)}",
            input_tokens=0, output_tokens=0, cost_usd=0.0, latency_ms=0,
            fallback_attempts=0, status="live", created_at=now(), raw=payload)


class ScriptedTavily:
    """Search returns the gold sources; extract returns their stored text."""

    def __init__(self, sources: list[dict]) -> None:
        self.sources = sources

    def search(self, query: str, **_: Any) -> SearchOutcome:
        hits = [
            SearchHit(title=source.get("title") or source["url"],
                      url=source["url"], snippet="", score=0.9,
                      published_date=source.get("published_date"))
            for source in self.sources
        ]
        return SearchOutcome(query=query, hits=hits, failed=[],
                             request_id="scripted-search", credits=0,
                             status="live", raw={})

    def extract(self, urls: list[str], **_: Any) -> ExtractOutcome:
        pages = {source["url"]: source["content"] for source in self.sources
                 if source["url"] in urls}
        failed = [FailedUrl(url, "not in the gold set") for url in urls
                  if url not in pages]
        return ExtractOutcome(pages=pages, failed=failed,
                              request_id="scripted-extract", credits=0,
                              status="live", raw={})


# ---------------------------------------------------------------------------
# running one case
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    note: str
    run_status: str = ""
    claims_total: int = 0
    rows: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    #: `dev` or `heldout`. Reported separately so a final number cannot quietly
    #: be measured on the cases that shaped the rules.
    split: str = "dev"
    #: Failures that are the product's fault whatever the web said: a claim the
    #: extractor dropped, a run that ended `failed`, a stored quote that does not
    #: resolve. Live accuracy is reported separately because the world changes.
    structural: list[str] = field(default_factory=list)
    quote_total: int = 0
    quote_valid: int = 0
    event_total: int = 0
    event_correct: int = 0
    cost_usd: float = 0.0


def _answer_payload(case: dict) -> AnswerIn:
    generated = case.get("generated_at")
    return AnswerIn(
        answer=case["answer"],
        prompt=case.get("prompt") or "",
        generated_at=datetime.fromisoformat(generated.replace("Z", "+00:00"))
        if generated else None,
        generated_at_source="user_supplied" if generated else "unknown",
    )


def _evidence_for(store: LocalStore, claim_id: str) -> list[dict]:
    return store.list_evidence(claim_id)


def _quote_valid(store: LocalStore, row: dict) -> bool:
    revision = store.get_revision(str(row["source_revision_id"]))
    if not revision:
        return False
    content = str(revision.get("content") or "")
    start, end = row.get("start_char"), row.get("end_char")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if start < 0 or end > len(content) or end <= start:
        return False
    return content[start:end] == row.get("quote_text")


def run_hermetic_case(case: dict, base: Path) -> CaseResult:
    store = LocalStore(str(base / case["id"]))
    created = create_run(store, _answer_payload(case))
    proposals = []
    for proposal in case.get("proposals", []):
        claim = case["claims"][int(proposal["claim"])]
        proposals.append({**proposal, "claim_text": claim["claim_text"]})
    gateway = ScriptedGateway(case["claims"], proposals)
    tavily = ScriptedTavily(case.get("sources", []))
    Verifier(store, created.run_id, bifrost=gateway, tavily=tavily).execute()

    run = store.get_run(created.run_id) or {}
    observed = store.list_claims(created.run_id)
    result = CaseResult(case_id=case["id"], note=case.get("note", ""),
                        run_status=str(run.get("status")), claims_total=len(observed),
                        split=str(case.get("split", "dev")))
    by_text = {row["claim_text"]: row for row in observed}

    for gold in case["claims"]:
        expected = gold.get("expect") or {}
        row = by_text.get(gold["claim_text"])
        if row is None:
            reason_text = f"claim was not extracted: {gold['claim_text']!r}"
            result.failures.append(reason_text)
            result.structural.append(reason_text)
            continue
        actual = str(row.get("status"))
        reason = row.get("reason_code")
        ok_status = actual == expected.get("status")
        ok_reason = (not expected.get("reason_code")
                     or reason == expected["reason_code"])
        result.rows.append({
            "claim": gold["claim_text"],
            "expected": expected.get("status"),
            "expected_reason": expected.get("reason_code"),
            "actual": actual,
            "actual_reason": reason,
            "ok_status": ok_status,
            "ok_reason": ok_reason,
        })
        if not ok_status:
            result.failures.append(
                f"{gold['claim_text']!r}: expected {expected.get('status')} "
                f"({expected.get('reason_code')}), got {actual} ({reason})")
        elif not ok_reason:
            result.failures.append(
                f"{gold['claim_text']!r}: status matched but reason "
                f"{reason!r} != {expected.get('reason_code')!r}")

        evidence = _evidence_for(store, str(row["id"]))
        for item in evidence:
            result.quote_total += 1
            if _quote_valid(store, item):
                result.quote_valid += 1
            else:
                reason_text = (f"{gold['claim_text']!r}: a stored quote does not "
                               f"resolve at its offsets")
                result.failures.append(reason_text)
                result.structural.append(reason_text)

        if expected.get("event_dates"):
            result.event_total += 1
            actual_dates = sorted(str(item["event_date"])
                                  for item in evidence if item.get("event_date"))
            wanted = sorted(expected["event_dates"])
            if actual_dates == wanted:
                result.event_correct += 1
            else:
                result.failures.append(
                    f"{gold['claim_text']!r}: event dates {actual_dates} != {wanted}")

    result.cost_usd = float((store.gateway_stats(created.run_id) or {})
                            .get("cost_usd") or 0.0)
    if result.run_status != "completed":
        reason_text = f"run status was {result.run_status!r}, not 'completed'"
        result.failures.append(reason_text)
        result.structural.append(reason_text)
    return result


# ---------------------------------------------------------------------------
# live mode
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", (text or "").casefold())
            if len(word) > 2}


def _match_claims(gold: list[dict], observed: list[dict]) -> dict[int, dict]:
    """Greedy Jaccard matching. A missing claim stays missing; it is not filled."""
    used: set[int] = set()
    mapping: dict[int, dict] = {}
    for index, expected in enumerate(gold):
        wanted = _tokens(expected["claim_text"])
        best, best_score = None, 0.0
        for other_index, row in enumerate(observed):
            if other_index in used:
                continue
            got = _tokens(str(row.get("claim_text") or ""))
            union = wanted | got
            score = len(wanted & got) / len(union) if union else 0.0
            if score > best_score:
                best, best_score = other_index, score
        if best is not None and best_score >= 0.55:
            used.add(best)
            mapping[index] = observed[best]
    return mapping


def run_live_case(case: dict, base: Path) -> CaseResult:
    store = LocalStore(str(base / case["id"]))
    created = create_run(store, _answer_payload(case))
    Verifier(store, created.run_id).execute()

    run = store.get_run(created.run_id) or {}
    observed = store.list_claims(created.run_id)
    result = CaseResult(case_id=case["id"], note=case.get("note", ""),
                        run_status=str(run.get("status")), claims_total=len(observed),
                        split=str(case.get("split", "dev")))
    mapping = _match_claims(case["claims"], observed)

    for index, gold in enumerate(case["claims"]):
        expected = gold.get("expect") or {}
        row = mapping.get(index)
        if row is None:
            reason_text = (f"claim was not extracted or not matched: "
                           f"{gold['claim_text']!r}")
            result.failures.append(reason_text)
            result.structural.append(reason_text)
            continue
        actual = str(row.get("status"))
        reason = row.get("reason_code")
        result.rows.append({
            "claim": gold["claim_text"],
            "expected": expected.get("status"),
            "expected_reason": expected.get("reason_code"),
            "actual": actual,
            "actual_reason": reason,
            "ok_status": actual == expected.get("status"),
            "ok_reason": (not expected.get("reason_code")
                          or reason == expected["reason_code"]),
        })
        if actual != expected.get("status"):
            result.failures.append(
                f"{gold['claim_text']!r}: expected {expected.get('status')}, "
                f"got {actual} ({reason})")
        evidence = _evidence_for(store, str(row["id"]))
        for item in evidence:
            result.quote_total += 1
            if _quote_valid(store, item):
                result.quote_valid += 1
            else:
                reason_text = (f"{gold['claim_text']!r}: a stored quote does not "
                               f"resolve")
                result.failures.append(reason_text)
                result.structural.append(reason_text)
        if expected.get("event_dates"):
            result.event_total += 1
            actual_dates = sorted(str(item["event_date"])
                                  for item in evidence if item.get("event_date"))
            if actual_dates == sorted(expected["event_dates"]):
                result.event_correct += 1
            else:
                result.failures.append(
                    f"{gold['claim_text']!r}: event dates {actual_dates} != "
                    f"{sorted(expected['event_dates'])}")

    result.cost_usd = float((store.gateway_stats(created.run_id) or {})
                            .get("cost_usd") or 0.0)
    if result.run_status not in {"completed", "partial"}:
        reason_text = f"run status was {result.run_status!r}"
        result.failures.append(reason_text)
        result.structural.append(reason_text)
    return result


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------

def summarise(results: list[CaseResult], mode: str = "hermetic") -> dict[str, Any]:
    rows = [row for result in results for row in result.rows]
    total = len(rows)
    correct = sum(1 for row in rows if row["ok_status"])
    false_supported = sum(1 for row in rows
                          if row["actual"] == "supported"
                          and row["expected"] != "supported")
    false_changed = sum(1 for row in rows
                        if row["actual"] == "changed"
                        and row["expected"] != "changed")
    abstain_expected = sum(1 for row in rows if row["expected"] == "unverifiable")
    abstain_actual = sum(1 for row in rows if row["expected"] == "unverifiable"
                         and row["actual"] == "unverifiable")
    over_abstain = sum(1 for row in rows
                       if row["expected"] in DECISIVE
                       and row["actual"] == "unverifiable")
    quotes = sum(result.quote_total for result in results)
    valid = sum(result.quote_valid for result in results)
    events = sum(result.event_total for result in results)
    event_ok = sum(result.event_correct for result in results)

    summary = {
        "cases": len(results),
        "claims": total,
        "splits": {name: sum(1 for result in results if result.split == name)
                   for name in ("dev", "heldout")},
        "status_correct": correct,
        "status_accuracy": correct / total if total else 0.0,
        "false_supported": false_supported,
        "false_changed": false_changed,
        "abstention_recall": abstain_actual / abstain_expected if abstain_expected else 1.0,
        "over_abstention": over_abstain,
        "quotes": quotes,
        "quote_validity": valid / quotes if quotes else 1.0,
        "event_checks": events,
        "event_accuracy": event_ok / events if events else 1.0,
        "cost_usd": round(sum(result.cost_usd for result in results), 6),
        "structural_failures": sum(len(result.structural) for result in results),
        "failures": [f"{result.case_id}: {failure}"
                     for result in results for failure in result.failures],
    }
    # Hermetic mode is the regression gate: the gold set is a rule table, so it
    # must be exact. Live mode answers a different question. What the open web
    # says this morning is not deterministic, so gating on agreement with a
    # snapshot of it would gate on the news. Live mode gates on what the product
    # controls: every claim is extracted, every run finishes, every stored quote
    # resolves, and no claim is marked supported or changed when the gold says
    # otherwise. Accuracy is printed either way; it is evidence, not a switch.
    summary["gate_basis"] = (
        "status accuracy, false supported/changed, quote validity, event dates"
        if mode == "hermetic" else
        "quote validity, no false supported/changed, every claim extracted and "
        "every run finished"
    )
    summary["gate"] = (
        summary["quote_validity"] >= 1.0
        and summary["false_supported"] == 0
        and summary["false_changed"] == 0
        and summary["structural_failures"] == 0
    )
    if mode == "hermetic":
        summary["gate"] = (summary["gate"]
                           and summary["status_accuracy"] >= 1.0
                           and summary["event_accuracy"] >= 1.0)
    return summary


def _print_markdown(results: list[CaseResult], summary: dict[str, Any],
                    mode: str) -> None:
    print(f"# Gold evaluation — {mode}\n")
    print("| case | claims | status | quotes | event dates | notes |")
    print("|---|---|---|---|---|---|")
    for result in results:
        status = (f"{sum(1 for row in result.rows if row['ok_status'])}"
                  f"/{len(result.rows)}")
        quotes = (f"{result.quote_valid}/{result.quote_total}"
                  if result.quote_total else "—")
        events = (f"{result.event_correct}/{result.event_total}"
                  if result.event_total else "—")
        note = result.note if not result.failures else result.failures[0]
        print(f"| `{result.case_id}` | {result.claims_total} | {status} | "
              f"{quotes} | {events} | {note} |")
    print()
    print(f"- cases: {summary['cases']}")
    print(f"- splits: {summary['splits']['dev']} dev / "
          f"{summary['splits']['heldout']} held out")
    print(f"- claims: {summary['claims']}")
    print(f"- status accuracy: {summary['status_accuracy']:.1%}")
    print(f"- false supported: {summary['false_supported']}")
    print(f"- false changed: {summary['false_changed']}")
    print(f"- abstention recall: {summary['abstention_recall']:.1%}")
    print(f"- over-abstention (decisive expected): {summary['over_abstention']}")
    print(f"- quote validity: {summary['quote_validity']:.1%} "
          f"({summary['quotes']} quotes)")
    print(f"- event-date accuracy: {summary['event_accuracy']:.1%} "
          f"({summary['event_checks']} checks)")
    print(f"- recorded model cost: ${summary['cost_usd']:.4f}")
    print(f"- gate basis: {summary['gate_basis']}")
    print()
    if summary["failures"]:
        print("## Failures\n")
        for failure in summary["failures"]:
            print(f"- {failure}")
        print()
    print(f"**Gate: {'PASS' if summary['gate'] else 'FAIL'}**")


def load_cases(path: Path, limit: int | None, split: str = "all") -> list[dict]:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number}: invalid JSON: {exc}") from exc
        if split != "all" and case.get("split", "dev") != split:
            continue
        cases.append(case)
    if limit:
        cases = cases[:limit]
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", default=None,
                        help="one or more JSONL case files; hermetic defaults to "
                             "gold.jsonl + gold_generated.jsonl, live to "
                             "gold_live.jsonl")
    parser.add_argument("--mode", choices=["hermetic", "live"], default="hermetic")
    parser.add_argument("--live", action="store_true", help="alias for --mode live")
    parser.add_argument("--split", choices=["all", "dev", "heldout"],
                        default="all",
                        help="score only the dev or held-out cases")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--pause", type=float, default=0.0,
                        help="seconds between live cases; paced for a free-tier "
                             "token-per-minute limit")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()

    mode = "live" if args.live else args.mode
    if args.cases:
        paths = [Path(item) for item in args.cases]
    elif mode == "live":
        paths = [ROOT / "eval" / "gold_live.jsonl"]
    else:
        paths = [ROOT / "eval" / "gold.jsonl"]
        generated = ROOT / "eval" / "gold_generated.jsonl"
        if generated.is_file():
            paths.append(generated)

    cases: list[dict] = []
    for path in paths:
        if not path.is_file():
            print(f"case file not found: {path}", file=sys.stderr)
            return 2
        cases.extend(load_cases(path, None, args.split))
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

    if mode == "live":
        # Fail before spending anything, not halfway through a case.
        from app.core.config import settings
        missing = []
        if not settings.BIFROST_VIRTUAL_KEY:
            missing.append("BIFROST_VIRTUAL_KEY")
        if not settings.TAVILY_API_KEY:
            missing.append("TAVILY_API_KEY")
        if missing:
            print(f"live mode needs {', '.join(missing)} in the environment",
                  file=sys.stderr)
            return 2

    base = Path(tempfile.mkdtemp(prefix="tev-eval-"))
    try:
        runner = run_hermetic_case if mode == "hermetic" else run_live_case
        results = []
        for index, case in enumerate(cases):
            if mode == "live" and index and args.pause > 0:
                time.sleep(args.pause)
            results.append(runner(case, base))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    summary = summarise(results, mode)
    if args.json:
        print(json.dumps({
            "mode": mode,
            "summary": summary,
            "cases": [
                {
                    "id": result.case_id,
                    "run_status": result.run_status,
                    "claims": result.claims_total,
                    "rows": result.rows,
                    "quote_validity": (result.quote_valid / result.quote_total
                                       if result.quote_total else 1.0),
                    "event_accuracy": (result.event_correct / result.event_total
                                       if result.event_total else 1.0),
                    "cost_usd": result.cost_usd,
                    "failures": result.failures,
                }
                for result in results
            ],
        }, indent=2))
    else:
        _print_markdown(results, summary, mode)
    return 0 if summary["gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
