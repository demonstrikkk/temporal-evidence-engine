"""Multi-layer cache coordinator coordinating L0 through L5 caching.

Provides single-flight execution, content-addressed lookup, TTL invalidation,
and metrics across deterministic computation, LLM completions, Tavily searches,
source snapshots, and claim-evidence reuse.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.cache.keys import (
    claim_fingerprint,
    computation_key,
    model_kv_key,
    request_fingerprint,
    source_snapshot_key,
    tavily_query_key,
)
from app.cache.policy import CachePolicy
from app.core.config import RULE_VERSION, settings
from app.core.logging import log


class CacheCoordinator:
    """Central manager for multi-tier caching across the intelligence engine."""

    def __init__(self, store: Any = None) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_results: dict[str, Any] = {}
        self._inflight_errors: dict[str, Exception] = {}

        # In-memory fast cache for L1 pure deterministic computation
        self._l1_mem_cache: dict[str, tuple[Any, float]] = {}

        # Cache metrics
        self._stats = {
            "l0_hits": 0, "l0_misses": 0,
            "l1_hits": 0, "l1_misses": 0,
            "l2_hits": 0, "l2_misses": 0,
            "l3_hits": 0, "l3_misses": 0,
            "l4_hits": 0, "l4_misses": 0,
            "l5_hits": 0, "l5_misses": 0,
            "saved_model_calls": 0,
            "saved_tavily_searches": 0,
            "saved_tavily_extracts": 0,
        }

    def set_store(self, store: Any) -> None:
        with self._lock:
            self._store = store

    @property
    def store(self) -> Any:
        if self._store is not None:
            return self._store
        from app.verify.runtime import get_store
        return get_store()

    # -- Single-flight coordination (L0) ----------------------------------
    def single_flight(self, key: str, action: Callable[[], Any]) -> Any:
        """Run action only once for concurrent identical keys; others await outcome."""
        with self._lock:
            if key in self._inflight:
                event = self._inflight[key]
                is_leader = False
            else:
                event = threading.Event()
                self._inflight[key] = event
                is_leader = True

        if not is_leader:
            # Follower: wait for leader to complete
            event.wait(timeout=120.0)
            with self._lock:
                if key in self._inflight_errors:
                    raise self._inflight_errors[key]
                return self._inflight_results.get(key)

        # Leader: execute and signal followers
        try:
            result = action()
            with self._lock:
                self._inflight_results[key] = result
            return result
        except Exception as exc:
            with self._lock:
                self._inflight_errors[key] = exc
            raise
        finally:
            with self._lock:
                event.set()
                # Clean up in flight after brief buffer
                self._inflight.pop(key, None)
                self._inflight_results.pop(key, None)
                self._inflight_errors.pop(key, None)

    # -- L1: Deterministic computation cache ------------------------------
    def get_l1(self, op_name: str, input_data: Any, version: str = "v1") -> Any | None:
        if settings.CACHE_MODE == "off":
            return None
        key = computation_key(op_name, input_data, version)
        with self._lock:
            if key in self._l1_mem_cache:
                val, exp = self._l1_mem_cache[key]
                if exp == 0.0 or time.time() < exp:
                    self._stats["l1_hits"] += 1
                    return val
                self._l1_mem_cache.pop(key, None)

        # Fallback to persistent store
        entry = self.store.get_cache_entry(key)
        if entry and not CachePolicy.is_expired(entry.get("expires_at")):
            with self._lock:
                self._stats["l1_hits"] += 1
                self._l1_mem_cache[key] = (entry.get("payload"), time.time() + 3600.0)
            self.store.touch_cache_entry(key)
            return entry.get("payload")

        with self._lock:
            self._stats["l1_misses"] += 1
        return None

    def put_l1(self, op_name: str, input_data: Any, result: Any, version: str = "v1", ttl_seconds: int = 86400) -> None:
        if settings.CACHE_MODE == "off":
            return
        key = computation_key(op_name, input_data, version)
        exp_time = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0
        with self._lock:
            self._l1_mem_cache[key] = (result, exp_time)

        self.store.put_cache_entry({
            "cache_key": key,
            "cache_type": "l1_calc",
            "cache_version": version,
            "payload": result,
            "expires_at": CachePolicy.calculate_expiry(ttl_seconds).isoformat() if ttl_seconds else None,
            "metadata": {"op_name": op_name},
        })

    # -- L2: Model KV cache ------------------------------------------------
    def get_l2(self, model: str, stage: str, system: str, user: str,
               temperature: float = 0.0, max_tokens: int = 1400,
               json_mode: bool = True) -> dict[str, Any] | None:
        if settings.CACHE_MODE == "off":
            return None
        key = model_kv_key(model, stage, system, user, temperature, max_tokens, json_mode)
        entry = self.store.get_cache_entry(key)
        if entry and not CachePolicy.is_expired(entry.get("expires_at")):
            with self._lock:
                self._stats["l2_hits"] += 1
                self._stats["saved_model_calls"] += 1
            self.store.touch_cache_entry(key)
            return entry.get("payload")

        with self._lock:
            self._stats["l2_misses"] += 1
        return None

    def put_l2(self, model: str, stage: str, system: str, user: str,
               payload: dict[str, Any], temperature: float = 0.0,
               max_tokens: int = 1400, json_mode: bool = True,
               ttl_seconds: int = 7 * 86400) -> None:
        if settings.CACHE_MODE == "off":
            return
        key = model_kv_key(model, stage, system, user, temperature, max_tokens, json_mode)
        self.store.put_cache_entry({
            "cache_key": key,
            "cache_type": "l2_kv",
            "cache_version": RULE_VERSION,
            "payload": payload,
            "expires_at": CachePolicy.calculate_expiry(ttl_seconds).isoformat() if ttl_seconds else None,
            "metadata": {"model": model, "stage": stage},
        })

    # -- L3: Tavily search query cache ------------------------------------
    def get_l3(self, query: str, search_depth: str = "basic", time_range: str | None = None,
               include_domains: list[str] | None = None, max_results: int = 5) -> dict[str, Any] | None:
        if settings.CACHE_MODE == "off":
            return None
        key = tavily_query_key(query, search_depth, time_range, include_domains, max_results=max_results)
        entry = self.store.get_cache_entry(key)
        if entry and not CachePolicy.is_expired(entry.get("expires_at")):
            with self._lock:
                self._stats["l3_hits"] += 1
                self._stats["saved_tavily_searches"] += 1
            self.store.touch_cache_entry(key)
            return entry.get("payload")

        with self._lock:
            self._stats["l3_misses"] += 1
        return None

    def put_l3(self, query: str, payload: dict[str, Any], search_depth: str = "basic",
               time_range: str | None = None, include_domains: list[str] | None = None,
               max_results: int = 5, ttl_seconds: int = 86400) -> None:
        if settings.CACHE_MODE == "off":
            return
        key = tavily_query_key(query, search_depth, time_range, include_domains, max_results=max_results)
        self.store.put_cache_entry({
            "cache_key": key,
            "cache_type": "l3_query",
            "cache_version": "v1",
            "payload": payload,
            "expires_at": CachePolicy.calculate_expiry(ttl_seconds).isoformat() if ttl_seconds else None,
            "metadata": {"query": query, "search_depth": search_depth},
        })

    # -- L4: Source revision reuse ----------------------------------------
    def find_source_revision(self, canonical_url: str, content_hash: str) -> dict[str, Any] | None:
        rev = self.store.find_revision_by_hash(canonical_url, content_hash)
        with self._lock:
            if rev:
                self._stats["l4_hits"] += 1
                self._stats["saved_tavily_extracts"] += 1
            else:
                self._stats["l4_misses"] += 1
        return rev

    # -- L5: Claim evidence reuse -----------------------------------------
    def get_l5(self, subject: str, predicate: str, qualifiers: dict[str, Any] | None = None,
               value_scope: str = "") -> list[dict[str, Any]] | None:
        """Lookup previously settled candidate evidence for a semantically matching proposition."""
        if settings.CACHE_MODE == "off":
            return None
        key = claim_fingerprint(subject, predicate, qualifiers, value_scope)
        entry = self.store.get_cache_entry(key)
        if entry and not CachePolicy.is_expired(entry.get("expires_at")):
            with self._lock:
                self._stats["l5_hits"] += 1
            self.store.touch_cache_entry(key)
            return entry.get("payload")

        with self._lock:
            self._stats["l5_misses"] += 1
        return None

    def put_l5(self, subject: str, predicate: str, evidence_list: list[dict[str, Any]],
               qualifiers: dict[str, Any] | None = None, value_scope: str = "",
               claim_type: str = "factual", volatility_class: str = "moderate") -> None:
        if settings.CACHE_MODE == "off" or not evidence_list:
            return
        key = claim_fingerprint(subject, predicate, qualifiers, value_scope)
        ttl = CachePolicy.ttl_for_claim(claim_type, volatility_class)
        self.store.put_cache_entry({
            "cache_key": key,
            "cache_type": "l5_evidence",
            "cache_version": RULE_VERSION,
            "payload": evidence_list,
            "expires_at": CachePolicy.calculate_expiry(ttl).isoformat() if ttl else None,
            "metadata": {"subject": subject, "predicate": predicate, "claim_type": claim_type},
        })

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    # -- Cache invalidation -------------------------------------------------
    def invalidate_for_source_revision(self, canonical_url: str,
                                       content_hash: str) -> int:
        """Invalidate L5 claim-evidence cache entries that depend on a source
        whose revision has changed. Reuse eligible evidence, but recompute the
        temporal verdict — never blindly reuse a verdict from stale text."""
        if settings.CACHE_MODE == "off":
            return 0
        removed = self.store.purge_cache_by_prefix("l5:claim:", canonical_url)
        return removed

    def invalidate_for_config_change(self, previous_version: str,
                                     new_version: str) -> int:
        """Invalidate L2 model KV cache entries when the rule/model config
        changes. A cached result must never be presented as live after the
        governing rules have moved."""
        if settings.CACHE_MODE == "off":
            return 0
        removed = self.store.purge_cache_by_version("l2:kv",
                                                     previous_version,
                                                     new_version)
        return removed


# Global singleton
cache_coordinator = CacheCoordinator()
