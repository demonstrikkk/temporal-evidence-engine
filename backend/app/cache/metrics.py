"""Cache metrics collection and reporting.

Aggregates L0–L5 hit/miss statistics from the CacheCoordinator into
application-visible metrics: saved external calls, hit rates by layer, and
top-level cache effectiveness.
"""
from __future__ import annotations

from typing import Any

from app.cache.coordinator import aggregate_stats


class CacheMetrics:
    """Read-only cache metrics aggregated from every coordinator instance."""

    @staticmethod
    def snapshot() -> dict[str, Any]:
        """Return a point-in-time snapshot of all cache metrics."""
        stats = aggregate_stats()

        total_hits = sum(v for k, v in stats.items()
                         if k.endswith("_hits"))
        total_misses = sum(v for k, v in stats.items()
                           if k.endswith("_misses"))
        total_requests = total_hits + total_misses
        overall_hit_rate = round(total_hits / total_requests, 4) \
            if total_requests > 0 else 0.0

        layer_hit_rates: dict[str, float] = {}
        for layer in ("l0", "l1", "l2", "l3", "l4", "l5"):
            h = stats.get(f"{layer}_hits", 0)
            m = stats.get(f"{layer}_misses", 0)
            t = h + m
            layer_hit_rates[layer] = round(h / t, 4) if t > 0 else 0.0

        saved_calls = stats.get("saved_model_calls", 0)
        saved_searches = stats.get("saved_tavily_searches", 0)
        saved_extracts = stats.get("saved_tavily_extracts", 0)

        return {
            "overall_hit_rate": overall_hit_rate,
            "total_requests": total_requests,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "layer_hit_rates": layer_hit_rates,
            "layer_counts": {
                layer: {
                    "hits": stats.get(f"{layer}_hits", 0),
                    "misses": stats.get(f"{layer}_misses", 0),
                }
                for layer in ("l0", "l1", "l2", "l3", "l4", "l5")
            },
            "external_calls_saved": {
                "model_calls": saved_calls,
                "tavily_searches": saved_searches,
                "tavily_extracts": saved_extracts,
                "total": saved_calls + saved_searches + saved_extracts,
            },
            "raw": stats,
        }


# Singleton instance
cache_metrics = CacheMetrics()
