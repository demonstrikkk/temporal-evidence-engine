"""The pacer must know what a call costs before the provider tells it.

Two defects made a 30-call run walk into a rate limit that a 25-second pause
would have avoided, and both are cheap to pin:

* the client told the limiter a call cost `max_tokens // 2` (~500) when the
  provider billed 2,671 input tokens, so the window never filled;
* the limiter's ceiling was hard-wired to Groq's 8,000 even when the run was
  pointed at another provider through the same gateway.
"""
from __future__ import annotations

import time

from app.bifrost.client import _estimate_tokens
from app.bifrost.rate_limiter import QuotaLimiter
from app.bifrost.scheduler import configured_tpm_limit
from app.core.config import settings


def test_estimate_counts_the_prompt_not_only_the_output_budget():
    prompt = "x" * 4_000  # ~1,000 tokens at four characters per token
    assert _estimate_tokens("", prompt, 500) == 1_500


def test_estimate_counts_the_system_prompt_too():
    shrink = _estimate_tokens("", "x" * 400, 200)
    grow = _estimate_tokens("y" * 1_200, "x" * 400, 200)
    assert grow - shrink == 300


def test_estimate_has_a_floor_so_a_tiny_call_still_reserves_something():
    assert _estimate_tokens("", "", 0) == 200


def test_tpm_ceiling_prefers_the_gateway_setting(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_TPM_LIMIT", 8_000)
    monkeypatch.setattr(settings, "BIFROST_TPM_LIMIT", 60_000)
    assert configured_tpm_limit() == 60_000


def test_tpm_ceiling_falls_back_to_the_groq_setting(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_TPM_LIMIT", 5_000)
    monkeypatch.setattr(settings, "BIFROST_TPM_LIMIT", None)
    assert configured_tpm_limit() == 5_000


def test_a_call_that_would_exceed_the_window_waits_instead_of_firing():
    limiter = QuotaLimiter(rpm_limit=100, tpm_limit=1_000)
    assert limiter.wait_time_needed(estimated_tokens=300) == 0.0

    limiter._window.add(time.time(), 900)  # noqa: SLF001 - the window is the unit under test

    # 900 already spent + 300 requested > 1,000: the honest answer is "wait",
    # which is what keeps the provider from being the one to say 429.
    assert limiter.wait_time_needed(estimated_tokens=300) > 0.0
