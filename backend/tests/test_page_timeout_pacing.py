"""A queued page is not a stalled page.

The runner's per-page ceiling exists to cut a *stall*: a provider that hangs
must not eat the run's whole budget. With a free-tier provider the page instead
waits in the client's own token queue, and charging that wait to the page
reported healthy pages as `timeout` — measured, the fourth page of a 12-page
run spent its entire `EXTRACT_PAGE_TIMEOUT_S` before its first call.

Both directions are pinned here: credited queue time does not cut the page, and
a page that never queues is still cut exactly at the limit.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core import pacing
from app.services import runner as runner_svc


def run(coro):
    return asyncio.run(coro)


def test_credited_queue_time_does_not_cut_the_page():
    async def go():
        scope = pacing.open_scope()

        async def page():
            pacing.record(2.0)  # the provider queued this page for a token window
            await asyncio.sleep(0.2)  # the page's own work
            return ["record"], "groq"

        return await runner_svc._cut_at_page_timeout(page(), 0.1, scope)

    assert run(go()) == (["record"], "groq")


def test_an_in_flight_wait_is_credited_before_the_cut():
    """A page still queued when the watchdog fires is not a stalled page.

    The provider opens the wait and closes it after the fact; the watchdog
    fires while the wait is open, and must read the partial wait rather than
    the (still zero) completed total.
    """
    async def go():
        scope = pacing.open_scope()

        async def page():
            pacing.begin_wait()
            try:
                await asyncio.sleep(0.3)
            finally:
                pacing.end_wait()
            return ["record"], "groq"

        return await runner_svc._cut_at_page_timeout(page(), 0.1, scope)

    assert run(go()) == (["record"], "groq")


def test_a_stalled_page_is_still_cut_at_the_limit():
    async def go():
        scope = pacing.open_scope()
        cancelled: list[bool] = []

        async def stalled():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return [], "never"

        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await runner_svc._cut_at_page_timeout(stalled(), 0.1, scope)
        return loop.time() - started, cancelled

    elapsed, cancelled = run(go())
    assert elapsed < 1.0, "a stalled page must not hold the run for the whole budget"
    assert cancelled == [True], "the stalled task must be cancelled, not leaked"


def test_the_pages_own_timeout_error_is_not_credited_away():
    async def go():
        scope = pacing.open_scope()

        async def boom():
            pacing.record(5.0)  # queue credit must not mask the page's own failure
            raise asyncio.TimeoutError

        await runner_svc._cut_at_page_timeout(boom(), 1.0, scope)

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        run(go())
