"""Build the legal corpus from sources.json.

    backend\\.venv\\Scripts\\python.exe editions\\datagoblin_legal\\ingest\\build.py --write

The output is `corpus/documents.json` (full stored text and quote-backed
fields), `corpus/index.json` (one summary row per document) and
`corpus/report.md`. The build is deterministic apart from `generated_at` and
`retrieved_at`: the same fetched bytes produce the same text and the same
quotes.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ingest.fetch import fetch
    from ingest.normalize import compose, fields_for, tier_for
    from ingest.parse import body_text, constitution_article
    from ingest.validate import report, require_valid, validate
else:
    from .fetch import fetch
    from .normalize import compose, fields_for, tier_for
    from .parse import body_text, constitution_article
    from .validate import report, require_valid, validate

EDITION_DIR = Path(__file__).resolve().parents[1]
SOURCES = EDITION_DIR / "sources.json"
CORPUS = EDITION_DIR / "corpus"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def _edition() -> dict:
    return json.loads((EDITION_DIR / "edition.json").read_text(encoding="utf-8"))


def _tiering_policy() -> dict:
    """How a document is placed in a tier, read from the manifest.

    The policy is data because it is legal knowledge: which states are union
    law, and which state this edition serves. It is declared next to the
    sources it describes, so adding a state is an edit to a list rather than
    an edit to the code that reads it.
    """
    return _edition().get("tiering") or {}


def _summary(document: dict) -> dict:
    return {
        "doc_id": document["doc_id"],
        "doc_type": document.get("doc_type", ""),
        "title": document.get("title", ""),
        "citation": document.get("citation", ""),
        "court": document.get("court", ""),
        "jurisdiction": document.get("jurisdiction", ""),
        "tier": document.get("tier", ""),
        "year": document.get("year"),
        "source_url": document.get("source_url", ""),
        "fetch_url": document.get("fetch_url", ""),
        "text_chars": document.get("text_chars", 0),
        "content_hash": document.get("content_hash", ""),
        "fields": sorted((document.get("fields") or {}).keys()),
    }


def build(documents: list[dict], *, cache_dir: Path, refresh: bool = False,
          timeout: float = 90.0) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    built: list[dict] = []
    policy = _tiering_policy()
    for index, source in enumerate(documents, 1):
        doc_id = str(source.get("doc_id") or "")
        try:
            fetched = fetch(source, cache_dir=cache_dir, refresh=refresh,
                            timeout=timeout)
            body = body_text(source, fetched["content"],
                             fetched.get("content_type", ""))
            if source.get("doc_type") == "constitution":
                body = constitution_article(
                    body, str(source.get("section") or ""),
                    str(source.get("title") or ""))
            # The tier is derived here and stored in the header, so the
            # bucket rule that reads it is a value in the text rather than a
            # place name written into code.
            source = {**source, "tier": tier_for(source, policy)}
            text, lines = compose(source, body)
            fields = fields_for(source, text, lines, body)
            if "document_title" not in fields:
                raise ValueError("metadata title could not be placed in the stored text")
            document = {
                **{k: source.get(k) for k in (
                    "doc_id", "doc_type", "title", "citation", "act", "short_act",
                    "section", "court", "jurisdiction", "state", "tier", "year",
                    "source_url", "fetch_url", "content_type", "text_field")},
                "retrieved_at": fetched.get("fetched_at", ""),
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "text_chars": len(text),
                "fields": fields,
                "warnings": [],
            }
            built.append(document)
            print(f"[{index:>2}/{len(documents)}] {doc_id:<32} "
                  f"chars={len(text):>7} fields={len(fields):>2} "
                  f"{'(cache)' if fetched.get('from_cache') else ''}")
        except Exception as exc:  # noqa: BLE001 - one document must not hide the rest
            warnings.append(f"{doc_id}: {type(exc).__name__}: {exc}")
            print(f"[{index:>2}/{len(documents)}] {doc_id:<32} FAILED: {exc}",
                  file=sys.stderr)

    edition = _edition()
    payload = {
        "corpus_version": 1,
        "edition": edition.get("name", ""),
        "edition_version": edition.get("version", ""),
        "entity": edition.get("entity", "legal_document"),
        "name": "Indian legal corpus",
        "generated_at": _now(),
        "schema": edition.get("fields", []),
        "buckets": edition.get("buckets", []),
        "tiering": edition.get("tiering", {}),
        "dedupe_keys": edition.get("dedupe_keys", []),
        "allowed_sources": edition.get("allowed_sources", []),
        "seed_domains": edition.get("seed_domains", []),
        "validation_rules": edition.get("validation_rules", []),
        "documents": built,
        "warnings": warnings,
    }
    return payload, warnings


def write(payload: dict) -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    documents_path = CORPUS / "documents.json"
    index_path = CORPUS / "index.json"
    report_path = CORPUS / "report.md"
    documents_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    index_path.write_text(
        json.dumps({
            "corpus_version": 1,
            "edition": payload.get("edition", ""),
            "generated_at": payload.get("generated_at", ""),
            "documents": [_summary(d) for d in payload.get("documents") or []],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    report_path.write_text(report(payload), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write corpus/ files")
    parser.add_argument("--only", action="append", default=[], help="doc_id to build")
    parser.add_argument("--refresh", action="store_true", help="ignore the fetch cache")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--cache-dir", default=str(CORPUS / ".cache"))
    args = parser.parse_args(argv)

    source_payload = json.loads(SOURCES.read_text(encoding="utf-8"))
    documents = source_payload.get("documents") or []
    selected = [d for d in documents if not args.only or d.get("doc_id") in args.only]
    missing = set(args.only) - {d.get("doc_id") for d in selected}
    if missing:
        sys.exit(f"unknown doc_id: {', '.join(sorted(missing))}")

    payload, warnings = build(selected, cache_dir=Path(args.cache_dir),
                              refresh=args.refresh, timeout=args.timeout)
    if warnings:
        print("\n".join(warnings), file=sys.stderr)
    if len(payload["documents"]) != len(selected):
        print(f"{len(payload['documents'])}/{len(selected)} documents built; "
              "refusing to write", file=sys.stderr)
        return 1
    # A partial `--only` build is a view, not a corpus: validating it against
    # the edition is useful, but writing it would replace the full corpus.
    if args.only:
        problems = validate(payload)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"{len(payload['documents'])} documents validated (not written: --only)")
        return 0
    require_valid(payload, expected_count=len(documents))

    if args.write:
        write(payload)
        print(f"wrote {CORPUS / 'documents.json'}")
        print(report(payload))
    else:
        print(report(payload))
        print("dry run; pass --write to replace corpus/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
