"""Persistent quota accounting for Tavily credits, Groq tokens, and model calls."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.verify.store import VerifyStore, iso, now


class QuotaLedger:
    """Tracks and reconciles usage against monthly and daily free-tier quotas."""

    def __init__(self, store: VerifyStore | None = None) -> None:
        self._store = store
        self._lock = threading.RLock()

        # In-memory session trackers
        self._tavily_searches = 0
        self._tavily_extracts = 0
        self._tavily_credits_used = 0
        self._groq_requests = 0
        self._groq_tokens = 0

    def set_store(self, store: VerifyStore) -> None:
        with self._lock:
            self._store = store

    @property
    def store(self) -> VerifyStore:
        if self._store is not None:
            return self._store
        from app.verify.runtime import get_store
        return get_store()

    def record_tavily_usage(self, tool: str, credits: int = 1) -> None:
        with self._lock:
            if tool == "search":
                self._tavily_searches += 1
            elif tool == "extract":
                self._tavily_extracts += 1
            self._tavily_credits_used += credits

        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.store.record_quota_usage({
            "provider": "tavily",
            "resource_type": "credits",
            "window_type": "month",
            "window_start": iso(month_start),
            "window_end": iso(month_start),
            "used_amount": credits,
            "allocated_limit": 1000,
        })

    def record_model_usage(self, tokens: int, provider: str = "groq") -> None:
        with self._lock:
            self._groq_requests += 1
            self._groq_tokens += tokens

        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.store.record_quota_usage({
            "provider": provider,
            "resource_type": "tokens",
            "window_type": "day",
            "window_start": iso(day_start),
            "window_end": iso(day_start),
            "used_amount": tokens,
            "allocated_limit": 500_000,
        })

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            tavily_row = self.store.get_quota_usage("tavily", "credits", "month") or {}
            groq_row = self.store.get_quota_usage("groq", "tokens", "day") or {}

            used_tavily = int(tavily_row.get("used_amount") or self._tavily_credits_used)
            used_groq = int(groq_row.get("used_amount") or self._groq_tokens)

            return {
                "tavily": {
                    "monthly_limit": 1000,
                    "credits_used": used_tavily,
                    "credits_remaining": max(0, 1000 - used_tavily),
                    "session_searches": self._tavily_searches,
                    "session_extracts": self._tavily_extracts,
                },
                "groq": {
                    "daily_tokens_used": used_groq,
                    "session_requests": self._groq_requests,
                    "session_tokens": self._groq_tokens,
                },
            }


# Global singleton
quota_ledger = QuotaLedger()
