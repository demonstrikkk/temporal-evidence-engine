"""Freeze part of a local store into a reviewable demo snapshot.

A demo that needs provider keys is a demo most judges cannot run. This script
takes runs that were actually executed against the real gateway and the real
search API and copies them, with their original ids and timestamps, into
`demo/state.json`. Nothing is synthesised: every quote in the snapshot resolves
against stored text, and every verdict was produced by the pipeline from that
text.

What is kept, and why:

* `answers`, `runs`, `claims`, `sources`, `revisions`, `evidence`, `links`,
  `assertions`, `history`, `events` - the run itself.
* `calls` - the recorded model calls. Replaying one needs the exact prompt hash,
  which is why the snapshot is only useful for the code version that recorded it;
  a later prompt change invalidates it on purpose.
* `cache` - the recorded search and extraction responses.

What is dropped, and why:

* `cache_entries` - the L2/L5 layers. Keeping them would let a reverify return a
  cached *verdict*; dropping them makes the demo replay the model calls and
  recompute every status from stored text, which is the property being shown.
* any run not named on the command line.

Refuses to write a snapshot that contains a `running` run, a `pending` claim, or
a quote that does not resolve at its offsets. A demo that quietly ships a broken
row is worse than no demo.

Usage (from the repo root):

    python scripts/freeze_demo_snapshot.py --source .tev --run <id> [--run <id>]

Run ids may be prefixes, as long as each prefix is unambiguous.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Collections copied verbatim from the store.
KEPT = ("answers", "runs", "claims", "sources", "revisions", "evidence",
        "links", "assertions", "history", "events", "calls", "cache",
        "quota_ledger")

#: Collections deliberately left behind (see the module docstring).
DROPPED = ("cache_entries", "queries", "conversations", "messages")


def _fail(message: str) -> None:
    print(f"refusing to freeze: {message}", file=sys.stderr)
    raise SystemExit(1)


def _resolve(store: dict, prefix: str) -> dict:
    matches = [run for run in store.get("runs") or []
               if str(run.get("id", "")).startswith(prefix)]
    if len(matches) != 1:
        _fail(f"run prefix {prefix!r} matched {len(matches)} runs")
    return matches[0]


def _payload_dict(row: dict) -> dict:
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        for loader in (json.loads, ast.literal_eval):
            try:
                loaded = loader(payload)
            except (ValueError, SyntaxError):
                continue
            if isinstance(loaded, dict):
                return loaded
    return {}


def _needed_cache(state: dict, run_ids: set[str]) -> list[dict]:
    """Keep only the search/extract responses this run actually asked for.

    The cache is global and keyed by request, so a store with a dozen runs holds
    pages from all of them. Shipping all of it made a one-run snapshot 5 MB,
    most of it text no claim in the snapshot cites.
    """
    queries: set[str] = set()
    urls: set[str] = set()
    for event in state.get("events") or []:
        if event.get("run_id") not in run_ids:
            continue
        payload = event.get("payload") or {}
        if event.get("event_type") == "search.done" and payload.get("query"):
            queries.add(str(payload["query"]))
        if event.get("event_type") == "source.stored" and payload.get("url"):
            urls.add(str(payload["url"]))

    kept = []
    for row in state.get("cache") or []:
        payload = _payload_dict(row)
        tool = str(row.get("tool") or "")
        if tool == "search" and str(payload.get("query") or "") in queries:
            kept.append(row)
        elif tool == "extract":
            got = {str(item.get("url") or "")
                   for item in payload.get("results") or []
                   if isinstance(item, dict)}
            if got & urls:
                kept.append(row)
    return kept


def build(source: Path, prefixes: list[str]) -> dict:
    state = json.loads((source / "state.json").read_text(encoding="utf-8"))
    runs = [_resolve(state, prefix) for prefix in prefixes]

    for run in runs:
        if run.get("status") in ("running",):
            _fail(f"run {run['id']} is still marked running")
        if run.get("status") == "failed":
            _fail(f"run {run['id']} ended failed; a failure belongs in the "
                  "failure-path capture, not the demo")

    answer_ids = {run["answer_id"] for run in runs}
    run_ids = {run["id"] for run in runs}
    claims = [row for row in state.get("claims") or []
              if row.get("run_id") in run_ids]
    pending = [row for row in claims if row.get("status") == "pending"]
    if pending:
        _fail(f"{len(pending)} claim(s) are still pending")

    claim_ids = {row["id"] for row in claims}
    links = [row for row in state.get("links") or []
             if row.get("claim_id") in claim_ids]
    evidence_ids = {row["evidence_id"] for row in links}
    evidence = [row for row in state.get("evidence") or []
                if row.get("id") in evidence_ids]

    revisions_by_id = {row["id"]: row for row in state.get("revisions") or []}
    source_ids = set()
    for row in evidence:
        revision = revisions_by_id.get(row.get("source_revision_id"))
        if not revision:
            _fail(f"evidence {row['id']} cites a revision that is not stored")
        content = str(revision.get("content") or "")
        start, end = row.get("start_char"), row.get("end_char")
        if not (isinstance(start, int) and isinstance(end, int)
                and 0 <= start < end <= len(content)
                and content[start:end] == row.get("quote_text")):
            _fail(f"evidence {row['id']} does not resolve at its offsets")
        source_ids.add(revision["source_id"])

    sources = [row for row in state.get("sources") or []
               if row.get("id") in source_ids]
    revisions = [revisions_by_id[row["source_revision_id"]]
                 for row in evidence]

    snapshot: dict[str, list[dict]] = {name: [] for name in KEPT}
    snapshot["answers"] = [row for row in state.get("answers") or []
                           if row.get("id") in answer_ids]
    snapshot["runs"] = runs
    snapshot["claims"] = claims
    snapshot["sources"] = sources
    snapshot["revisions"] = revisions
    snapshot["evidence"] = evidence
    snapshot["links"] = links
    snapshot["assertions"] = [row for row in state.get("assertions") or []
                              if row.get("claim_id") in claim_ids]
    snapshot["history"] = [row for row in state.get("history") or []
                           if row.get("claim_id") in claim_ids]
    snapshot["events"] = [row for row in state.get("events") or []
                          if row.get("run_id") in run_ids]
    snapshot["calls"] = [row for row in state.get("calls") or []
                         if row.get("run_id") in run_ids]
    snapshot["cache"] = _needed_cache(state, run_ids)
    snapshot["quota_ledger"] = list(state.get("quota_ledger") or [])

    for name in DROPPED:
        snapshot.pop(name, None)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(REPO / ".tev"))
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--out", default=str(REPO / "demo" / "state.json"))
    args = parser.parse_args()

    source = Path(args.source)
    if not (source / "state.json").is_file():
        _fail(f"no state.json under {source}")

    snapshot = build(source, args.run)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "note": ("Recorded runs, replayed from the store. Not live: every run "
                 "keeps the timestamp it was originally executed at."),
        "recorded_from": str(source.name),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "runs": [{"id": run["id"], "status": run["status"],
                  "started_at": run.get("started_at")}
                 for run in snapshot["runs"]],
    }
    payload = {"demo_meta": header, **snapshot}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    size = out.stat().st_size

    claims = snapshot["claims"]
    by_status: dict[str, int] = {}
    for claim in claims:
        by_status[claim["status"]] = by_status.get(claim["status"], 0) + 1
    print(f"wrote {out} ({size / 1e6:.2f} MB)")
    print(f"runs: {len(snapshot['runs'])} | claims: {len(claims)} "
          f"({by_status}) | sources: {len(snapshot['sources'])} | "
          f"evidence: {len(snapshot['evidence'])} | calls: {len(snapshot['calls'])} "
          f"| search cache: {len(snapshot['cache'])} | events: "
          f"{len(snapshot['events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
