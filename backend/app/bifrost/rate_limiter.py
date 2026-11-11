"""Centralized rate limiting and quota tracking for model requests.

Tracks both token (TPM) and request (RPM) consumption using thread-safe sliding
windows, and reconciles estimates with actual provider response headers
(e.g., Groq's `x-ratelimit-remaining-*` and `retry-after`).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RateLimitWindow:
    window_seconds: float = 60.0
    timestamps: deque[float] = field(default_factory=deque)
    token_counts: deque[tuple[float, int]] = field(default_factory=deque)

    def prune(self, current_time: float) -> None:
        threshold = current_time - self.window_seconds
        while self.timestamps and self.timestamps[0] <= threshold:
            self.timestamps.popleft()
        while self.token_counts and self.token_counts[0][0] <= threshold:
            self.token_counts.popleft()

    def add(self, current_time: float, tokens: int = 0) -> None:
        self.prune(current_time)
        self.timestamps.append(current_time)
        if tokens > 0:
            self.token_counts.append((current_time, tokens))

    @property
    def current_requests(self) -> int:
        return len(self.timestamps)

    @property
    def current_tokens(self) -> int:
        return sum(tokens for _, tokens in self.token_counts)


class QuotaLimiter:
    """Thread-safe rate limiter with header-driven reconciliation."""

    def __init__(
        self,
        rpm_limit: int = 30,
        tpm_limit: int = 8000,
        safety_margin: float = 0.8,
    ) -> None:
        self.rpm_limit = max(1, rpm_limit)
        self.tpm_limit = max(100, tpm_limit)
        self.safety_margin = safety_margin
        self._effective_rpm = max(1, int(self.rpm_limit * self.safety_margin))
        self._effective_tpm = max(100, int(self.tpm_limit * self.safety_margin))

        self._lock = threading.RLock()
        self._window = RateLimitWindow()
        self._pause_until: float = 0.0

        # Observed provider headers
        self._remaining_requests: int | None = None
        self._remaining_tokens: int | None = None
        self._reset_requests_seconds: float | None = None
        self._reset_tokens_seconds: float | None = None
        self._last_header_update: float = 0.0

    def pause_for(self, seconds: float) -> None:
        with self._lock:
            target = time.time() + max(0.0, seconds)
            if target > self._pause_until:
                self._pause_until = target

    def reset(self) -> None:
        """Forget the current window and any provider-reported pause.

        The limiter is process-wide on purpose - it guards one provider key
        shared by every run in this process - so anything that asserts on *how
        long* a call waits has to start from an empty window. That includes a
        test that measures `Retry-After` handling, and an operator who has just
        raised the ceiling and wants the new one to take effect immediately.
        """
        with self._lock:
            self._window = RateLimitWindow()
            self._pause_until = 0.0
            self._remaining_requests = None
            self._remaining_tokens = None
            self._reset_requests_seconds = None
            self._reset_tokens_seconds = None
            self._last_header_update = 0.0

    def is_paused(self) -> bool:
        with self._lock:
            return time.time() < self._pause_until

    def wait_time_needed(self, estimated_tokens: int = 500) -> float:
        """Calculate seconds to wait before a request can proceed safely."""
        with self._lock:
            now = time.time()
            if now < self._pause_until:
                return self._pause_until - now

            self._window.prune(now)

            # Check provider remaining header overrides
            if self._remaining_requests is not None and self._remaining_requests <= 1:
                return max(1.0, self._reset_requests_seconds or 2.0)
            if self._remaining_tokens is not None and self._remaining_tokens < estimated_tokens:
                return max(1.0, self._reset_tokens_seconds or 2.0)

            # Check sliding window
            if self._window.current_requests >= self._effective_rpm:
                oldest = self._window.timestamps[0]
                return max(0.1, (oldest + 60.0) - now)

            if (self._window.current_tokens + estimated_tokens) > self._effective_tpm:
                if self._window.token_counts:
                    oldest_token_time = self._window.token_counts[0][0]
                    return max(0.1, (oldest_token_time + 60.0) - now)
                return 1.0

            return 0.0

    def acquire(self, estimated_tokens: int = 500, timeout: float = 30.0) -> bool:
        """Wait until quota is available or timeout expires."""
        with self._lock:
            wait_sec = self.wait_time_needed(estimated_tokens)
            if wait_sec <= 0.001:
                self._window.add(time.time(), estimated_tokens)
                return True
            if wait_sec > timeout:
                return False

        # Sleep once for the required duration
        time.sleep(wait_sec)
        with self._lock:
            self._window.add(time.time(), estimated_tokens)
            return True

    def update_from_headers(self, headers: dict[str, Any] | Any) -> None:
        """Parse rate limit headers from provider HTTP response."""
        with self._lock:
            now = time.time()
            self._last_header_update = now

            def _get(key: str) -> str | None:
                if hasattr(headers, "get"):
                    v = headers.get(key)
                    if v is None:
                        v = headers.get(key.lower())
                    return str(v) if v is not None else None
                return None

            rem_req = _get("x-ratelimit-remaining-requests")
            if rem_req is not None:
                try:
                    self._remaining_requests = int(rem_req)
                except ValueError:
                    pass

            rem_tok = _get("x-ratelimit-remaining-tokens")
            if rem_tok is not None:
                try:
                    self._remaining_tokens = int(rem_tok)
                except ValueError:
                    pass

            reset_req = _get("x-ratelimit-reset-requests")
            if reset_req is not None:
                try:
                    self._reset_requests_seconds = self._parse_reset_seconds(reset_req)
                except Exception:
                    pass

            reset_tok = _get("x-ratelimit-reset-tokens")
            if reset_tok is not None:
                try:
                    self._reset_tokens_seconds = self._parse_reset_seconds(reset_tok)
                except Exception:
                    pass

            # Record remaining quota and reset times from headers
            # Note: 429 retry-after wait is handled directly by client retry policy

    @staticmethod
    def _parse_reset_seconds(raw: str) -> float:
        text = str(raw).strip().lower()
        if text.endswith("ms"):
            return float(text[:-2]) / 1000.0
        if text.endswith("s"):
            return float(text[:-1])
        if text.endswith("m"):
            return float(text[:-1]) * 60.0
        return float(text)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            self._window.prune(now)
            return {
                "rpm_limit": self.rpm_limit,
                "tpm_limit": self.tpm_limit,
                "effective_rpm": self._effective_rpm,
                "effective_tpm": self._effective_tpm,
                "window_requests": self._window.current_requests,
                "window_tokens": self._window.current_tokens,
                "is_paused": self.is_paused(),
                "paused_seconds_remaining": max(0.0, self._pause_until - now),
                "header_remaining_requests": self._remaining_requests,
                "header_remaining_tokens": self._remaining_tokens,
                "header_reset_requests_s": self._reset_requests_seconds,
                "header_reset_tokens_s": self._reset_tokens_seconds,
            }
