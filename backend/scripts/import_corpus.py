"""Import an offline corpus file into the configured repository.

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\import_corpus.py \\
        --corpus editions\\datagoblin_legal\\corpus\\documents.json

The corpus is validated first: every field quote must resolve against its
document's stored text, and every value must be contained in that quote. A bad
corpus is a named 422-style error, not a half-written run.

This is the path for a curated edition with more documents than a live run's
seed cap. It writes a normal run, pages, records and dataset through the same
repository methods the pipeline uses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.core.errors import AppError  # noqa: E402
from app.repositories.factory import build_repo  # noqa: E402
from app.services.corpus_import import import_corpus, load_corpus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="path to documents.json")
    parser.add_argument("--workflow-id", default="legal_corpus",
                        help="workflow id recorded on the run")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the corpus, write nothing")
    args = parser.parse_args(argv)

    try:
        payload = load_corpus(args.corpus)
        if args.dry_run:
            print(json.dumps({
                "corpus": args.corpus,
                "documents": len(payload.get("documents") or []),
                "fields": len(payload.get("schema") or []),
                "valid": True,
            }, indent=2))
            return 0

        repo = build_repo()
        try:
            result = import_corpus(repo, payload, workflow_id=args.workflow_id)
        finally:
            close = getattr(repo, "close", None)
            if callable(close):
                close()
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cannot read corpus: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
