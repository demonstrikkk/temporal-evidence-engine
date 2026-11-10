"""Governance, quota tracking, and infrastructure diagnostics API."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.bifrost.client import BifrostClient
from app.cache.coordinator import cache_coordinator
from app.cache.metrics import cache_metrics
from app.core.config import RULE_VERSION, settings
from app.governance.quota_ledger import quota_ledger
from app.governance.resource_budget import budget_manager
from app.verify.runtime import get_store

router = APIRouter(prefix="/api/governance", tags=["governance"],
                   dependencies=[Depends(require_api_key)])


class BudgetModeIn(BaseModel):
    mode: Literal["balanced", "conserve", "deep", "offline"]


class ConfigVersionIn(BaseModel):
    previous_version: str
    new_version: str


@router.get("/quota")
async def get_quota() -> dict[str, Any]:
    summary = quota_ledger.get_summary()
    cache_stats = cache_coordinator.stats()
    return {
        "quota": summary,
        "cache_savings": cache_stats,
        "current_budget_mode": budget_manager.current_mode,
        "budget_policy": budget_manager.get_policy(),
    }


@router.get("/cache/metrics")
async def cache_metrics_endpoint() -> dict[str, Any]:
    return cache_metrics.snapshot()


@router.post("/cache/invalidate-config")
async def invalidate_config(body: ConfigVersionIn) -> dict[str, Any]:
    """Invalidate L2 model KV cache entries when rule/model config changes."""
    removed = cache_coordinator.invalidate_for_config_change(
        body.previous_version, body.new_version)
    return {
        "status": "invalidated",
        "cache_type": "l2_kv",
        "previous_version": body.previous_version,
        "new_version": body.new_version,
        "entries_removed": removed,
    }


@router.post("/budget-mode")
async def set_budget_mode(body: BudgetModeIn) -> dict[str, Any]:
    budget_manager.set_mode(body.mode)
    return {
        "mode": budget_manager.current_mode,
        "policy": budget_manager.get_policy(body.mode),
    }


@router.get("/bifrost/diagnostic")
async def bifrost_diagnostic() -> dict[str, Any]:
    store = get_store()
    client = BifrostClient(store)
    diag = await asyncio.to_thread(client.diagnostics)
    return diag
