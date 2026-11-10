"""Offline coverage scorecard for one stored dataset. Read-only, no network.

    python scripts/coverage_scorecard.py [dataset_id] [--json]

Answers "did coverage actually get better, and why" without spending a search or
a provider call. For each field it reports how many stored records carry a value,
how many of those are `verified`, and how many carry no value at all. It also
reports the crawl's truncation causes from the run counters, because a thin
column is only actionable if the run can say whether it ran out of page budget,
hit a per-domain cap, or never went past its seeds.

`live_scrape_check.py` is the live counterpart: it runs a prompt end to end.
This one reads a dataset that already exists, which makes it the right baseline
tool before and after a change.

Omit the dataset id to use the most recent dataset in the store.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories.factory import build_repo  # noqa: E402
from app.services import coverage as coverage_svc  # noqa: E402
from app.services import gaps as gaps_svc  # noqa: E402
from app.services import quality as quality_svc  # noqa: E402

#: Enough for any dataset a single run produces. `get_records` pages in SQL, so
#: this is a ceiling on the read, not a scan of the table.
_PAGE = 1000
_MAX_RECORDS = 20000


def _field_names(schema: list) -> list[str]:
    return [coverage_svc._field_name(f) for f in (schema or [])]


def _records(repo, dataset_id: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while len(out) < _MAX_RECORDS:
        batch = (repo.get_records(dataset_id, "", _PAGE, offset) or {}).get("records") or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += len(batch)
    return out


def _latest_dataset(repo) -> str:
    rows = repo.list_datasets(25) or []
    for row in rows:
        if row.get("id"):
            return str(row["id"])
    return ""


def _citation_report(records: list[dict], repo) -> dict:
    """Do the stored citations still resolve against the stored pages?

    This is the deterministic half of an evaluation: no model, no network, and
    no judgement. `validator.settle` writes the located slice as the quote, so
    on the verified path a mismatch is a real regression rather than a fuzzy
    threshold. Cells whose page cannot be found are `no_text`, not excused:
    an unverifiable citation is the defect this exists to surface.
    """
    page_ids: set[str] = set()
    for rec in records:
        for cell in (rec.get("fields") or {}).values():
            pid = str(((cell or {}).get("source") or {}).get("page_id") or "")
            if pid:
                page_ids.add(pid)
    texts: dict[str, str] = {}
    if page_ids:
        getter = getattr(repo, "get_pages_by_ids", None)
        if callable(getter):
            try:
                for page in getter(sorted(page_ids)) or []:
                    texts[str(page.get("id") or "")] = str(page.get("markdown") or "")
            except Exception:  # noqa: BLE001 (a read failure is reported as no_text)
                texts = {}
    pairs = []
    temporal: dict[str, int] = {}
    dated = 0
    # How the values arrived. This is the ladder's measurement: a run that reads
    # its columns from JSON-LD or from labelled lines and never asks the model is
    # cheaper *and* no less verified, and without this the only evidence was the
    # per-cell tooltip. Counted only over cells that carry a value.
    read_methods: dict[str, int] = {}
    for rec in records:
        for cell in (rec.get("fields") or {}).values():
            cell = cell or {}
            pid = str((cell.get("source") or {}).get("page_id") or "")
            pairs.append((cell, texts.get(pid, "")))
            if str(cell.get("valid_time") or "").strip():
                dated += 1
            status = str((cell.get("temporal_check") or {}).get("status") or "")
            if status:
                temporal[status] = temporal.get(status, 0) + 1
            if str(cell.get("value") or "") == "":
                continue
            method = str(cell.get("read_method") or "").strip() or "unrecorded"
            read_methods[method] = read_methods.get(method, 0) + 1
    report = quality_svc.score(pairs)
    report["pages_cited"] = len(page_ids)
    report["pages_found"] = len(texts)
    report["cells_dated"] = dated
    report["temporal_statuses"] = temporal
    report["read_methods"] = read_methods
    return report


def _report(repo, dataset_id: str) -> dict:
    dataset = repo.get_dataset_row(dataset_id) or {}
    schema = dataset.get("schema") or []
    records = _records(repo, dataset_id)
    coverage = coverage_svc.field_coverage(records, schema)
    derived = gaps_svc.derive(coverage)
    return {
        "dataset_id": dataset_id,
        "run_id": dataset.get("run_id", ""),
        "name": dataset.get("name", ""),
        "records": coverage.get("records", len(records)),
        "schema": _field_names(schema),
        "fields": coverage.get("fields", []),
        "gaps": derived,
        "citations": _citation_report(records, repo),
        "temporal": dataset.get("temporal") or {},
        "counts": dataset.get("counts") or {},
    }


def _print(report: dict) -> None:
    print(f"dataset : {report['dataset_id']}")
    if report.get("name"):
        print(f"name    : {report['name']}")
    print(f"run     : {report.get('run_id') or '(unknown)'}")
    print(f"records : {report['records']}")
    print()
    print(f"{'field':<28} {'present':>7} {'verified':>8} {'missing':>8} "
          f"{'cover%':>7} {'proven%':>8}")
    for f in report["fields"]:
        print(f"{str(f['field']):<28} {f['present']:>7} {f['verified']:>8} "
              f"{f['missing']:>8} {f['coverage_pct']:>7.1f} {f['proven_pct']:>8.1f}")
    if report["gaps"]:
        print()
        print("open gaps (by outstanding cells):")
        for g in report["gaps"]:
            print(f"  {str(g['field']):<28} {g['category']:<14} "
                  f"missing={g['missing']} unverified={g['unverified']} "
                  f"conflicting={g['conflicting']}")
    traversal = (report.get("counts") or {}).get("traversal") or {}
    if traversal:
        print()
        print("crawl:")
        for key in ("budget", "seeds_queued", "seeds_dropped", "traversed_pages",
                    "depth_reached", "max_depth", "per_domain_cap",
                    "blocked_by_domain_cap", "blocked_by_budget"):
            if key in traversal:
                print(f"  {key:<22} {traversal[key]}")
    citations = report.get("citations") or {}
    if citations:
        print()
        print("citations (deterministic, no model):")
        print(f"  valued cells           {citations.get('valued', 0)}")
        print(f"  exact slice            {citations.get('exact', 0)}")
        print(f"  located after normal.  {citations.get('located', 0)}")
        print(f"  unlocatable            {citations.get('unlocatable', 0)}")
        print(f"  offset mismatch        {citations.get('offset_mismatch', 0)}")
        print(f"  no quote               {citations.get('no_quote', 0)}")
        print(f"  page text missing      {citations.get('no_text', 0)}")
        print(f"  resolution rate        {citations.get('resolution_rate', 0.0):.3f}")
        print(f"  pages cited / found    {citations.get('pages_cited', 0)} / "
              f"{citations.get('pages_found', 0)}")
        print(f"  cells with valid_time  {citations.get('cells_dated', 0)}")
        statuses = citations.get("temporal_statuses") or {}
        if statuses:
            print("  temporal check         " + ", ".join(
                f"{k}={v}" for k, v in sorted(statuses.items())))
        methods = citations.get("read_methods") or {}
        if methods:
            print("  read methods           " + ", ".join(
                f"{k}={v}" for k, v in
                sorted(methods.items(), key=lambda kv: (-kv[1], kv[0]))))
    temporal = report.get("temporal") or {}
    if temporal.get("kind") and temporal.get("kind") != "none":
        print()
        print(f"requested time: {temporal.get('kind')} "
              f"({temporal.get('as_of') or temporal.get('start', '')} -> "
              f"{temporal.get('end', '')})")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    want_json = "--json" in sys.argv[1:]
    repo = build_repo()
    try:
        dataset_id = args[0] if args else _latest_dataset(repo)
        if not dataset_id:
            print("no dataset id given and the store has no datasets")
            return 2
        report = _report(repo, dataset_id)
    finally:
        closer = getattr(repo, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
    if want_json:
        print(json.dumps(report, indent=1, default=str))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
