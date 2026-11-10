"""Time a page spent waiting for provider capacity, not doing its own work.

The Groq free tier meters tokens per minute, so a page can sit in the client's
own budget queue for most of its wall time. That queue is throughput
management, not a stall — but `asyncio.wait_for` charged it to the page
timeout, so a healthy page was reported `timeout` before its first call.
Measured on a live run: pages four onward in a 12-page run died in the queue.

The runner opens a scope per page and cuts the page at
`EXTRACT_PAGE_TIMEOUT_S` of *work*. A provider that knows it is queueing calls
opens a wait with `begin_wait()` and closes it with `end_wait()`; `record()`
books a wait that is already over (a backoff sleep). `total()` includes a wait
that is still open, so a page queued at the moment the watchdog fires is
credited rather than cut. A page whose provider never waits — a hang, a dead
rung — reads 0 and is cut exactly as before, which `test_phase3` pins.

This lives in `core` so the runner never imports a provider: the provider
writes, the runner reads, and neither learns the other's name.
"""
from __future__ import annotations

import contextvars
import time


class WaitScope:
    """Seconds one page has spent queued, plus any wait still in progress."""

    __slots__ = ("seconds", "waiting_since")

    def __init__(self) -> None:
        self.seconds = 0.0
        self.waiting_since: float | None = None

    def begin(self, now: float) -> None:
        if self.waiting_since is None:
            self.waiting_since = now

    def end(self, now: float) -> None:
        if self.waiting_since is not None:
            self.seconds += max(0.0, now - self.waiting_since)
            self.waiting_since = None

    def add(self, seconds: float) -> None:
        if seconds > 0:
            self.seconds += seconds

    def total(self, now: float) -> float:
        extra = 0.0 if self.waiting_since is None else max(0.0, now - self.waiting_since)
        return self.seconds + extra


_CURRENT: contextvars.ContextVar[WaitScope | None] = contextvars.ContextVar(
    "page_wait_scope", default=None)


def open_scope() -> WaitScope:
    """Start accounting for the current task; called once per page."""
    scope = WaitScope()
    _CURRENT.set(scope)
    return scope


def current() -> WaitScope | None:
    """The scope for the current task, or None outside a run."""
    return _CURRENT.get()


def begin_wait() -> None:
    """Mark the start of a wait in the current task; a no-op with no scope."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.begin(time.monotonic())


def end_wait() -> None:
    """Close a wait opened by `begin_wait`; idempotent when nothing is open."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.end(time.monotonic())


def record(seconds: float) -> None:
    """Book a wait that has already elapsed (a backoff sleep)."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.add(seconds)
