"""Publish a legal-edition export to Supabase Storage.

The export is produced by
`editions/datagoblin_legal/ingest/export_nyayapath.py`. This script uploads the
already-built files; it does not generate or transform them.

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\publish_edition.py ^
        --snapshot editions\\datagoblin_legal\\export\\nyayapath_snapshot.json ^
        --csv editions\\datagoblin_legal\\export\\nyayapath_dataset.csv

Credentials come from `SUPABASE_URL` and `SUPABASE_KEY` (service role) in the
repository's `.env`, read through the application's settings. Without them the
script writes to `--out` instead, so the same artifact can be synced locally.

The bucket must exist. `--create-bucket` creates a public bucket first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402


def _upload(client: httpx.Client, bucket: str, path: str, data: bytes,
            content_type: str) -> None:
    url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/{bucket}/{path}"
    res = client.post(url, content=data, headers={
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    })
    if res.status_code == 404 and "bucket" in res.text.lower():
        raise RuntimeError(
            f"bucket {bucket!r} does not exist; re-run with --create-bucket "
            "or create it in the Supabase dashboard")
    res.raise_for_status()


def _ensure_bucket(client: httpx.Client, bucket: str) -> None:
    url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/bucket"
    body = {"id": bucket, "name": bucket, "public": True, "file_size_limit": None}
    res = client.post(url, json=body, headers={
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": "application/json",
    })
    if res.status_code not in (200, 201, 400, 409):
        res.raise_for_status()


def _public_url(bucket: str, path: str) -> str:
    return (f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/"
            f"{bucket}/{path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--extra", action="append", default=[],
                        help="additional file to upload (repeatable)")
    parser.add_argument("--bucket", default="legal-editions")
    parser.add_argument("--prefix", default="latest")
    parser.add_argument("--out", default="",
                        help="copy files here instead of uploading")
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    files = [
        (Path(args.snapshot), "application/json"),
        (Path(args.csv), "text/csv"),
    ] + [(Path(p), "application/octet-stream") for p in args.extra]
    for file, _ in files:
        if not file.is_file():
            print(f"missing: {file}", file=sys.stderr)
            return 2

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for file, _ in files:
            (out / file.name).write_bytes(file.read_bytes())
        print(json.dumps({"copied_to": str(out),
                          "files": [f.name for f, _ in files]}, indent=2))
        return 0

    if args.dry_run:
        base = (settings.SUPABASE_URL or "https://<project>.supabase.co").rstrip("/")
        print(json.dumps({
            "bucket": args.bucket,
            "prefix": args.prefix,
            "files": [f.name for f, _ in files],
            "public_urls": [f"{base}/storage/v1/object/public/"
                            f"{args.bucket}/{args.prefix}/{f.name}"
                            for f, _ in files],
        }, indent=2))
        return 0

    if not (settings.SUPABASE_URL and settings.SUPABASE_KEY):
        print("SUPABASE_URL/SUPABASE_KEY unset; use --out for a local copy",
              file=sys.stderr)
        return 2

    try:
        with httpx.Client(timeout=60) as client:
            if args.create_bucket:
                _ensure_bucket(client, args.bucket)
            for file, content_type in files:
                path = f"{args.prefix}/{file.name}"
                _upload(client, args.bucket, path, file.read_bytes(), content_type)
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 2

    snapshot_url = _public_url(args.bucket, f"{args.prefix}/{Path(args.snapshot).name}")
    print(json.dumps({
        "bucket": args.bucket,
        "prefix": args.prefix,
        "files": [f.name for f, _ in files],
        "snapshot_url": snapshot_url,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
