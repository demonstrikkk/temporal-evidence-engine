"""Persistence for the verifier — one interface, two honest adapters.

`local` is a JSON state file. It exists so the whole product can run with no
infrastructure (and so the test suite never needs a database), and it is
explicitly labelled as such by `/api/health`. `postgres` is the real store, in
schema `tev` so it cannot collide with the DataGoblin tables in the same
database.

The shapes are plain dicts, because they cross three boundaries (SQL rows, JSON
state, API responses) and a dataclass per row would be a fourth representation
to keep in sync. The typed models live in `app/verify/schemas.py` at the edge.

Invariants this layer owns:

* source revisions are append-only per `(source_id, content_hash)`;
* run events are append-only with a per-run sequence;
* claim status history is append-only;
* nothing here decides a verdict — it stores what the orchestrator decided.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from datetime import date, datetime, time as dtime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from app.core.config import REPO_ROOT, settings
from app.core.logging import log

#: Collections the local store persists. Order matters only for readability.
COLLECTIONS = (
    "answers", "runs", "claims", "sources", "revisions", "evidence", "links",
    "assertions", "history", "events", "calls", "queries", "cache",
    "cache_entries", "conversations", "messages", "quota_ledger",
    "assertion_spans", "coverage_audits",
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class VerifyStore(Protocol):
    adapter: str

    # answers / runs
    def create_answer(self, row: dict) -> dict: ...
    def get_answer(self, answer_id: str) -> dict | None: ...
    def create_run(self, row: dict) -> dict: ...
    def update_run(self, run_id: str, **fields: Any) -> dict | None: ...
    def get_run(self, run_id: str) -> dict | None: ...
    def list_runs(self, limit: int = 25) -> list[dict]: ...

    # claims
    def add_claim(self, row: dict) -> dict: ...
    def update_claim(self, claim_id: str, **fields: Any) -> dict | None: ...
    def get_claim(self, claim_id: str) -> dict | None: ...
    def list_claims(self, run_id: str) -> list[dict]: ...
    def list_due(self, limit: int = 50) -> list[dict]: ...

    # sources
    def add_source(self, row: dict) -> dict: ...
    def get_source(self, source_id: str) -> dict | None: ...
    def list_sources(self, limit: int = 50) -> list[dict]: ...
    def add_revision(self, row: dict) -> dict: ...
    def get_revision(self, revision_id: str) -> dict | None: ...
    def find_revision(self, source_id: str, content_hash: str) -> dict | None: ...
    def list_revisions_for_source(self, source_id: str, limit: int = 5) -> list[dict]: ...

    # evidence
    def add_evidence(self, row: dict) -> dict: ...
    def get_evidence(self, evidence_id: str) -> dict | None: ...
    def add_link(self, row: dict) -> dict: ...
    def list_evidence(self, claim_id: str) -> list[dict]: ...

    # assertions / history
    def add_assertion(self, row: dict) -> dict: ...
    def list_assertions(self, claim_id: str) -> list[dict]: ...
    def add_history(self, row: dict) -> dict: ...
    def list_history(self, claim_id: str) -> list[dict]: ...

    # coverage: the durable answer to "what was not checked"
    def add_assertion_span(self, row: dict) -> dict: ...
    def list_assertion_spans(self, run_id: str) -> list[dict]: ...
    def replace_assertion_spans(self, run_id: str, rows: list[dict]) -> list[dict]: ...
    def put_coverage_audit(self, row: dict) -> dict: ...
    def get_coverage_audit(self, run_id: str) -> dict | None: ...

    # events
    def append_event(self, run_id: str, event_type: str, payload: dict) -> dict: ...
    def list_events(self, run_id: str, after: int = 0) -> list[dict]: ...

    # model calls / research cache
    def add_model_call(self, row: dict) -> dict: ...
    def find_model_call(self, prompt_hash: str, model: str, stage: str) -> dict | None: ...
    def cache_get(self, key: str) -> dict | None: ...
    def cache_put(self, key: str, tool: str, payload: dict) -> None: ...
    def gateway_stats(self, run_id: str | None = None) -> dict: ...
    def list_model_calls(self, limit: int = 25,
                         run_id: str | None = None) -> list[dict]: ...

    # multi-layer cache entries
    def get_cache_entry(self, cache_key: str) -> dict | None: ...
    def put_cache_entry(self, row: dict) -> dict: ...
    def touch_cache_entry(self, cache_key: str) -> None: ...
    def purge_cache(self, cache_type: str | None = None) -> int: ...
    def find_revision_by_hash(self, canonical_url: str, content_hash: str) -> dict | None: ...

    # quota ledger
    def record_quota_usage(self, row: dict) -> dict: ...
    def get_quota_usage(self, provider: str, resource_type: str, window_type: str) -> dict | None: ...

    # conversations & messages
    def create_conversation(self, row: dict) -> dict: ...
    def get_conversation(self, conv_id: str) -> dict | None: ...
    def list_conversations(self, limit: int = 50) -> list[dict]: ...
    def update_conversation(self, conv_id: str, **fields: Any) -> dict | None: ...
    def create_message(self, row: dict) -> dict: ...
    def list_messages(self, conv_id: str) -> list[dict]: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# local (JSON state file)
# ---------------------------------------------------------------------------

class LocalStore:
    """Explicitly a development store. Fast, dependency-free, single-process."""

    adapter = "local"

    def __init__(self, directory: str | None = None) -> None:
        target = Path(directory or settings.TEV_LOCAL_DIR)
        # Anchor a relative setting at the repo root, not the process cwd: the
        # API was run once from `backend/` and once from the root, producing two
        # stores whose runs each looked missing from the other.
        self._dir = target if target.is_absolute() else REPO_ROOT / target
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "state.json"
        self._lock = threading.RLock()
        self._save_counter = itertools.count(1)
        self._data: dict[str, list[dict]] = {c: [] for c in COLLECTIONS}
        if self._path.is_file():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                for name in COLLECTIONS:
                    if isinstance(loaded.get(name), list):
                        self._data[name] = loaded[name]
            except (json.JSONDecodeError, OSError):
                # A corrupt dev store must not take the API down; it is not
                # production truth and the file is still on disk for inspection.
                self._data = {c: [] for c in COLLECTIONS}

    # -- plumbing ---------------------------------------------------------
    def _save(self) -> None:
        """Persist the in-memory state, tolerating a Windows file lock.

        `os.replace` onto `state.json` fails with `WinError 5` whenever another
        handle has the destination open — a virus scanner, an editor, a backup
        agent, or an earlier reader. That is not a corruption and it is not the
        caller's fault, but a single failed replace used to bubble out of
        `update_claim` and turn into `extraction_failed` on a claim whose
        extraction had worked perfectly. A transient lock was being reported as
        a model failure.

        The retry loop is bounded and each attempt writes its own temp file, so
        two writers cannot collide on one `.tmp` name either. If every attempt
        fails the in-memory state is still coherent and the next successful save
        persists it; the failure is logged at error level rather than swallowed,
        because a dev store that cannot write is something an operator needs to
        know about.
        """
        payload = json.dumps(self._data, default=str, ensure_ascii=False)
        last: OSError | None = None
        for attempt in range(6):
            tmp = self._dir / (f"state.{os.getpid()}."
                               f"{next(self._save_counter)}.tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
                return
            except OSError as exc:  # noqa: PERF203 — retries are the point
                last = exc
                try:
                    tmp.unlink()
                except OSError:
                    pass
                time.sleep(0.02 * (2 ** attempt))
        log.error("local store could not persist state",
                  extra={"data": {"path": str(self._path),
                                  "error": str(last)[:300]}})

    def _insert(self, collection: str, row: dict) -> dict:
        with self._lock:
            self._data[collection].append(row)
            self._save()
        return row

    def _find(self, collection: str, **match: Any) -> dict | None:
        for row in reversed(self._data[collection]):
            if all(row.get(k) == v for k, v in match.items()):
                return row
        return None

    def _update(self, collection: str, key: str, value: str, fields: dict) -> dict | None:
        with self._lock:
            for row in self._data[collection]:
                if row.get(key) == value:
                    row.update(fields)
                    self._save()
                    return row
        return None

    # -- answers / runs ---------------------------------------------------
    def create_answer(self, row: dict) -> dict:
        return self._insert("answers", row)

    def get_answer(self, answer_id: str) -> dict | None:
        return self._find("answers", id=answer_id)

    def create_run(self, row: dict) -> dict:
        return self._insert("runs", row)

    def update_run(self, run_id: str, **fields: Any) -> dict | None:
        return self._update("runs", "id", run_id, fields)

    def get_run(self, run_id: str) -> dict | None:
        return self._find("runs", id=run_id)

    def list_runs(self, limit: int = 25) -> list[dict]:
        rows = sorted(self._data["runs"],
                      key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return rows[:limit]

    # -- claims -----------------------------------------------------------
    def add_claim(self, row: dict) -> dict:
        return self._insert("claims", row)

    def update_claim(self, claim_id: str, **fields: Any) -> dict | None:
        return self._update("claims", "id", claim_id, fields)

    def get_claim(self, claim_id: str) -> dict | None:
        return self._find("claims", id=claim_id)

    def list_claims(self, run_id: str) -> list[dict]:
        rows = [r for r in self._data["claims"] if r.get("run_id") == run_id]
        return sorted(rows, key=lambda r: r.get("ordinal", 0))

    def list_due(self, limit: int = 50) -> list[dict]:
        rows = [r for r in self._data["claims"] if r.get("next_check_at")]
        rows.sort(key=lambda r: str(r.get("next_check_at")))
        return rows[:limit]

    # -- sources ----------------------------------------------------------
    def add_source(self, row: dict) -> dict:
        with self._lock:
            existing = self._find("sources", canonical_url=row["canonical_url"])
            if existing:
                return existing
        return self._insert("sources", row)

    def get_source(self, source_id: str) -> dict | None:
        return self._find("sources", id=source_id)

    def list_sources(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = list(self._data.get("sources") or [])
        rows.sort(key=lambda r: str(r.get("first_seen_at") or ""), reverse=True)
        return rows[:limit]

    def add_revision(self, row: dict) -> dict:
        with self._lock:
            existing = self._find("revisions", source_id=row["source_id"],
                                  content_hash=row["content_hash"])
            if existing:
                return existing
        return self._insert("revisions", row)

    def get_revision(self, revision_id: str) -> dict | None:
        return self._find("revisions", id=revision_id)

    def find_revision(self, source_id: str, content_hash: str) -> dict | None:
        return self._find("revisions", source_id=source_id, content_hash=content_hash)

    def list_revisions_for_source(self, source_id: str, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = [r for r in self._data.get("revisions") or []
                    if r.get("source_id") == source_id]
        rows.sort(key=lambda r: str(r.get("retrieved_at") or ""), reverse=True)
        return rows[:limit]

    # -- evidence ---------------------------------------------------------
    def add_evidence(self, row: dict) -> dict:
        return self._insert("evidence", row)

    def get_evidence(self, evidence_id: str) -> dict | None:
        return self._find("evidence", id=evidence_id)

    def add_link(self, row: dict) -> dict:
        with self._lock:
            existing = self._find("links", claim_id=row["claim_id"],
                                  evidence_id=row["evidence_id"])
            if existing:
                return existing
        return self._insert("links", row)

    def list_evidence(self, claim_id: str) -> list[dict]:
        out = []
        for evidence in self._data["evidence"]:
            if evidence.get("claim_id") != claim_id:
                continue
            link = self._find("links", evidence_id=evidence["id"])
            revision = self.get_revision(evidence.get("source_revision_id", ""))
            source = self.get_source(revision.get("source_id", "")) if revision else None
            out.append({**evidence,
                        "link": link or {},
                        "revision": revision or {},
                        "source": source or {}})
        return out

    # -- assertions / history ---------------------------------------------
    def add_assertion(self, row: dict) -> dict:
        return self._insert("assertions", row)

    def list_assertions(self, claim_id: str) -> list[dict]:
        return [r for r in self._data["assertions"] if r.get("claim_id") == claim_id]

    def add_history(self, row: dict) -> dict:
        return self._insert("history", row)

    def list_history(self, claim_id: str) -> list[dict]:
        return [r for r in self._data["history"] if r.get("claim_id") == claim_id]

    # -- coverage ---------------------------------------------------------
    def add_assertion_span(self, row: dict) -> dict:
        return self._insert("assertion_spans", row)

    def list_assertion_spans(self, run_id: str) -> list[dict]:
        return [r for r in self._data["assertion_spans"]
                if r.get("run_id") == run_id]

    def replace_assertion_spans(self, run_id: str, rows: list[dict]) -> list[dict]:
        """Rebuild one run's spans. Used when a repair pass changes the audit."""
        with self._lock:
            self._data["assertion_spans"] = [
                r for r in self._data["assertion_spans"]
                if r.get("run_id") != run_id]
            self._data["assertion_spans"].extend(rows)
            self._save()
        return rows

    def put_coverage_audit(self, row: dict) -> dict:
        """One row per run: the audit is recomputed after repair, not appended."""
        with self._lock:
            for existing in self._data["coverage_audits"]:
                if existing.get("run_id") == row.get("run_id"):
                    existing.update(row)
                    self._save()
                    return existing
            self._data["coverage_audits"].append(row)
            self._save()
        return row

    def get_coverage_audit(self, run_id: str) -> dict | None:
        return self._find("coverage_audits", run_id=run_id)

    # -- events -----------------------------------------------------------
    def append_event(self, run_id: str, event_type: str, payload: dict) -> dict:
        with self._lock:
            seq = 1 + max((int(r.get("sequence", 0)) for r in self._data["events"]
                           if r.get("run_id") == run_id), default=0)
            row = {"id": new_id(), "run_id": run_id, "sequence": seq,
                   "event_type": event_type, "payload": payload,
                   "created_at": iso(now())}
            return self._insert("events", row)

    def list_events(self, run_id: str, after: int = 0) -> list[dict]:
        rows = [r for r in self._data["events"]
                if r.get("run_id") == run_id and int(r.get("sequence", 0)) > after]
        return sorted(rows, key=lambda r: int(r["sequence"]))

    # -- calls / cache ----------------------------------------------------
    def add_model_call(self, row: dict) -> dict:
        with self._lock:
            # A successful call is a cache entry; a failed one is an audit row.
            # Only the first successful call for a prompt may occupy the cache
            # key, so a later replay is stable and a failure never poisons it.
            if row.get("cacheable", True):
                existing = self.find_model_call(row["prompt_hash"], row["model"],
                                                row["stage"])
                if existing:
                    return existing
        return self._insert("calls", row)

    def find_model_call(self, prompt_hash: str, model: str, stage: str) -> dict | None:
        for row in reversed(self._data["calls"]):
            if (row.get("prompt_hash") == prompt_hash and row.get("model") == model
                    and row.get("stage") == stage
                    and row.get("status") == "ok" and row.get("cacheable", True)):
                return row
        return None

    def cache_get(self, key: str) -> dict | None:
        return self._find("cache", cache_key=key)

    def cache_put(self, key: str, tool: str, payload: dict) -> None:
        with self._lock:
            if self._find("cache", cache_key=key):
                return
            self._insert("cache", {"cache_key": key, "tool": tool,
                                   "payload": payload, "fetched_at": iso(now())})

    def gateway_stats(self, run_id: str | None = None) -> dict:
        rows = [r for r in self._data["calls"]
                if run_id is None or r.get("run_id") == run_id]
        return {
            "calls": len(rows),
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
            # How much of `cost_usd` is an estimate rather than a bill. Without
            # this the panel cannot tell a free run from an unpriced one.
            "estimated_calls": sum(1 for r in rows
                                   if r.get("cost_source") == "catalogue"),
            "fallback_attempts": sum(int(r.get("fallback_attempts") or 0) for r in rows),
            "providers": sorted({str(r.get("provider") or "") for r in rows} - {""}),
            "last": rows[-1] if rows else None,
        }

    def list_model_calls(self, limit: int = 25,
                         run_id: str | None = None) -> list[dict]:
        rows = [r for r in self._data["calls"]
                if run_id is None or r.get("run_id") == run_id]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        keys = ("stage", "provider", "model", "resolved_model",
                "bifrost_request_id", "input_tokens", "output_tokens",
                "cost_usd", "cost_source", "latency_ms", "fallback_attempts",
                "status", "created_at")
        return [{k: row.get(k) for k in keys} for row in rows[:limit]]

    # -- multi-layer cache entries ----------------------------------------
    def get_cache_entry(self, cache_key: str) -> dict | None:
        with self._lock:
            return self._find("cache_entries", cache_key=cache_key)

    def put_cache_entry(self, row: dict) -> dict:
        with self._lock:
            existing = self._find("cache_entries", cache_key=row["cache_key"])
            if existing:
                for k, v in row.items():
                    existing[k] = v
                existing["last_accessed_at"] = iso(now())
                self._save()
                return existing
            clean = {
                "hit_count": 0,
                "created_at": iso(now()),
                "last_accessed_at": iso(now()),
                "metadata": {},
                **row,
            }
            return self._insert("cache_entries", clean)

    def touch_cache_entry(self, cache_key: str) -> None:
        with self._lock:
            entry = self._find("cache_entries", cache_key=cache_key)
            if entry:
                entry["hit_count"] = int(entry.get("hit_count") or 0) + 1
                entry["last_accessed_at"] = iso(now())
                self._save()

    def purge_cache(self, cache_type: str | None = None) -> int:
        with self._lock:
            before = len(self._data.get("cache_entries") or [])
            if cache_type is None:
                self._data["cache_entries"] = []
            else:
                self._data["cache_entries"] = [
                    e for e in self._data.get("cache_entries") or []
                    if e.get("cache_type") != cache_type
                ]
            removed = before - len(self._data.get("cache_entries") or [])
            if removed > 0:
                self._save()
            return removed

    def purge_cache_by_prefix(self, prefix: str, metadata_contains: str = "") -> int:
        """Purge cache entries whose cache_key starts with `prefix`. Optionally
        also require `metadata_contains` to appear in the entry's metadata."""
        with self._lock:
            before = len(self._data.get("cache_entries") or [])
            self._data["cache_entries"] = [
                e for e in self._data.get("cache_entries") or []
                if not (
                    e.get("cache_key", "").startswith(prefix)
                    and (not metadata_contains
                         or metadata_contains in str(e.get("metadata") or {})))
            ]
            removed = before - len(self._data.get("cache_entries") or [])
            if removed > 0:
                self._save()
            return removed

    def purge_cache_by_version(self, cache_type: str,
                               previous_version: str,
                               new_version: str) -> int:
        """Purge cache entries of `cache_type` whose cache_version equals
        `previous_version` (the config that just changed). `new_version` is
        recorded for audit but entries are keyed by version."""
        with self._lock:
            before = len(self._data.get("cache_entries") or [])
            self._data["cache_entries"] = [
                e for e in self._data.get("cache_entries") or []
                if not (e.get("cache_type") == cache_type
                        and e.get("cache_version") == previous_version)
            ]
            removed = before - len(self._data.get("cache_entries") or [])
            if removed > 0:
                self._save()
            return removed

    def find_revision_by_hash(self, canonical_url: str, content_hash: str) -> dict | None:
        with self._lock:
            src = self._find("sources", canonical_url=canonical_url)
            if not src:
                return None
            return self.find_revision(src["id"], content_hash)

    # -- quota ledger -----------------------------------------------------
    def record_quota_usage(self, row: dict) -> dict:
        with self._lock:
            for r in self._data.get("quota_ledger") or []:
                if (r.get("provider") == row.get("provider")
                        and r.get("resource_type") == row.get("resource_type")
                        and r.get("window_type") == row.get("window_type")
                        and r.get("window_start") == row.get("window_start")):
                    r["used_amount"] = int(r.get("used_amount") or 0) + int(row.get("used_amount") or 0)
                    if "allocated_limit" in row:
                        r["allocated_limit"] = int(row["allocated_limit"])
                    r["last_updated_at"] = iso(now())
                    self._save()
                    return r
            clean = {
                "id": row.get("id") or new_id(),
                "used_amount": 0,
                "last_updated_at": iso(now()),
                **row,
            }
            return self._insert("quota_ledger", clean)

    def get_quota_usage(self, provider: str, resource_type: str, window_type: str) -> dict | None:
        with self._lock:
            matches = [
                r for r in self._data.get("quota_ledger") or []
                if r.get("provider") == provider
                and r.get("resource_type") == resource_type
                and r.get("window_type") == window_type
            ]
            matches.sort(key=lambda x: str(x.get("window_start") or ""), reverse=True)
            return matches[0] if matches else None

    # -- conversations & messages -----------------------------------------
    def create_conversation(self, row: dict) -> dict:
        clean = {
            "id": row.get("id") or new_id(),
            "title": row.get("title") or "New Research Conversation",
            "budget_mode": row.get("budget_mode") or "balanced",
            "structured_memory": row.get("structured_memory") or {},
            "created_at": iso(now()),
            "updated_at": iso(now()),
        }
        return self._insert("conversations", clean)

    def get_conversation(self, conv_id: str) -> dict | None:
        return self._find("conversations", id=conv_id)

    def list_conversations(self, limit: int = 50) -> list[dict]:
        rows = list(self._data.get("conversations") or [])
        rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        return rows[:limit]

    def update_conversation(self, conv_id: str, **fields: Any) -> dict | None:
        with self._lock:
            conv = self._find("conversations", id=conv_id)
            if not conv:
                return None
            for k, v in fields.items():
                conv[k] = v
            conv["updated_at"] = iso(now())
            self._save()
            return conv

    def create_message(self, row: dict) -> dict:
        clean = {
            "id": row.get("id") or new_id(),
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "content": row["content"],
            "run_id": row.get("run_id"),
            "citations": row.get("citations") or [],
            "quality_metrics": row.get("quality_metrics") or {},
            "created_at": iso(now()),
        }
        return self._insert("messages", clean)

    def list_messages(self, conv_id: str) -> list[dict]:
        rows = [m for m in self._data.get("messages") or []
                if m.get("conversation_id") == conv_id]
        rows.sort(key=lambda m: str(m.get("created_at") or ""))
        return rows

    def close(self) -> None:  # nothing to release
        return None


# ---------------------------------------------------------------------------
# postgres
# ---------------------------------------------------------------------------

def _jsonb(value: Any) -> Any:
    """A jsonb parameter that can hold what the product actually produces.

    psycopg's default JSON dumper refuses a `datetime`, `date`, `Decimal` or
    `UUID`. The payloads here are event payloads and receipts, which contain
    exactly those types, so a model receipt carrying `created_at` was dropped
    with `could not append run event` on Postgres while the local store kept it.
    A store that silently loses an event is worse than one that fails loudly,
    but the right answer is neither: encode the type that is already a legal part
    of the domain.
    """
    from psycopg.types.json import Jsonb

    if value is None:
        return None

    def encode(obj: Any) -> Any:
        if isinstance(obj, (datetime, date, dtime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (uuid.UUID, Path)):
            return str(obj)
        if isinstance(obj, (set, frozenset)):
            return sorted(obj, key=str)
        raise TypeError(f"{type(obj).__name__} is not JSON serialisable")

    return Jsonb(value, dumps=lambda obj: json.dumps(obj, default=encode,
                                                     ensure_ascii=False))


class PgStore:
    """The real store. Short-lived connections, schema `tev`."""

    adapter = "postgres"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._dsn, row_factory=dict_row,
                               options="-c search_path=tev,public")

    def _one(self, sql: str, params: Any = ()) -> dict | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: Any = ()) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _write(self, sql: str, params: Any = ()) -> dict | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if cur.description else None
            conn.commit()
            return row

    # -- answers / runs ---------------------------------------------------
    def create_answer(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO answers (id, prompt, answer_text, generated_at,
                   generated_at_source, submitted_at, locale, source_urls,
                   domain_filter, skip_non_factual)
               VALUES (%(id)s, %(prompt)s, %(answer_text)s, %(generated_at)s,
                   %(generated_at_source)s, %(submitted_at)s, %(locale)s,
                   %(source_urls)s, %(domain_filter)s, %(skip_non_factual)s)
               RETURNING *""",
            {**row, "source_urls": _jsonb(row.get("source_urls") or []),
             "domain_filter": _jsonb(row.get("domain_filter") or [])})

    def get_answer(self, answer_id: str) -> dict | None:
        return self._one("SELECT * FROM answers WHERE id = %s", (answer_id,))

    def create_run(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO verification_runs (id, answer_id, status, stage,
                   rule_version, cache_mode, budget_mode, started_at)
               VALUES (%(id)s, %(answer_id)s, %(status)s, %(stage)s,
                   %(rule_version)s, %(cache_mode)s, %(budget_mode)s,
                   %(started_at)s)
               RETURNING *""", row)

    def update_run(self, run_id: str, **fields: Any) -> dict | None:
        if not fields:
            return self.get_run(run_id)
        allowed = {"status", "stage", "completed_at", "error"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return self.get_run(run_id)
        sets = ", ".join(f"{k} = %({k})s" for k in clean)
        return self._write(
            f"UPDATE verification_runs SET {sets} WHERE id = %(id)s RETURNING *",
            {**clean, "id": run_id, "error": _jsonb(clean.get("error"))})

    def get_run(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM verification_runs WHERE id = %s", (run_id,))

    def list_runs(self, limit: int = 25) -> list[dict]:
        return self._all(
            "SELECT * FROM verification_runs ORDER BY started_at DESC LIMIT %s",
            (limit,))

    # -- claims -----------------------------------------------------------
    def add_claim(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO claims (id, answer_id, run_id, ordinal, claim_text,
                   span_start, span_end, subject, predicate, object_raw,
                   object_normalized, qualifiers, claim_type, time_sensitive,
                   volatility_class, temporal_scope, extraction_confidence,
                   status, reason_code, next_check_at, last_verified_at)
               VALUES (%(id)s, %(answer_id)s, %(run_id)s, %(ordinal)s,
                   %(claim_text)s, %(span_start)s, %(span_end)s, %(subject)s,
                   %(predicate)s, %(object_raw)s, %(object_normalized)s,
                   %(qualifiers)s, %(claim_type)s, %(time_sensitive)s,
                   %(volatility_class)s, %(temporal_scope)s,
                   %(extraction_confidence)s, %(status)s, %(reason_code)s,
                   %(next_check_at)s, %(last_verified_at)s)
               RETURNING *""", self._claim_params(row))

    @staticmethod
    def _claim_params(row: dict) -> dict:
        return {"next_check_at": None, "last_verified_at": None, **row,
                "object_normalized": _jsonb(row.get("object_normalized") or {}),
                "qualifiers": _jsonb(row.get("qualifiers") or {}),
                "temporal_scope": _jsonb(row.get("temporal_scope") or {})}

    def update_claim(self, claim_id: str, **fields: Any) -> dict | None:
        allowed = {"status", "reason_code", "next_check_at", "last_verified_at",
                   "object_normalized", "qualifiers", "temporal_scope"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return self.get_claim(claim_id)
        params = {"id": claim_id}
        for key, value in clean.items():
            params[key] = _jsonb(value) if key in {"object_normalized", "qualifiers",
                                                   "temporal_scope"} else value
        sets = ", ".join(f"{k} = %({k})s" for k in clean)
        return self._write(f"UPDATE claims SET {sets} WHERE id = %(id)s RETURNING *",
                           params)

    def get_claim(self, claim_id: str) -> dict | None:
        return self._one("SELECT * FROM claims WHERE id = %s", (claim_id,))

    def list_claims(self, run_id: str) -> list[dict]:
        return self._all("SELECT * FROM claims WHERE run_id = %s ORDER BY ordinal",
                         (run_id,))

    def list_due(self, limit: int = 50) -> list[dict]:
        return self._all(
            """SELECT * FROM claims WHERE next_check_at IS NOT NULL
               ORDER BY next_check_at ASC LIMIT %s""", (limit,))

    # -- sources ----------------------------------------------------------
    def add_source(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO sources (id, canonical_url, domain, title, first_seen_at)
               VALUES (%(id)s, %(canonical_url)s, %(domain)s, %(title)s,
                       %(first_seen_at)s)
               ON CONFLICT (canonical_url) DO UPDATE SET
                   title = COALESCE(NULLIF(EXCLUDED.title, ''), sources.title)
               RETURNING *""", row)

    def get_source(self, source_id: str) -> dict | None:
        return self._one("SELECT * FROM sources WHERE id = %s", (source_id,))

    def list_sources(self, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT * FROM sources ORDER BY first_seen_at DESC LIMIT %s",
            (limit,))

    def add_revision(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO source_revisions (id, source_id, content_hash, content,
                   published_date, published_date_source, retrieved_at,
                   extraction_method, tavily_request_id, fetch_outcome)
               VALUES (%(id)s, %(source_id)s, %(content_hash)s, %(content)s,
                   %(published_date)s, %(published_date_source)s, %(retrieved_at)s,
                   %(extraction_method)s, %(tavily_request_id)s, %(fetch_outcome)s)
               ON CONFLICT (source_id, content_hash) DO NOTHING
               RETURNING *""",
            {**row, "fetch_outcome": _jsonb(row.get("fetch_outcome") or {})}) or \
            self.find_revision(row["source_id"], row["content_hash"])

    def get_revision(self, revision_id: str) -> dict | None:
        return self._one("SELECT * FROM source_revisions WHERE id = %s", (revision_id,))

    def find_revision(self, source_id: str, content_hash: str) -> dict | None:
        return self._one(
            "SELECT * FROM source_revisions WHERE source_id = %s AND content_hash = %s",
            (source_id, content_hash))

    def list_revisions_for_source(self, source_id: str, limit: int = 5) -> list[dict]:
        return self._all(
            "SELECT * FROM source_revisions WHERE source_id = %s "
            "ORDER BY retrieved_at DESC LIMIT %s",
            (source_id, limit))

    # -- evidence ---------------------------------------------------------
    def add_evidence(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO evidence_spans (id, claim_id, source_revision_id,
                   quote_text, start_char, end_char, event_date, event_date_quote,
                   event_date_status, retrieved_at)
               VALUES (%(id)s, %(claim_id)s, %(source_revision_id)s, %(quote_text)s,
                   %(start_char)s, %(end_char)s, %(event_date)s,
                   %(event_date_quote)s, %(event_date_status)s, %(retrieved_at)s)
               RETURNING *""", row)

    def get_evidence(self, evidence_id: str) -> dict | None:
        return self._one("SELECT * FROM evidence_spans WHERE id = %s", (evidence_id,))

    def add_link(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO claim_evidence (id, claim_id, evidence_id,
                   relationship_proposed, relationship_settled, settlement_status,
                   settlement_reason, settlement_start, settlement_end,
                   candidate_value_raw, candidate_value_normalized, model_call_id,
                   created_at)
               VALUES (%(id)s, %(claim_id)s, %(evidence_id)s,
                   %(relationship_proposed)s, %(relationship_settled)s,
                   %(settlement_status)s, %(settlement_reason)s,
                   %(settlement_start)s, %(settlement_end)s,
                   %(candidate_value_raw)s, %(candidate_value_normalized)s,
                   %(model_call_id)s, %(created_at)s)
               ON CONFLICT (claim_id, evidence_id) DO UPDATE SET
                   relationship_settled = EXCLUDED.relationship_settled,
                   settlement_status = EXCLUDED.settlement_status,
                   settlement_reason = EXCLUDED.settlement_reason,
                   settlement_start = EXCLUDED.settlement_start,
                   settlement_end = EXCLUDED.settlement_end
               RETURNING *""",
            {**row, "candidate_value_normalized":
                _jsonb(row.get("candidate_value_normalized"))})

    def list_evidence(self, claim_id: str) -> list[dict]:
        return self._all(
            """SELECT e.*, l.relationship_proposed, l.relationship_settled,
                      l.settlement_status, l.settlement_reason,
                      l.settlement_start, l.settlement_end,
                      l.candidate_value_raw, l.candidate_value_normalized,
                      r.published_date, r.published_date_source, r.retrieved_at
                      AS revision_retrieved_at, r.extraction_method,
                      r.content AS source_content,
                      s.canonical_url, s.title, s.domain
               FROM evidence_spans e
               JOIN claim_evidence l ON l.evidence_id = e.id
               JOIN source_revisions r ON r.id = e.source_revision_id
               JOIN sources s ON s.id = r.source_id
               WHERE e.claim_id = %s
               ORDER BY e.id""", (claim_id,))

    # -- assertions / history ---------------------------------------------
    def add_assertion(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO claim_assertions (id, claim_id, value_raw,
                   value_normalized, valid_from, valid_until, evidence_id,
                   is_original)
               VALUES (%(id)s, %(claim_id)s, %(value_raw)s, %(value_normalized)s,
                   %(valid_from)s, %(valid_until)s, %(evidence_id)s,
                   %(is_original)s)
               RETURNING *""",
            {**row, "value_normalized": _jsonb(row.get("value_normalized") or {})})

    def list_assertions(self, claim_id: str) -> list[dict]:
        return self._all("SELECT * FROM claim_assertions WHERE claim_id = %s",
                         (claim_id,))

    def add_history(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO claim_status_history (id, claim_id, run_id,
                   previous_status, new_status, reason_code, rule_version, decided_at)
               VALUES (%(id)s, %(claim_id)s, %(run_id)s, %(previous_status)s,
                   %(new_status)s, %(reason_code)s, %(rule_version)s, %(decided_at)s)
               RETURNING *""", row)

    def list_history(self, claim_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM claim_status_history WHERE claim_id = %s ORDER BY decided_at",
            (claim_id,))

    # -- coverage ---------------------------------------------------------
    def add_assertion_span(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO assertion_spans (id, run_id, answer_id, claim_id,
                   kind, text, start_char, end_char, sentence_start,
                   sentence_end, disposition, signals, created_at)
               VALUES (%(id)s, %(run_id)s, %(answer_id)s, %(claim_id)s,
                   %(kind)s, %(text)s, %(start_char)s, %(end_char)s,
                   %(sentence_start)s, %(sentence_end)s, %(disposition)s,
                   %(signals)s, %(created_at)s)
               RETURNING *""",
            {**row, "signals": _jsonb(row.get("signals") or {}),
             "created_at": row.get("created_at") or now()})

    def list_assertion_spans(self, run_id: str) -> list[dict]:
        return self._all(
            """SELECT * FROM assertion_spans WHERE run_id = %s
               ORDER BY start_char""", (run_id,))

    def replace_assertion_spans(self, run_id: str, rows: list[dict]) -> list[dict]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM assertion_spans WHERE run_id = %s", (run_id,))
            if rows:
                cur.executemany(
                    """INSERT INTO assertion_spans (id, run_id, answer_id,
                           claim_id, kind, text, start_char, end_char,
                           sentence_start, sentence_end, disposition, signals,
                           created_at)
                       VALUES (%(id)s, %(run_id)s, %(answer_id)s, %(claim_id)s,
                           %(kind)s, %(text)s, %(start_char)s, %(end_char)s,
                           %(sentence_start)s, %(sentence_end)s, %(disposition)s,
                           %(signals)s, %(created_at)s)""",
                    [{**row, "signals": _jsonb(row.get("signals") or {}),
                      "created_at": row.get("created_at") or now()}
                     for row in rows])
        return rows

    def put_coverage_audit(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO coverage_audits (run_id, answer_id, answer_chars,
                   sentences, candidates, claims, claims_without_candidate,
                   total_assertions, checked, coverage, material_assertions,
                   material_coverage, dispositions, repair_calls,
                   repaired_claims, repair_targets, created_at, updated_at)
               VALUES (%(run_id)s, %(answer_id)s, %(answer_chars)s,
                   %(sentences)s, %(candidates)s, %(claims)s,
                   %(claims_without_candidate)s, %(total_assertions)s,
                   %(checked)s, %(coverage)s, %(material_assertions)s,
                   %(material_coverage)s, %(dispositions)s, %(repair_calls)s,
                   %(repaired_claims)s, %(repair_targets)s, %(created_at)s,
                   %(updated_at)s)
               ON CONFLICT (run_id) DO UPDATE SET
                   answer_chars = EXCLUDED.answer_chars,
                   sentences = EXCLUDED.sentences,
                   candidates = EXCLUDED.candidates,
                   claims = EXCLUDED.claims,
                   claims_without_candidate = EXCLUDED.claims_without_candidate,
                   total_assertions = EXCLUDED.total_assertions,
                   checked = EXCLUDED.checked,
                   coverage = EXCLUDED.coverage,
                   material_assertions = EXCLUDED.material_assertions,
                   material_coverage = EXCLUDED.material_coverage,
                   dispositions = EXCLUDED.dispositions,
                   repair_calls = EXCLUDED.repair_calls,
                   repaired_claims = EXCLUDED.repaired_claims,
                   repair_targets = EXCLUDED.repair_targets,
                   updated_at = EXCLUDED.updated_at
               RETURNING *""",
            {**row,
             "dispositions": _jsonb(row.get("dispositions") or {}),
             "repair_targets": _jsonb(row.get("repair_targets") or []),
             "created_at": row.get("created_at") or now(),
             "updated_at": row.get("updated_at") or now()})

    def get_coverage_audit(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM coverage_audits WHERE run_id = %s",
                         (run_id,))

    # -- events -----------------------------------------------------------
    def append_event(self, run_id: str, event_type: str, payload: dict) -> dict:
        return self._write(
            """INSERT INTO verification_events (run_id, sequence, event_type,
                   payload, created_at)
               VALUES (%(run_id)s,
                   COALESCE((SELECT MAX(sequence) FROM verification_events
                             WHERE run_id = %(run_id)s), 0) + 1,
                   %(event_type)s, %(payload)s, %(created_at)s)
               RETURNING *""",
            {"run_id": run_id, "event_type": event_type,
             "payload": _jsonb(payload), "created_at": now()})

    def list_events(self, run_id: str, after: int = 0) -> list[dict]:
        return self._all(
            """SELECT * FROM verification_events
               WHERE run_id = %s AND sequence > %s ORDER BY sequence""",
            (run_id, after))

    # -- calls / cache ----------------------------------------------------
    def add_model_call(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO model_calls (id, run_id, claim_id, stage, provider,
                   model, resolved_model, bifrost_request_id, prompt_hash,
                   response_json, input_tokens, output_tokens, cost_usd,
                   cost_source, latency_ms, fallback_attempts, status, cacheable,
                   created_at)
               VALUES (%(id)s, %(run_id)s, %(claim_id)s, %(stage)s, %(provider)s,
                   %(model)s, %(resolved_model)s, %(bifrost_request_id)s,
                   %(prompt_hash)s, %(response_json)s, %(input_tokens)s,
                   %(output_tokens)s, %(cost_usd)s, %(cost_source)s,
                   %(latency_ms)s, %(fallback_attempts)s, %(status)s,
                   %(cacheable)s, %(created_at)s)
               ON CONFLICT (prompt_hash, model, stage) WHERE cacheable DO NOTHING
               RETURNING *""",
            {"cacheable": True, "resolved_model": "",
             "cost_source": "gateway", **row,
             "response_json": _jsonb(row.get("response_json"))}) or \
            self.find_model_call(row["prompt_hash"], row["model"], row["stage"])

    def find_model_call(self, prompt_hash: str, model: str, stage: str) -> dict | None:
        return self._one(
            """SELECT * FROM model_calls WHERE prompt_hash = %s AND model = %s
               AND stage = %s AND status = 'ok' AND cacheable
               ORDER BY created_at DESC LIMIT 1""",
            (prompt_hash, model, stage))

    def cache_get(self, key: str) -> dict | None:
        return self._one("SELECT * FROM research_cache WHERE cache_key = %s", (key,))

    def cache_put(self, key: str, tool: str, payload: dict) -> None:
        self._write(
            """INSERT INTO research_cache (cache_key, tool, payload, fetched_at)
               VALUES (%s, %s, %s, %s) ON CONFLICT (cache_key) DO NOTHING""",
            (key, tool, _jsonb(payload), now()))

    def gateway_stats(self, run_id: str | None = None) -> dict:
        where, params = ("WHERE run_id = %s", (run_id,)) if run_id else ("", ())
        row = self._one(
            f"""SELECT COUNT(*) AS calls,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(SUM(fallback_attempts), 0) AS fallback_attempts,
                       COUNT(*) FILTER (WHERE cost_source = 'catalogue')
                           AS estimated_calls
                FROM model_calls {where}""", params) or {}
        providers = [r["provider"] for r in self._all(
            f"SELECT DISTINCT provider FROM model_calls {where}", params)]
        return {**row, "cost_usd": float(row.get("cost_usd") or 0),
                "estimated_calls": int(row.get("estimated_calls") or 0),
                "providers": [p for p in providers if p]}

    def list_model_calls(self, limit: int = 25,
                         run_id: str | None = None) -> list[dict]:
        where, params = ("WHERE run_id = %s", (run_id,)) if run_id else ("", ())
        return self._all(
            f"""SELECT stage, provider, model, resolved_model, bifrost_request_id,
                       input_tokens, output_tokens, cost_usd, cost_source,
                       latency_ms, fallback_attempts, status, created_at
                FROM model_calls {where}
                ORDER BY created_at DESC LIMIT %s""",
            (*params, limit))

    # -- multi-layer cache entries ----------------------------------------
    def get_cache_entry(self, cache_key: str) -> dict | None:
        return self._one("SELECT * FROM cache_entries WHERE cache_key = %s", (cache_key,))

    def put_cache_entry(self, row: dict) -> dict:
        return self._write(
            """INSERT INTO cache_entries (cache_key, cache_type, cache_version,
                   payload, created_at, expires_at, last_accessed_at, hit_count, metadata)
               VALUES (%(cache_key)s, %(cache_type)s, %(cache_version)s,
                   %(payload)s, NOW(), %(expires_at)s, NOW(), 0, %(metadata)s)
               ON CONFLICT (cache_key) DO UPDATE SET
                   payload = EXCLUDED.payload,
                   expires_at = EXCLUDED.expires_at,
                   last_accessed_at = NOW(),
                   metadata = EXCLUDED.metadata
               RETURNING *""",
            {
                "expires_at": row.get("expires_at"),
                "metadata": _jsonb(row.get("metadata") or {}),
                **row,
                "payload": _jsonb(row.get("payload")),
            })

    def touch_cache_entry(self, cache_key: str) -> None:
        self._write(
            """UPDATE cache_entries
               SET hit_count = hit_count + 1, last_accessed_at = NOW()
               WHERE cache_key = %s""",
            (cache_key,))

    def purge_cache(self, cache_type: str | None = None) -> int:
        if cache_type is None:
            res = self._write("DELETE FROM cache_entries RETURNING cache_key")
            return 1 if res else 0
        res = self._write("DELETE FROM cache_entries WHERE cache_type = %s RETURNING cache_key", (cache_type,))
        return 1 if res else 0

    def purge_cache_by_prefix(self, prefix: str, metadata_contains: str = "") -> int:
        """Purge cache entries whose cache_key starts with `prefix`, optionally
        requiring `metadata_contains` to appear in the entry's metadata."""
        res = self._write(
            """DELETE FROM cache_entries
               WHERE cache_key LIKE %s
                 AND (%s = '' OR metadata::text LIKE %s)
               RETURNING cache_key""",
            (prefix + "%", metadata_contains, f"%{metadata_contains}%"))
        return len(res) if res else 0

    def purge_cache_by_version(self, cache_type: str,
                               previous_version: str,
                               new_version: str) -> int:
        """Purge cache entries of `cache_type` whose cache_version equals
        `previous_version`."""
        res = self._write(
            "DELETE FROM cache_entries WHERE cache_type = %s AND cache_version = %s RETURNING cache_key",
            (cache_type, previous_version))
        return len(res) if res else 0

    def find_revision_by_hash(self, canonical_url: str, content_hash: str) -> dict | None:
        return self._one(
            """SELECT r.* FROM source_revisions r
               JOIN sources s ON r.source_id = s.id
               WHERE s.canonical_url = %s AND r.content_hash = %s""",
            (canonical_url, content_hash))

    # -- quota ledger -----------------------------------------------------
    def record_quota_usage(self, row: dict) -> dict:
        clean = {
            "id": row.get("id") or new_id(),
            "allocated_limit": row.get("allocated_limit", 0),
            **row,
        }
        return self._write(
            """INSERT INTO quota_ledger (id, provider, resource_type, window_type,
                   window_start, window_end, used_amount, allocated_limit, last_updated_at)
               VALUES (%(id)s, %(provider)s, %(resource_type)s, %(window_type)s,
                   %(window_start)s, %(window_end)s, %(used_amount)s, %(allocated_limit)s, NOW())
               ON CONFLICT (provider, resource_type, window_type, window_start) DO UPDATE SET
                   used_amount = quota_ledger.used_amount + EXCLUDED.used_amount,
                   allocated_limit = EXCLUDED.allocated_limit,
                   last_updated_at = NOW()
               RETURNING *""", clean)

    def get_quota_usage(self, provider: str, resource_type: str, window_type: str) -> dict | None:
        return self._one(
            """SELECT * FROM quota_ledger
               WHERE provider = %s AND resource_type = %s AND window_type = %s
               ORDER BY window_start DESC LIMIT 1""",
            (provider, resource_type, window_type))

    # -- conversations & messages -----------------------------------------
    def create_conversation(self, row: dict) -> dict:
        clean = {
            "id": row.get("id") or new_id(),
            "title": row.get("title") or "New Research Conversation",
            "budget_mode": row.get("budget_mode") or "balanced",
            "structured_memory": _jsonb(row.get("structured_memory") or {}),
        }
        return self._write(
            """INSERT INTO conversations (id, title, budget_mode, created_at, updated_at, structured_memory)
               VALUES (%(id)s, %(title)s, %(budget_mode)s, NOW(), NOW(), %(structured_memory)s)
               RETURNING *""", clean)

    def get_conversation(self, conv_id: str) -> dict | None:
        return self._one("SELECT * FROM conversations WHERE id = %s", (conv_id,))

    def list_conversations(self, limit: int = 50) -> list[dict]:
        return self._all("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT %s", (limit,))

    def update_conversation(self, conv_id: str, **fields: Any) -> dict | None:
        allowed = {"title", "budget_mode", "structured_memory"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return self.get_conversation(conv_id)
        params = {"id": conv_id}
        for k, v in clean.items():
            params[k] = _jsonb(v) if k == "structured_memory" else v
        sets = ", ".join(f"{k} = %({k})s" for k in clean)
        return self._write(
            f"UPDATE conversations SET {sets}, updated_at = NOW() WHERE id = %(id)s RETURNING *",
            params)

    def create_message(self, row: dict) -> dict:
        clean = {
            "id": row.get("id") or new_id(),
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "content": row["content"],
            "run_id": row.get("run_id"),
            "citations": _jsonb(row.get("citations") or []),
            "quality_metrics": _jsonb(row.get("quality_metrics") or {}),
        }
        return self._write(
            """INSERT INTO messages (id, conversation_id, role, content, run_id, citations, quality_metrics, created_at)
               VALUES (%(id)s, %(conversation_id)s, %(role)s, %(content)s, %(run_id)s, %(citations)s, %(quality_metrics)s, NOW())
               RETURNING *""", clean)

    def list_messages(self, conv_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM messages WHERE conversation_id = %s ORDER BY created_at ASC",
            (conv_id,))

    def close(self) -> None:
        return None


def build_store() -> VerifyStore:
    want = (settings.PERSISTENCE or "").strip().lower()
    dsn = (settings.DATABASE_URL or "").strip()
    if want == "postgres" and dsn:
        return PgStore(dsn)
    if want == "postgres" and not dsn:
        raise RuntimeError("PERSISTENCE=postgres but DATABASE_URL is empty")
    return LocalStore()
