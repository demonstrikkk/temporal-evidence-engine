"""Cache TTL policies, volatility mappings, and invalidation rules.

Defines expiration intervals for claim categories, sources, and query results.
Guarantees that a cached fact is never represented as live or current without
checking temporal validity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


# Default TTL mappings by claim taxonomy/volatility
TAXONOMY_TTL_SECONDS: dict[str, int] = {
    "historical_event": 30 * 86400,   # 30 days
    "scientific_claim": 14 * 86400,   # 14 days
    "statistical_claim": 14 * 86400,  # 14 days
    "regulatory": 7 * 86400,          # 7 days
    "product_capability": 7 * 86400,  # 7 days
    "software_version": 86400,        # 24 hours
    "api_limit": 86400,               # 24 hours
    "pricing": 6 * 3600,              # 6 hours
    "leadership": 6 * 3600,           # 6 hours
    "company_metric": 6 * 3600,       # 6 hours
    "availability": 15 * 60,          # 15 minutes
    "other": 24 * 3600,               # 24 hours
}

VOLATILITY_TTL_SECONDS: dict[str, int] = {
    "static": 30 * 86400,             # 30 days
    "slow": 7 * 86400,                # 7 days
    "moderate": 24 * 3600,            # 24 hours
    "fast": 6 * 3600,                 # 6 hours
    "rapid": 15 * 60,                 # 15 minutes
}


class CachePolicy:
    """Evaluates TTL and freshness for cache entries."""

    @staticmethod
    def ttl_for_claim(claim_type: str, volatility_class: str = "moderate") -> int:
        type_ttl = TAXONOMY_TTL_SECONDS.get(claim_type.lower())
        vol_ttl = VOLATILITY_TTL_SECONDS.get(volatility_class.lower())

        if type_ttl and vol_ttl:
            return min(type_ttl, vol_ttl)
        return type_ttl or vol_ttl or 86400

    @staticmethod
    def ttl_for_query(topic: str = "general") -> int:
        if topic == "news":
            return 900  # 15 mins
        if topic == "finance":
            return 3600  # 1 hour
        return 86400  # 24 hours default for general search

    @staticmethod
    def calculate_expiry(ttl_seconds: int | None = None) -> datetime | None:
        if ttl_seconds is None or ttl_seconds <= 0:
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    @staticmethod
    def is_expired(expires_at: datetime | str | None) -> bool:
        if not expires_at:
            return False
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                return False
        now_dt = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now_dt >= expires_at
