"""Check a built corpus before anyone imports it.

The importer has its own guard, but a corpus is written once and read many
times. This validates the same invariants at build time, where the failing
document is still in hand:

* every doc_id is unique;
* every document has text and a title;
* every field's quote resolves against that document's text;
* every value is literally present in its quote.

A failure here is a build failure. It is not downgraded to a warning, because a
corpus with an unlocatable quote is exactly the artefact this project exists to
refuse.
"""
from __future__ import annotations

from collections import Counter

from .normalize import find_span


def _supported(value: object, quote: str, support_terms: object = ()) -> bool:
    quote_low = " ".join((quote or "").split()).lower()
    if not quote_low:
        return False
    terms = [str(t) for t in (support_terms or []) if str(t).strip()]
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, bool):
            if item and any(" ".join(t.split()).lower() in quote_low
                            for t in terms):
                continue
            needle = "true" if item else "false"
        else:
            needle = str(item)
        needle = " ".join(needle.split()).lower()
        if not needle or needle not in quote_low:
            return False
    return True


def validate(payload: dict, *, expected_count: int | None = None) -> list[str]:
    problems: list[str] = []
    version = int(payload.get("corpus_version") or 0)
    if version != 1:
        problems.append(f"corpus_version must be 1, got {version}")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return problems + ["corpus.documents must be a non-empty list"]
    schema = payload.get("schema")
    names = {str(f.get("name") or "") for f in (schema or []) if isinstance(f, dict)}
    if not names:
        problems.append("corpus.schema is empty")
    if expected_count is not None and len(documents) != expected_count:
        problems.append(f"expected {expected_count} documents, got {len(documents)}")

    seen: set[str] = set()
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            problems.append(f"document {index} is not an object")
            continue
        doc_id = str(document.get("doc_id") or "")
        if not doc_id:
            problems.append(f"document {index} has no doc_id")
            continue
        if doc_id in seen:
            problems.append(f"duplicate doc_id: {doc_id}")
        seen.add(doc_id)
        text = str(document.get("text") or "")
        if len(text.strip()) < 40:
            problems.append(f"{doc_id}: text is empty or too short")
            continue
        fields = document.get("fields")
        if not isinstance(fields, dict) or not fields:
            problems.append(f"{doc_id}: no fields")
            continue
        if "document_title" not in fields:
            problems.append(f"{doc_id}: missing document_title")
        for name, field in fields.items():
            if name not in names:
                problems.append(f"{doc_id}: field {name!r} is not in the schema")
            if not isinstance(field, dict):
                problems.append(f"{doc_id}: field {name!r} is not an object")
                continue
            quote = str(field.get("quote") or "")
            if not quote:
                problems.append(f"{doc_id}: field {name!r} has no quote")
                continue
            if find_span(text, quote) is None:
                problems.append(f"{doc_id}: field {name!r} quote does not resolve")
            if not _supported(field.get("value"), quote,
                              field.get("support_terms")):
                problems.append(f"{doc_id}: field {name!r} value is not in its quote")
    return problems


def require_valid(payload: dict, *, expected_count: int | None = None) -> None:
    problems = validate(payload, expected_count=expected_count)
    if problems:
        raise RuntimeError("corpus is not valid:\n" + "\n".join(problems))


def report(payload: dict) -> str:
    documents = payload.get("documents") or []
    types = Counter(str(d.get("doc_type") or "unknown") for d in documents)
    cells = sum(len(d.get("fields") or {}) for d in documents)
    lines = [
        "# Legal corpus build",
        "",
        f"- documents: {len(documents)}",
        f"- cells: {cells}",
        f"- generated_at: {payload.get('generated_at', '')}",
        "",
        "| document_type | documents |",
        "|---|---|",
    ]
    for name, count in sorted(types.items()):
        lines.append(f"| {name} | {count} |")
    warnings = payload.get("warnings") or []
    if warnings:
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {w}" for w in warnings)
    lines += [
        "",
        "## Storage rules",
        "",
        "The stored text is a metadata header from `sources.json` followed by the",
        "source's own body. Every field quote is a slice of that stored text; the",
        "header is never treated as body evidence.",
        "",
    ]
    return "\n".join(lines)
