"""Multi-layer cache statistics and management API."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.cache.metrics import cache_metrics
from app.verify.runtime import get_store

router = APIRouter(prefix="/api/cache", tags=["cache"],
                   dependencies=[Depends(require_api_key)])


class PurgeIn(BaseModel):
    cache_type: Optional[str] = None


@router.get("/stats")
async def cache_stats() -> dict[str, Any]:
    """The shape the quota rail reads: hit rates, layer counts, saved calls."""
    return cache_metrics.snapshot()


@router.post("/purge")
async def purge_cache(body: PurgeIn) -> dict[str, Any]:
    store = get_store()
    removed = await asyncio.to_thread(store.purge_cache, body.cache_type)
    return {
        "status": "purged",
        "cache_type": body.cache_type or "all",
        "entries_removed": removed,
    }
