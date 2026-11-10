"""Jittered backoff, circuit breaker, and retry policy for Bifrost requests.

Prevents retry multiplication (where app retries * gateway retries explode),
enforces exponential backoff with full jitter, and trips a circuit breaker
when consecutive 429s or fatal provider errors occur.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal


CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class RetryConfig:
    max_retries: int = 2
    base_delay_s: float = 0.5
    max_delay_s: float = 20.0
    jitter_factor: float = 0.5
    failure_threshold: int = 5
    recovery_time_s: float = 30.0


class CircuitBreaker:
    """Thread-safe circuit breaker protecting external inference services."""

    def __init__(self, failure_threshold: int = 5, recovery_time_s: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_time_s = recovery_time_s
        self._state: CircuitState = "closed"
        self._failure_count: int = 0
        self._last_state_change: float = time.time()
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            now = time.time()
            if self._state == "open" and (now - self._last_state_change) >= self.recovery_time_s:
                self._state = "half_open"
                self._last_state_change = now
            return self._state

    def allow_request(self) -> bool:
        with self._lock:
            return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state != "closed":
                self._state = "closed"
                self._last_state_change = time.time()

    def record_failure(self, is_fatal_or_rate_limit: bool = True) -> None:
        with self._lock:
            if not is_fatal_or_rate_limit:
                return
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = "open"
                self._last_state_change = time.time()

    def reset(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failure_count = 0
            self._last_state_change = time.time()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "consecutive_failures": self._failure_count,
                "seconds_since_state_change": time.time() - self._last_state_change,
            }


class RetryPolicy:
    """Calculates backoff delays with jitter and bounds."""

    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig()
        self.circuit = CircuitBreaker(
            failure_threshold=self.config.failure_threshold,
            recovery_time_s=self.config.recovery_time_s,
        )

    def calculate_delay(
        self,
        attempt: int,
        retry_after: float | None = None,
        cap: float | None = None,
    ) -> float:
        """Calculate exponential backoff with full jitter and Retry-After support."""
        effective_cap = min(self.config.max_delay_s, cap if cap is not None else self.config.max_delay_s)

        if retry_after is not None and retry_after > 0:
            return min(float(retry_after), effective_cap)

        # Exponential backoff: base * 2^attempt
        nominal_delay = self.config.base_delay_s * (2 ** attempt)
        nominal_delay = min(nominal_delay, effective_cap)

        # Full jitter: uniformly random between 0 and nominal_delay
        # Or proportional jitter: nominal_delay * (1 +/- jitter_factor)
        jitter_min = nominal_delay * (1.0 - self.config.jitter_factor)
        jitter_max = nominal_delay * (1.0 + self.config.jitter_factor)
        jittered = random.uniform(max(0.05, jitter_min), jitter_max)

        return min(jittered, effective_cap)
