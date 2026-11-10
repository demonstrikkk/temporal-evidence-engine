"""LLM: OpenCode server primary -> Groq -> Gemini fallback. Timeout, retry, identity.

Every success returns {"data": <parsed JSON>, "provider": ..., "model": ...}.
Every failure raises AppError (transient for 429/5xx/timeout/unreachable,
fatal for 4xx/auth). The OpenCode rung fails transient when the server is
down, so the pipeline degrades to Groq/Gemini instead of stalling.

The OpenCode transport itself lives in opencode.py: the legacy `/api/session`
protocol this module used to inline is gone in opencode 1.18.32 (its model-pin
call 400s, and its prompt call only enqueues, forcing 4s of dead polling).
"""
import asyncio
import json
import random
import re
import time
from collections import deque
from typing import Any

from app.core import pacing
from app.core.config import settings
from app.core.errors import AppError, provider_fatal, provider_transient
from app.providers.llm import opencode as opencode_svc
from app.providers.llm.classify import classify as _classify

#: Ceiling on the whole prompt sent over the OpenCode transport.
#:
#: It is a safety limit, not a budget: the extractor's own per-page budget is
#: `EXTRACT_MAX_CHARS` and it decides how much of a page is read. This sits above
#: that so it never shapes the data, which is what it used to do — it wrapped the
#: instruction and the prompt together, and since the page content is at the
#: tail, the entire overflow fell on the content.
_PROMPT_MAX_CHARS = 32_000


def _interaction_text(dump: dict) -> str:
    """Output text, falling back to model_output step contents (same interaction,
    no new calls). Empty string when the model produced nothing usable."""
    text = (dump.get("output_text") or "").strip()
    if text:
        return text
    parts = []
    for step in dump.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        for block in step.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers. Deterministic sanitization only."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:] if len(lines) > 1 else []
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


# Warm-session bookkeeping moved to opencode.py; these stay as the seam that
# tests and settings reloads already use.
def _reset_session_cache() -> None:
    global _GROQ_CALL_SEM
    opencode_svc.reset_session_cache()
    _GROQ_SPENT.clear()
    _GROQ_CALL_SEM = None


# --- Groq free-tier pacing -------------------------------------------------
#
# The free tier meters 8,000 tokens per minute, so the client keeps its own
# budget instead of discovering the cap through 429s. A mocked SDK in a test
# never spends real tokens and the per-test fixture clears the window, so no
# test pays a cooldown it did not ask for.

_GROQ_WINDOW_S = 60.0
#: Free tier is 8,000 TPM and one extraction prompt is ~4,300. A hard ceiling
#: of one in-flight call is what keeps two pages from booking the same window:
#: the reservation alone cannot, because the second page is already in the SDK
#: when the first lands.
_GROQ_SPENT: deque[tuple[float, int]] = deque()
_GROQ_LOCK: asyncio.Lock | None = None
_GROQ_LOCK_LOOP: Any = None
_GROQ_CALL_SEM: asyncio.Semaphore | None = None
_GROQ_CALL_SEM_LOOP: Any = None


def _estimate_tokens(text: str) -> int:
    """Conservative prompt-token estimate.

    Measured on this model: a 13,569-character extraction prompt was billed as
    4,263 prompt tokens, i.e. ~3.18 chars per token. Dividing by 3.2 rounds
    against us, which is the safe direction for a rate gate.
    """
    return max(1, int(len(text) / 3.2) + 1)


def _groq_tpm_limit() -> int:
    return max(1000, int(getattr(settings, "GROQ_TPM_LIMIT", 8_000) or 8_000))


def _retry_after_s(exc: Exception) -> float | None:
    """What the server asked us to wait, in seconds, if it said.

    Prefer the header; Groq also states it in the message ("Please try again
    in 4.44s"). The old fixed 5s/10s sleeps were shorter than the window the
    server was describing, which is why three retries could all fail inside
    the same minute.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("retry-after")
            if raw not in (None, ""):
                return float(raw)
        except (TypeError, ValueError):
            pass
    match = re.search(r"try again in ([\d.]+)\s*(ms|s)\b", str(exc), re.I)
    if match:
        value = float(match.group(1))
        return value / 1000.0 if match.group(2).lower() == "ms" else value
    return None


def _groq_lock() -> asyncio.Lock:
    """A lock per running loop, so a fresh test loop never inherits a stale one."""
    global _GROQ_LOCK, _GROQ_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _GROQ_LOCK is None or _GROQ_LOCK_LOOP is not loop:
        _GROQ_LOCK = asyncio.Lock()
        _GROQ_LOCK_LOOP = loop
    return _GROQ_LOCK


async def _groq_reserve(tokens: int) -> None:
    """Book `tokens` against the rolling minute, waiting until there is room.

    This is pre-emptive, not reactive: the budget is checked before the call,
    so a 4,200-token prompt cannot be followed by a second one inside the same
    minute. On the free tier that means one real extraction page roughly every
    35-45 seconds, which is the only way the page after it succeeds.
    """
    limit = _groq_tpm_limit()
    async with _groq_lock():
        while True:
            now = time.monotonic()
            while _GROQ_SPENT and now - _GROQ_SPENT[0][0] >= _GROQ_WINDOW_S:
                _GROQ_SPENT.popleft()
            used = sum(n for _, n in _GROQ_SPENT)
            # An empty window always admits: a single prompt larger than the
            # whole minute would otherwise wait forever and never be tried.
            if not _GROQ_SPENT or used + tokens <= limit:
                _GROQ_SPENT.append((now, tokens))
                return
            wait = _GROQ_WINDOW_S - (now - _GROQ_SPENT[0][0]) + 0.5
            await asyncio.sleep(max(0.5, min(wait, _GROQ_WINDOW_S)))


def _groq_call_sem() -> asyncio.Semaphore:
    """One in-flight Groq call, per running loop.

    The runner's extraction pool is sized for a paid provider. Against the
    free tier, six concurrent calls cannot fit in an 8,000-token minute no
    matter how the retries are spaced, so the limit lives here where every
    Groq caller shares it.
    """
    global _GROQ_CALL_SEM, _GROQ_CALL_SEM_LOOP
    loop = asyncio.get_running_loop()
    if _GROQ_CALL_SEM is None or _GROQ_CALL_SEM_LOOP is not loop:
        _GROQ_CALL_SEM = asyncio.Semaphore(1)
        _GROQ_CALL_SEM_LOOP = loop
    return _GROQ_CALL_SEM


def _json_validation_failure(exc: Exception) -> bool:
    """True when Groq rejected its OWN generation under JSON mode.

    That is a `400 json_validate_failed` with an empty `failed_generation`:
    nothing about the request was malformed, so the right response is a
    tolerant retry (and, if that fails, a cascade), not a fatal abort.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            if str(err.get("code") or "") in (
                    "json_validate_failed", "json_validate_failed_error"):
                return True
            if "failed to validate json" in str(err.get("message") or "").lower():
                return True
    return "failed to validate json" in str(exc).lower()


async def gemini_structured(prompt: str, schema: dict) -> dict[str, Any]:
    key = settings.GEMINI_API_KEY
    if not key:
        raise provider_fatal("LLM unavailable: GEMINI_API_KEY unset")
    model = settings.GEMINI_EXTRACT_MODEL
    last: Exception | None = None
    for _ in range(settings.LLM_MAX_RETRIES + 1):
        try:
            def _call() -> str:
                from google import genai
                client = genai.Client(api_key=key)
                it = client.interactions.create(
                    model=model, input=prompt,
                    response_format={"type": "text", "mime_type": "application/json",
                                     "schema": schema})
                text = _strip_fences(_interaction_text(it.model_dump()))
                if not text:
                    errs = it.model_dump().get("errors")
                    raise RuntimeError(f"empty model output (errors={errs})")
                return text
            text = await asyncio.wait_for(asyncio.to_thread(_call), timeout=settings.LLM_TIMEOUT_S)
            return {"data": json.loads(text), "provider": "gemini", "model": model}
        except Exception as e:  # noqa: BLE001 (classified below)
            last = _classify("Gemini", e)
            if getattr(last, "http", 502) != 502:
                raise last
    raise last  # type: ignore[misc]


async def groq_structured(prompt: str, inner_schema: dict) -> dict[str, Any]:
    """Groq JSON, with a tolerant second pass and the free tier's TPM respected.

    Two things this used to get wrong, both measured on a live 12-page run:

    1. `response_format={"type": "json_object"}` is validated by Groq *after*
       generation. Long, quote-heavy pages made the model emit something that
       was not strict JSON and Groq answered HTTP 400 `json_validate_failed`
       with an empty `failed_generation` — nothing to repair from the error.
       That 400 was classified fatal, so the rung died and the chain jumped
       straight past Groq. The request was fine; the sampler was not. So the
       first pass keeps JSON mode and a validation failure retries once
       without it, parsed by the tolerant reader the OpenCode rung already
       uses.

    2. The free tier meters 8,000 tokens per minute, not just requests. One
       extraction prompt is ~4,300 tokens and the runner extracts six pages
       concurrently, so the second page always 429'd. Retries slept 5-15s,
       still inside the same 60s window, and a page that had done nothing
       wrong was reported as a failure. `_groq_reserve` now keeps the client's
       own budget below the cap (and `_groq_call_sem` keeps one call in
       flight), so a run slows down instead of losing pages.
    """
    key = settings.GROQ_API_KEY
    if not key:
        raise provider_fatal("LLM fallback unavailable: GROQ_API_KEY unset")
    model = settings.GROQ_FALLBACK_MODEL
    from app.schemas.evidence import parse_llm_json

    _ = inner_schema  # the caller validates content downstream
    last: Exception | None = None
    # "json_object" first; "text" only if Groq rejected its own generation.
    for mode in ("json_object", "text"):
        json_mode_rejected = False
        for attempt in range(3):
            def _call() -> str:
                from groq import Groq
                kwargs = ({"response_format": {"type": "json_object"}}
                          if mode == "json_object" else {})
                resp = Groq(api_key=key, max_retries=0,
                            timeout=25).chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    **kwargs)
                return resp.choices[0].message.content or "{}"
            try:
                # Both waits are the free tier's throughput, not this page's
                # work: the single in-flight slot and the token budget. The
                # runner credits them back so a page that is queued when its
                # own watchdog fires is not reported `timeout`.
                pacing.begin_wait()
                async with _groq_call_sem():
                    pacing.end_wait()
                    pacing.begin_wait()
                    try:
                        await _groq_reserve(_estimate_tokens(prompt) + 1536)
                    finally:
                        pacing.end_wait()
                    text = await asyncio.wait_for(asyncio.to_thread(_call),
                                                  timeout=settings.LLM_TIMEOUT_S)
                data = (json.loads(text) if mode == "json_object"
                        else parse_llm_json(text))
                return {"data": data, "provider": "groq", "model": model}
            except Exception as e:  # noqa: BLE001 (classified below)
                if mode == "json_object" and _json_validation_failure(e):
                    last = provider_transient(
                        "Groq rejected its own JSON generation; retrying "
                        "without JSON mode")
                    json_mode_rejected = True
                    break
                err = _classify("Groq", e)
                if getattr(err, "http", 502) != 502:
                    raise err
                last = err
                if attempt < 2:
                    wait = _retry_after_s(e) or (5 * (attempt + 1)
                                                 + random.uniform(0, 2))
                    slept = min(max(wait, 0.5), 70)
                    await asyncio.sleep(slept)
                    # Backoff is the window being full, not the page stalling.
                    pacing.record(slept)
        if not json_mode_rejected:
            break
    raise last  # type: ignore[misc]


async def opencode_structured(prompt: str, schema: dict,
                              model: str | None = None) -> dict[str, Any]:
    """Local OpenCode server, pinned model, one synchronous round trip.

    `model` pins a specific model; without it OPENCODE_MODEL is used. The pin
    travels in the message body, and the reply's `info.modelID` decides which
    model is reported, so a silent server-side fallback surfaces as
    `model_mismatch` instead of being relabelled as the requested model.
    """
    from app.schemas.evidence import parse_llm_json
    # The cap is on the *instruction*, not the prompt.
    #
    # It used to wrap the concatenation, so the instruction plus the extractor's
    # own preamble consumed the budget and the last ~1,500 characters of page
    # content were cut — and the content sits at the tail of the prompt, so the
    # truncation fell entirely on the data and never on the instruction. Measured
    # with the real `build_prompt` and a 6-field plan: 1,479 characters of prefix,
    # 10,521 of 12,000 content surviving.
    #
    # Two consequences, both quiet. The tail of every page is never read, and the
    # loss is invisible: the call succeeds, the model returns records, and the
    # fields that lived past the cut come back "NA" — indistinguishable from a
    # page that did not carry them.
    #
    # The instruction is now truncated on its own, which is bounded and stable,
    # and the prompt body passes through whole. The extractor's per-page budget
    # is `EXTRACT_MAX_CHARS`; bounding the transport above it only matters for
    # callers that pass something larger, so the remaining prompt is clipped
    # rather than refused.
    instruction = "Reply with a single JSON object only. No prose, no backticks.\n"
    body = prompt or ""
    room = max(0, _PROMPT_MAX_CHARS - len(instruction))
    if len(body) > room:
        body = body[:room]
    full_prompt = instruction + body
    out = await opencode_svc.chat(model or settings.OPENCODE_MODEL, full_prompt)
    try:
        data = parse_llm_json(out["text"])
    except ValueError as e:
        raise provider_transient(
            f"OpenCode answer never became JSON ({len(out['text'])} chars): {str(e)[:100]}")
    return {"data": data, "provider": "opencode", "model": out["model"],
            "model_requested": out.get("model_requested"),
            "model_mismatch": bool(out.get("model_mismatch")),
            "latency_ms": out.get("latency_ms", 0), "cost": out.get("cost")}


async def structured_generate(prompt: str, schema: dict) -> dict[str, Any]:
    """OpenCode primary -> Groq -> Gemini, transient failures only cascade.

    OPENCODE_STRICT=true makes opencode THE reader: its result (or error) is
    final and cloud fallbacks never fire — no surprise quota burn. Fatal
    errors (auth, bad request) raise immediately in both modes — failing over
    would bill a second provider for a request that is wrong, not unlucky.
    Unexpected non-AppError exceptions propagate unmasked.

    A rung that is switched off or has no address is skipped, not raised: that
    is a configuration choice, and `preflight()` already lets a run start in
    that state. Raising here made the chain disagree with the gate that had
    just approved the run.
    """
    from app.core.errors import AppError
    if settings.OPENCODE_STRICT:
        return await opencode_structured(prompt, schema)
    rungs = ([opencode_structured] if opencode_svc.configured() else []) + [
        groq_structured]
    last: AppError | None = None
    for attempt in rungs:

        try:
            return await attempt(prompt, schema)
        except AppError as e:
            if e.http != 502:  # transient class only
                raise
            last = e
    if settings.GEMINI_ENABLED:
        return await gemini_structured(prompt, schema)
    # The rung is switched off: report the last real failure rather than
    # pretending no provider was ever tried.
    if last is not None:
        raise last
    raise provider_fatal("LLM unavailable: no provider is configured")
