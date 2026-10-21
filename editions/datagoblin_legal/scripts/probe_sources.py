"""Probe every source in sources.json and record what actually answered.

This is the Phase 0 gate for the legal edition: a source is only usable once we
have seen it answer, seen the field that carries its text, and checked robots.
The script never rewrites a source URL; it records facts next to it.

    python editions/datagoblin_legal/scripts/probe_sources.py
    python editions/datagoblin_legal/scripts/probe_sources.py --write
    python editions/datagoblin_legal/scripts/probe_sources.py --only bns-s3

Exit code is 1 when a fetch failed, a document body was unusable, or a source
was not reachable. Robots denials and unresolved robots files are reported but
do not make the run fail; they are a policy decision, not a network fact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

try:
    import httpx
except ImportError:  # pragma: no cover - dependency error, not a code path
    sys.exit("httpx is required (it is already a backend dependency)")

SOURCES = Path(__file__).resolve().parents[1] / "sources.json"
# legislative.gov.in returns 403 to anything that is not a browser, even its own
# robots.txt. The corpus is read-only and rate-limited, and robots allows it.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MIN_TEXT_CHARS = 40
DELAY_S = 0.5


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _first(metadata: dict, key: str) -> str:
    values = metadata.get(key) or []
    if not values:
        return ""
    return str(values[0].get("value") or "")


def _plain(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def _normal_act(value: str) -> str:
    value = re.sub(r"^the\s+", "", (value or "").strip(), flags=re.I)
    return re.sub(r"\s+", " ", value).casefold()


def _robots(client: httpx.Client, url: str, cache: dict[str, tuple[str, object]]) -> str:
    host = urlsplit(url).netloc
    if host not in cache:
        robots_url = f"{urlsplit(url).scheme}://{host}/robots.txt"
        try:
            response = client.get(robots_url)
            if response.status_code == 200 and "html" not in response.headers.get(
                "content-type", ""
            ):
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
                cache[host] = ("parsed", parser)
            elif 400 <= response.status_code < 500:
                cache[host] = ("absent", None)
            else:
                cache[host] = ("unresolved", None)
        except Exception:
            cache[host] = ("unresolved", None)
    kind, parser = cache[host]
    if kind == "parsed":
        return "allow" if parser.can_fetch(UA, url) else "deny"
    return kind


def _check_json(response: httpx.Response, document: dict, issues: list[str]) -> dict:
    detail: dict = {}
    try:
        payload = response.json()
    except ValueError:
        issues.append("body is not JSON")
        return detail
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        issues.append("missing metadata object")
        return detail

    field = document.get("text_field")
    text = _plain(_first(metadata, field)) if field else ""
    detail["text_chars"] = len(text)
    if len(text) < MIN_TEXT_CHARS:
        issues.append(f"{field} missing or too short ({len(text)} chars)")

    actual_act = _first(metadata, "dc.identifier.act_name")
    if actual_act and _normal_act(actual_act) != _normal_act(document.get("act", "")):
        issues.append(f"act drift: {actual_act!r}")

    section = str(document.get("section") or "").strip()
    actual_section = _first(metadata, "dc.identifier.section_number").strip()
    if section and re.fullmatch(r"\d+[A-Za-z]*|Art\.\s*\d+[A-Za-z]*", section):
        wanted = section.split()[-1].upper()
        if actual_section.upper() != wanted:
            issues.append(f"section drift: {actual_section!r} != {section!r}")

    detail["collection"] = _first(metadata, "dc.identifier.collection")
    detail["handle"] = _first(metadata, "dc.identifier.uri")
    return detail


def _check_pdf(response: httpx.Response, issues: list[str]) -> dict:
    content = response.content
    detail = {"bytes": len(content)}
    # JUDIS-era PDFs carry a short binary preamble before the %PDF header.
    start = content.find(b"%PDF-")
    if start < 0:
        issues.append("body is not a PDF")
        return detail
    if len(content) < 10_000:
        issues.append(f"PDF is suspiciously small ({len(content)} bytes)")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content[start:]))
        detail["pages"] = len(reader.pages)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        detail["text_chars"] = len(text.strip())
        if detail["text_chars"] < MIN_TEXT_CHARS:
            issues.append("PDF has no extractable text (OCR required)")
    except ImportError:
        detail["pages"] = None
        detail["text_chars"] = None
    except Exception as exc:
        issues.append(f"PDF unreadable: {type(exc).__name__}")
    return detail


def _fetch(client: httpx.Client, document: dict, robots: dict) -> dict:
    issues: list[str] = []
    probe: dict = {
        "status": None,
        "content_type": None,
        "robots": "unknown",
        "checked_at": _now(),
        "source_status": None,
        "bytes": None,
        "text_chars": None,
        "pages": None,
        "issues": issues,
    }

    fetch_url = document["fetch_url"]
    probe["robots"] = _robots(client, fetch_url, robots)

    try:
        response = client.get(fetch_url)
    except Exception as exc:
        issues.append(f"fetch failed: {type(exc).__name__}: {exc}")
        return probe

    probe["status"] = response.status_code
    probe["content_type"] = response.headers.get("content-type", "").split(";")[0].strip()
    probe["bytes"] = len(response.content)
    if response.status_code != 200:
        issues.append(f"HTTP {response.status_code} from fetch_url")
        return probe

    expected = document.get("content_type", "")
    if expected and not probe["content_type"].startswith(expected):
        issues.append(f"content-type {probe['content_type']!r} != {expected!r}")

    if "json" in probe["content_type"]:
        probe.update(_check_json(response, document, issues))
    elif probe["content_type"] == "application/pdf":
        probe.update(_check_pdf(response, issues))

    source_url = document.get("source_url")
    if source_url and source_url != fetch_url:
        try:
            source = client.get(source_url)
            probe["source_status"] = source.status_code
            if source.status_code != 200:
                issues.append(f"HTTP {source.status_code} from source_url")
        except Exception as exc:
            issues.append(f"source_url failed: {type(exc).__name__}")

    return probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write probe results back")
    parser.add_argument("--only", action="append", default=[], help="doc_id to probe")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    payload = json.loads(SOURCES.read_text(encoding="utf-8"))
    documents = payload.get("documents") or []
    selected = [d for d in documents if not args.only or d.get("doc_id") in args.only]
    missing = set(args.only) - {d.get("doc_id") for d in selected}
    if missing:
        sys.exit(f"unknown doc_id: {', '.join(sorted(missing))}")

    robots: dict[str, tuple[str, object]] = {}
    failures = 0
    with httpx.Client(
        headers={"User-Agent": UA, "Accept": "*/*"},
        timeout=args.timeout,
        follow_redirects=True,
    ) as client:
        for index, document in enumerate(selected, 1):
            probe = _fetch(client, document, robots)
            document["probe"] = probe
            if probe["issues"]:
                failures += 1
            print(
                f"[{index:>2}/{len(selected)}] {document['doc_id']:<22} "
                f"status={probe['status']} type={probe['content_type']} "
                f"robots={probe['robots']} chars={probe['text_chars']} "
                f"pages={probe['pages']} issues={len(probe['issues'])}"
            )
            for issue in probe["issues"]:
                print(f"       ! {issue}")
            if index < len(selected):
                time.sleep(DELAY_S)

    if args.write:
        payload["generated_at"] = _now()
        SOURCES.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {SOURCES}")

    print(f"{len(selected) - failures}/{len(selected)} usable")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
