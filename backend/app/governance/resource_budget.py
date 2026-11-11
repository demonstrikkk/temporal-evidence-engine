"""Research Budget Mode management and policies.

Controls depth, search escalation, and quota conservation without compromising
the deterministic evidence settlement standard.
"""
from __future__ import annotations

import threading
from typing import Any, Literal

from app.core.config import settings

BudgetMode = Literal["balanced", "conserve", "deep", "offline"]


class BudgetManager:
    """Manages system-wide and session-level budget modes."""

    def __init__(self, default_mode: BudgetMode = "balanced") -> None:
        self._mode: BudgetMode = default_mode
        self._lock = threading.RLock()

    @property
    def current_mode(self) -> BudgetMode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: BudgetMode) -> None:
        if mode not in ("balanced", "conserve", "deep", "offline"):
            raise ValueError(f"Invalid budget mode: {mode}")
        with self._lock:
            self._mode = mode

    def get_policy(self, mode: BudgetMode | None = None) -> dict[str, Any]:
        target = mode or self.current_mode
        if target == "conserve":
            return {
                "mode": "conserve",
                "max_queries_per_claim": 1,
                "allow_live_search": True,
                "prefer_cached_evidence": True,
                "search_depth": "basic",
                "description": "Conserve quota: prefers cached sources and limits search to 1 basic query.",
            }
        elif target == "deep":
            return {
                "mode": "deep",
                # Deeper than the default, but bounded by the same ceiling the
                # settings allow so "deep" cannot become an unbounded spend.
                "max_queries_per_claim": min(
                    6, settings.RESEARCH_MAX_QUERIES_PER_CLAIM + 3),
                "allow_live_search": True,
                "prefer_cached_evidence": False,
                "search_depth": "advanced",
                "description": "Deep research: conducts multiple targeted queries and independent corroboration.",
            }
        elif target == "offline":
            return {
                "mode": "offline",
                "max_queries_per_claim": 0,
                "allow_live_search": False,
                "prefer_cached_evidence": True,
                "search_depth": "none",
                "description": "Offline mode: 0 network calls; relies solely on stored sources and cached queries.",
            }
        else:
            return {
                "mode": "balanced",
                "max_queries_per_claim": settings.RESEARCH_MAX_QUERIES_PER_CLAIM,
                "allow_live_search": True,
                "prefer_cached_evidence": True,
                "search_depth": "basic",
                "description": "Balanced mode: default adaptive research balancing quality and quota.",
            }


# Global singleton
budget_manager = BudgetManager()
