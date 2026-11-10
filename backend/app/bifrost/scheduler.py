"""Centralized request scheduler and concurrency gate for all model inference.

Coordinates all Bifrost complete() requests across threads and workers.
Prevents burst concurrency, regulates token pacing, enforces circuit breaking,
and provides deferred execution semantics when rate limits or quotas trigger.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from app.bifrost.rate_limiter import QuotaLimiter
from app.bifrost.retry_policy import CircuitBreaker, RetryConfig, RetryPolicy
from app.core.config import settings
from app.core.logging import log


class RequestScheduler:
    """Singleton scheduler ensuring paced, bounded model requests across all runs."""

    def __init__(
        self,
        max_concurrency: int = 2,
        rpm_limit: int = 30,
        tpm_limit: int = 8000,
    ) -> None:
        self.semaphore = threading.Semaphore(max_concurrency)
        self.limiter = QuotaLimiter(rpm_limit=rpm_limit, tpm_limit=tpm_limit)
        self.retry_policy = RetryPolicy(
            RetryConfig(
                max_retries=settings.BIFROST_MAX_RETRIES,
                max_delay_s=float(settings.BIFROST_RETRY_AFTER_CAP_S),
            )
        )
        self._lock = threading.RLock()
        self._total_scheduled = 0
        self._total_deferred = 0

    def execute(
        self,
        func: Callable[[], Any],
        *,
        stage: str,
        estimated_tokens: int = 500,
        timeout: float = 60.0,
    ) -> Any:
        """Schedule and execute a model call with bounded concurrency and rate limits."""
        # Check circuit breaker first
        if not self.retry_policy.circuit.allow_request():
            log.warning("circuit breaker open for bifrost inference", extra={"data": {"stage": stage}})
            from app.bifrost.client import BifrostError
            raise BifrostError(
                "transient",
                f"Model gateway circuit breaker is OPEN due to repeated rate limits or provider failures. Stage: {stage}",
                status=429,
            )

        acquired_sem = self.semaphore.acquire(timeout=timeout)
        if not acquired_sem:
            self._total_deferred += 1
            from app.bifrost.client import BifrostError
            raise BifrostError(
                "transient",
                f"Model request concurrency limit reached; could not acquire execution slot for {stage} within {timeout}s",
                status=429,
            )

        try:
            # Acquire quota slot
            acquired_quota = self.limiter.acquire(estimated_tokens=estimated_tokens, timeout=timeout)
            if not acquired_quota:
                self._total_deferred += 1
                from app.bifrost.client import BifrostError
                raise BifrostError(
                    "transient",
                    f"Model request rate limiter wait expired ({timeout}s) for {stage}",
                    status=429,
                )

            with self._lock:
                self._total_scheduled += 1

            # Execute callable
            result = func()
            self.retry_policy.circuit.record_success()
            return result

        except Exception as exc:
            # Check if this error is rate limit or fatal
            status_code = getattr(exc, "status", None)
            is_rate_limit = status_code == 429
            self.retry_policy.circuit.record_failure(is_fatal_or_rate_limit=is_rate_limit)
            raise
        finally:
            self.semaphore.release()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_scheduled": self._total_scheduled,
                "total_deferred": self._total_deferred,
                "limiter": self.limiter.stats(),
                "circuit": self.retry_policy.circuit.stats(),
            }


# Global singleton scheduler
scheduler = RequestScheduler(
    max_concurrency=2,
    rpm_limit=30,
    tpm_limit=getattr(settings, "GROQ_TPM_LIMIT", 8000),
)
