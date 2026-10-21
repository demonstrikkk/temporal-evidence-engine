"""Re-fetch the corpus and report which documents changed.

    backend\\.venv\\Scripts\\python.exe editions\\datagoblin_legal\\ingest\\refresh.py

This is deliberately read-only: it does not replace `corpus/documents.json`.
A changed source is an editorial decision -- the old text is the evidence a
stored cell cites, so replacing it is `build.py --write`, run on purpose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ingest.build import CORPUS, SOURCES, build
else:
    from .build import CORPUS, SOURCES, build


def main() -> int:
    existing_path = CORPUS / "documents.json"
    old = {}
    if existing_path.is_file():
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
        old = {d["doc_id"]: d.get("content_hash", "")
               for d in payload.get("documents") or []}

    documents = json.loads(SOURCES.read_text(encoding="utf-8")).get("documents") or []
    payload, warnings = build(documents, cache_dir=CORPUS / ".cache", refresh=True)
    changed = [d for d in payload.get("documents") or []
               if old.get(d["doc_id"]) != d.get("content_hash")]
    added = [d for d in payload.get("documents") or [] if d["doc_id"] not in old]
    removed = sorted(set(old) - {d["doc_id"] for d in payload.get("documents") or []})

    print(f"{len(payload.get('documents') or [])} fetched, "
          f"{len(changed)} changed, {len(added)} added, {len(removed)} removed")
    for document in changed:
        print(f"  changed {document['doc_id']}")
    for doc_id in removed:
        print(f"  removed {doc_id}")
    for warning in warnings:
        print(f"  ! {warning}", file=sys.stderr)
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
