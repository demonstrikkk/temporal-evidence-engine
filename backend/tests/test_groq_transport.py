"""The Groq rung must not die on its own JSON validation, and must pace itself.

Both defects shipped and cost a live run every record it could have produced:

* `response_format=json_object` is validated by Groq after generation. A long,
  quote-heavy page produced non-strict JSON and the API answered 400
  `json_validate_failed`; that was classified fatal, so the rung aborted and
  the chain never used Groq at all.
* The free tier is 8,000 tokens/minute. Six concurrent ~4,300-token prompts
  cannot fit, and the old 5-15s retries stayed inside the same window.

A fake SDK keeps these hermetic; no network, no key.
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from app.core import config as config_mod
from app.core.errors import AppError
from app.providers.llm import generate as gen


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_groq_state(monkeypatch):
    monkeypatch.setattr(config_mod.settings, "GROQ_API_KEY", "test-key")
    gen._reset_session_cache()
    yield
    gen._reset_session_cache()


def _fake_groq(behaviour):
    """`behaviour(call_index, kwargs)` decides the reply or raises."""

    class _FakeGroq:
        def __init__(self, *a, **k):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return behaviour(self, kwargs)

    return _FakeGroq


def _json_validation_400() -> Exception:
    exc = RuntimeError("Error code: 400 - json_validate_failed")
    exc.body = {"error": {"message": "Failed to validate JSON. Please adjust "
                                     "your prompt.",
                          "code": "json_validate_failed",
                          "type": "invalid_request_error",
                          "failed_generation": ""}}
    return exc


def test_json_validation_failure_retries_without_json_mode(monkeypatch):
    calls: list[dict] = []

    def behaviour(_self, kwargs):
        calls.append(kwargs)
        if kwargs.get("response_format"):
            raise _json_validation_400()
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(
                content='```json\n{"records": [], "coverage": "none"}\n```'))])

    monkeypatch.setitem(sys.modules, "groq",
                        SimpleNamespace(Groq=_fake_groq(behaviour)))
    out = run(gen.groq_structured("Extract as JSON. " * 400, {"type": "object"}))

    assert out["provider"] == "groq"
    assert out["data"]["records"] == []
    assert len(calls) == 2, "one JSON-mode attempt, then one tolerant attempt"
    assert calls[0].get("response_format") == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_a_plain_bad_request_still_fails_fast(monkeypatch):
    """Only a rejected *generation* is retried; a malformed request is not."""
    calls: list[dict] = []

    def behaviour(_self, kwargs):
        calls.append(kwargs)
        raise RuntimeError("400 invalid model or malformed schema")

    monkeypatch.setitem(sys.modules, "groq",
                        SimpleNamespace(Groq=_fake_groq(behaviour)))
    with pytest.raises(AppError) as ei:
        run(gen.groq_structured("Do it.", {"type": "object"}))

    assert ei.value.code == "E_PROVIDER_FATAL"
    assert len(calls) == 1


def test_retry_after_is_read_from_the_message(monkeypatch):
    exc = RuntimeError("Please try again in 4.44s")
    assert gen._retry_after_s(exc) == pytest.approx(4.44)

    exc_ms = RuntimeError("Please try again in 250ms")
    assert gen._retry_after_s(exc_ms) == pytest.approx(0.25)

    response = SimpleNamespace(headers={"retry-after": "12"})
    with_header = RuntimeError("429")
    with_header.response = response
    assert gen._retry_after_s(with_header) == 12.0


def test_a_second_large_prompt_waits_for_the_window(monkeypatch):
    """4,200-token prompts cannot share a 60s minute; the second one waits.

    Exercised with a fake clock so the assertion is about the arithmetic, not
    about the test sitting still for a minute.
    """
    clock = {"now": 1000.0}
    sleeps: list[float] = []

    monkeypatch.setattr(gen.time, "monotonic", lambda: clock["now"])

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(gen.asyncio, "sleep", fake_sleep)

    async def go():
        await gen._groq_reserve(5_700)
        await gen._groq_reserve(5_700)

    run(go())
    assert sleeps, "the second reservation was admitted inside the same minute"
    assert sleeps[0] >= 30, "a 35s-scale cooldown is the free-tier reality"


def test_only_one_groq_call_is_in_flight(monkeypatch):
    """Two pages must not race the same window from the SDK side."""
    state = {"in_flight": 0, "max": 0}

    def behaviour(_self, kwargs):
        state["in_flight"] += 1
        state["max"] = max(state["max"], state["in_flight"])
        try:
            time.sleep(0.02)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"records": []}'))])
        finally:
            state["in_flight"] -= 1

    monkeypatch.setitem(sys.modules, "groq",
                        SimpleNamespace(Groq=_fake_groq(behaviour)))

    async def go():
        await asyncio.gather(
            gen.groq_structured("first page", {"type": "object"}),
            gen.groq_structured("second page", {"type": "object"}),
        )

    run(go())
    assert state["max"] == 1


def test_the_slot_wait_is_recorded_for_the_page_timeout(monkeypatch):
    """The queue wait must be visible to the runner, not charged to the page.

    Without this the runner cuts a page at `EXTRACT_PAGE_TIMEOUT_S` of queue
    time and reports `timeout` for a page the provider never got to.
    """
    from app.core import pacing

    def behaviour(_self, kwargs):
        time.sleep(0.05)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"records": []}'))])

    monkeypatch.setitem(sys.modules, "groq",
                        SimpleNamespace(Groq=_fake_groq(behaviour)))

    async def one():
        scope = pacing.open_scope()
        await gen.groq_structured("page", {"type": "object"})
        return scope.seconds

    async def go():
        return await asyncio.gather(one(), one())

    first, second = run(go())
    # One of the two held the single slot while the other waited for it.
    assert max(first, second) > 0.01


def test_prompt_shape_is_valid_json():
    """The model copies this line, so it must not contain pipe-joined strings."""
    from app.services.extractor import build_prompt

    prompt = build_prompt({"fields": [{"name": "title", "type": "string",
                                       "required": True,
                                       "description": "Title"}]},
                          "https://example.test/a", "A", "content")
    assert '"coverage": "full"|' not in prompt
    assert '"coverage": "full"' in prompt
    assert "full, partial or none" in prompt
