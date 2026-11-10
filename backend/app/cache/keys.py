"""Canonical cache key and fingerprint generators for L0 through L5 caching.

Keys are content-addressed and versioned so that rule updates, model updates,
or normalizer changes naturally invalidate old entries without destructive purges.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.core.config import RULE_VERSION


def _canonical_json(obj: Any) -> str:
    def _default(val: Any) -> Any:
        if isinstance(val, (date, datetime)):
            return val.isoformat()
        if isinstance(val, set):
            return sorted(list(val))
        return str(val)

    return json.dumps(obj, sort_keys=True, ensure_ascii=True, default=_default, separators=(",", ":"))


def sha256_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# L0: Request fingerprint for single-flight and run deduplication
def request_fingerprint(
    answer: str,
    prompt: str = "",
    generated_at: str | None = None,
    research_policy: str = "balanced",
    rule_version: str = RULE_VERSION,
) -> str:
    payload = {
        "answer": answer.strip(),
        "prompt": prompt.strip(),
        "generated_at": str(generated_at or "").strip(),
        "research_policy": research_policy,
        "rule_version": rule_version,
    }
    return f"l0:req:{sha256_digest(_canonical_json(payload))}"


# L1: Deterministic computation cache key
def computation_key(op_name: str, input_data: Any, version: str = "v1") -> str:
    payload = _canonical_json(input_data)
    return f"l1:calc:{op_name}:{version}:{sha256_digest(payload)}"


# L2: Exact LLM KV cache key
def model_kv_key(
    model: str,
    stage: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 1400,
    json_mode: bool = True,
    pipeline_version: str = RULE_VERSION,
) -> str:
    payload = {
        "model": model,
        "stage": stage,
        "system": system_prompt.strip(),
        "user": user_prompt.strip(),
        "temperature": round(temperature, 3),
        "max_tokens": max_tokens,
        "json_mode": json_mode,
        "pipeline_version": pipeline_version,
    }
    return f"l2:kv:{sha256_digest(_canonical_json(payload))}"


# L3: Tavily search query cache key
def tavily_query_key(
    query: str,
    search_depth: str = "basic",
    time_range: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    max_results: int = 5,
    policy_version: str = "v1",
) -> str:
    payload = {
        "query": query.strip().lower(),
        "search_depth": search_depth,
        "time_range": time_range or "",
        "include_domains": sorted([d.lower() for d in (include_domains or [])]),
        "exclude_domains": sorted([d.lower() for d in (exclude_domains or [])]),
        "max_results": max_results,
        "policy_version": policy_version,
    }
    return f"l3:query:{sha256_digest(_canonical_json(payload))}"


# L4: Extracted source snapshot key
def source_snapshot_key(canonical_url: str, content_hash: str) -> str:
    return f"l4:source:{sha256_digest(canonical_url)}:{content_hash}"


# L5: Normalized claim fingerprint for evidence candidate reuse
def claim_fingerprint(
    subject: str,
    predicate: str,
    qualifiers: dict[str, Any] | None = None,
    value_scope: str = "",
    normalizer_version: str = "v1",
) -> str:
    payload = {
        "subject": subject.strip().lower(),
        "predicate": predicate.strip().lower(),
        "qualifiers": qualifiers or {},
        "value_scope": value_scope.strip().lower(),
        "normalizer_version": normalizer_version,
    }
    return f"l5:claim:{sha256_digest(_canonical_json(payload))}"
