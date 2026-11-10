"""Import an offline, quote-backed corpus as a normal dataset.

The live pipeline discovers and fetches pages. A curated edition already knows
which documents it wants, so this path does the one thing the live path cannot
do at that size: it stores every document as a page and every value as a cell
that names its page, quote, offsets and content hash.

It deliberately reuses the same chokepoints as a run:

* `repo.upsert_page` so a `page_id` addresses exactly one stored text;
* `validator.settle` so `verified` still means a support verdict **and** a
  locator that resolves;
* `repo.finalize_dataset` so the run and its records are written together.

A corpus is data, not a bypass. Every field is checked before import: its quote
must resolve in the document text and its value must be supported by that quote.
An unsupported value is stored `unverified` with the reason, never promoted.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.core.errors import validation
from app.schemas.evidence import locate_quote, normalize_for_match
from app.services import confidence as confidence_svc
from app.services.validator import settle

CORPUS_VERSION = 1
#: The reducer version on a page whose text came from an edition's normalizer,
#: not from `reducer.py`. Recorded so a later reader does not assume the live
#: HTML reducer produced offsets that it did not.
CORPUS_REDUCER = "corpus-v1"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def _document_date(document: dict) -> str:
    """The date a document's own metadata states, or "".

    Curated corpora carry a year (or a fuller date) beside each document. It is
    the source's statement about when its content was true, so it becomes the
    cell's `valid_time`: without it every corpus cell is undated, and a temporal
    question over the corpus has nothing to compare against. A bare year is kept
    as a year, because `valid_interval` needs the coarser precision.
    """
    for key in ("published_date", "date", "year"):
        raw = document.get(key)
        if isinstance(raw, (str, int)) and str(raw).strip():
            return str(raw).strip()
    field = (document.get("fields") or {}).get("year")
    if isinstance(field, dict):
        value = field.get("value")
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def load_corpus(path: str | Path) -> dict:
    """Read and shape-check a corpus file. Raises E_VALIDATION, never KeyError."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise validation(f"corpus is not JSON: {exc.msg} at line {exc.lineno}")
    if not isinstance(payload, dict):
        raise validation("corpus root must be an object")
    if int(payload.get("corpus_version") or 0) != CORPUS_VERSION:
        raise validation(f"corpus_version must be {CORPUS_VERSION}, "
                         f"got {payload.get('corpus_version')!r}")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise validation("corpus.documents must be a non-empty list")
    schema = payload.get("schema")
    if not isinstance(schema, list) or not schema:
        raise validation("corpus.schema must be a non-empty list of field specs")
    names = [str(f.get("name") or "") for f in schema if isinstance(f, dict)]
    if len(names) != len(schema) or any(not n for n in names):
        raise validation("every schema entry needs a name")
    if len(names) != len(set(names)):
        raise validation("corpus.schema has duplicate field names")
    return payload


def _supported(value: Any, quote: str, support_terms: Any = ()) -> bool:
    """Is `value` stated by `quote`?

    This is the corpus-side support verdict. It is intentionally literal: a
    value has to appear in the quote it cites, folded for case, whitespace and
    markdown the same way the live gate folds. A list must have every item
    present. It does not prove the value means what the schema says -- only a
    reader or a judge can -- but it refuses a value attributed to a sentence
    that does not contain it.

    A boolean is a classification, not a word the source prints, so the words
    that support `true` are declared by the edition, per field, in the corpus
    file. The core does not know what any of them mean: it checks that one of
    them is in the quote. A boolean field that declares no terms can only be
    supported by the literal word, and otherwise fails closed.
    """
    if value is None:
        return False
    quote_norm = normalize_for_match(quote)
    if not quote_norm:
        return False
    terms = [str(t) for t in (support_terms or []) if str(t).strip()]
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, bool):
            if item and any(normalize_for_match(t) in quote_norm for t in terms):
                continue
            needle = "true" if item else "false"
        else:
            needle = str(item)
        if not needle.strip():
            return False
        if normalize_for_match(needle) not in quote_norm:
            return False
    return True


def _validate_document(document: dict, field_names: set[str]) -> None:
    if not isinstance(document, dict):
        raise validation("every corpus document must be an object")
    doc_id = str(document.get("doc_id") or "").strip()
    if not doc_id:
        raise validation("every corpus document needs a doc_id")
    text = document.get("text")
    if not isinstance(text, str) or len(text.strip()) < 40:
        raise validation(f"{doc_id}: text is empty or too short to cite")
    fields = document.get("fields")
    if not isinstance(fields, dict):
        raise validation(f"{doc_id}: fields must be an object")
    for name, field in fields.items():
        if name not in field_names:
            raise validation(f"{doc_id}: field {name!r} is not in corpus.schema")
        if not isinstance(field, dict):
            raise validation(f"{doc_id}: field {name!r} must be an object")
        quote = str(field.get("quote") or "")
        if not quote:
            raise validation(f"{doc_id}: field {name!r} has no quote")
        start, end = locate_quote(quote, text)
        if start is None or end is None:
            raise validation(f"{doc_id}: field {name!r} quote does not resolve "
                             "against the document text")


def _plan(payload: dict) -> dict:
    documents = payload.get("documents") or []
    source_urls = list(dict.fromkeys(
        str(d.get("source_url") or d.get("fetch_url") or "")
        for d in documents if (d.get("source_url") or d.get("fetch_url"))))
    return {
        "goal": str(payload.get("name") or "Imported legal corpus")[:200],
        "entity": str(payload.get("entity") or "document"),
        "requested_count": len(documents),
        "max_results": len(documents),
        "fields": list(payload.get("schema") or []),
        "search_queries": [str(payload.get("name") or "curated corpus")],
        "seed_domains": list(payload.get("seed_domains") or []),
        "seed_urls": source_urls,
        "source_types": ["official"],
        "traversal": {"max_pages_per_domain": len(documents)},
        "validation_rules": list(payload.get("validation_rules") or []),
        "dedupe_keys": list(payload.get("dedupe_keys") or []),
        "allowed_sources": list(payload.get("allowed_sources") or []),
        "max_pages": len(documents),
        "edition": str(payload.get("edition") or ""),
        "edition_version": str(payload.get("edition_version") or ""),
        "buckets": list(payload.get("buckets") or []),
        "tiering": dict(payload.get("tiering") or {}),
    }


def _workflow_uuid(label: str) -> str:
    """A stable UUID for a workflow label.

    `workflows.id` is a uuid column, so a label like "legal_corpus" is not a
    legal value for it. `LocalRepo` stores a string and accepted one, which
    is why the import worked in local mode and failed against Postgres with
    "invalid input syntax for type uuid" -- the same bug, two stores, one
    visible only where it mattered.

    The id is derived from the label rather than randomised, so importing the
    same corpus twice updates one workflow row instead of leaving a new one
    behind each time. The plan is stored under it, and the run points at it,
    exactly as a compiled run does.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"datagoblin-corpus:{label}"))


def import_corpus(repo: object, payload: dict, *,
                  workflow_id: str = "corpus_import") -> dict:
    """Store `payload` as one run, its pages, and one dataset. Returns ids.

    The repository surface is the one both adapters share: `create_run`,
    `save_plan`, `upsert_page`, `append_event`, `finalize_dataset`.

    `workflow_id` is a *label*, not an id: it names the workflow in the run
    history, and the id it maps to is derived from it.
    """
    if int(payload.get("corpus_version") or 0) != CORPUS_VERSION:
        raise validation(f"corpus_version must be {CORPUS_VERSION}")
    documents = payload.get("documents") or []
    schema = payload.get("schema") or []
    field_names = {str(f["name"]) for f in schema}
    seen: set[str] = set()
    for document in documents:
        _validate_document(document, field_names)
        doc_id = str(document["doc_id"])
        if doc_id in seen:
            raise validation(f"duplicate doc_id in corpus: {doc_id}")
        seen.add(doc_id)

    plan = _plan(payload)
    run_id = str(uuid.uuid4())
    plan_id = _workflow_uuid(workflow_id)
    repo.create_run(run_id, plan_id, plan)
    repo.save_plan(plan_id, plan)
    repo.append_event(run_id, {
        "type": "stage.started", "stage": "FETCHING",
        "message": f"importing {len(documents)} curated documents",
        "progress": 0, "timestamp": _now(), "data": {"documents": len(documents)},
    })

    rows: list[dict] = []
    verified_cells = 0
    unverified_cells = 0
    for index, document in enumerate(documents, 1):
        text = str(document["text"])
        url = str(document.get("source_url") or document.get("fetch_url") or
                  f"corpus://{document['doc_id']}")
        retrieved_at = str(document.get("retrieved_at") or _now())
        doc_date = _document_date(document)
        chash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        page_id = repo.upsert_page(run_id, {
            "url": url,
            "final_url": str(document.get("fetch_url") or url),
            "method": "corpus",
            "status": "ok",
            "content_hash": chash,
            "markdown": text,
            "raw_html": "",
            "snapshot_chars": len(text),
            "reducer_version": CORPUS_REDUCER,
            "final_content_source": "corpus",
            "retrieved_at": retrieved_at,
            "published_date": doc_date,
        })

        cells: dict[str, dict] = {}
        for name, field in (document.get("fields") or {}).items():
            quote = str(field.get("quote") or "")
            value = field.get("value")
            supported = _supported(value, quote, field.get("support_terms"))
            status = "verified" if supported else "unverified"
            reason = "" if supported else (
                "the quote does not contain the value it is cited for")
            cell = settle({
                "value": value,
                "verification_status": status,
                # When the document says its own content was published or
                # enacted. Advisory, like every `valid_time`: it never moves a
                # cell out of `verified`.
                "valid_time": doc_date,
                "source": {
                    "url": url,
                    "title": str(document.get("title") or ""),
                    "quote": quote,
                    "retrieved_at": retrieved_at,
                    "page_id": page_id,
                    "content_hash": chash,
                },
            }, status=status, quote=quote, source_text=text, reason=reason)
            method = str(field.get("read_method") or "api")
            cell.update(confidence_svc.receipt(
                method, verified=(cell["verification_status"] == "verified")))
            cells[name] = cell
            if cell["verification_status"] == "verified":
                verified_cells += 1
            else:
                unverified_cells += 1
        rows.append({"fields": cells})
        repo.append_event(run_id, {
            "type": "stage.progress", "stage": "EXTRACTING",
            "message": f"stored {document['doc_id']}",
            "progress": int(index * 100 / max(1, len(documents))),
            "timestamp": _now(),
            "data": {"doc_id": document["doc_id"], "page_id": page_id},
        })

    fully_verified = sum(
        1 for row in rows
        if row["fields"] and all(
            c.get("verification_status") == "verified"
            for c in row["fields"].values()))
    counts = {
        "attempted": len(documents),
        "successful": len(documents),
        "failed": 0,
        "records": len(rows),
        "verified": fully_verified,
        "needs_review": len(rows) - fully_verified,
        "fields_verified": verified_cells,
        "fields_unverified": unverified_cells,
        "fields_judgment_unavailable": 0,
        "fields_conflicting": 0,
        "fields_rate_limited": 0,
        "records_fully_verified": fully_verified,
        "records_needing_review": len(rows) - fully_verified,
    }
    dataset_id = repo.finalize_dataset(run_id, plan, rows, counts)
    repo.append_event(run_id, {
        "type": "run.completed", "stage": "COMPLETED",
        "message": f"imported {len(rows)} records",
        "progress": 100, "timestamp": _now(),
        "data": {"dataset_id": dataset_id, "records": len(rows),
                 "fields_verified": verified_cells},
    })
    return {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "records": len(rows),
        "fields_verified": verified_cells,
        "fields_unverified": unverified_cells,
    }
