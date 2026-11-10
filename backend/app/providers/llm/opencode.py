"""OpenCode server transport — the only sanctioned path to gated Zen models.

Two wires, one client. The same binary family speaks both, and the user's
install decides which:

  v1 (opencode 1.x)                    v2 (opencode 2.x, measured 2.2.0)
  POST /session                        GET  /api/info
  POST /session/{id}/message           POST /api/experimental/generate/text
  DELETE /session/{id}                 body: {"prompt": ..., "model":
                                            {"providerID": ..., "id": ...}}
                                       -> {"data": {"text": ...}}

The v2 wire is detected by asking `/api/info` for JSON and is cached per base
URL, so pointing `OPENCODE_BASE_URL` at either version works without a config
switch. A v2 install used to read as `wire_mismatch` — the web UI answers
`GET /session` with HTML — which was true and still meant no model could serve
the run. Now it is a supported transport rather than a diagnosis.

Why `generate/text` and not v2 sessions: a session runs agents, tools and
permission prompts, and DataGoblin wants one JSON answer from one model. The
one-shot endpoint is stateless, so nothing to pool, nothing to leak, and no
cross-serving between concurrent callers.

v1 semantics are unchanged and still the fallback:

  POST /session                        -> {"id": ...}
  POST /session/{id}/message            -> {"info": {...}, "parts": [...]}
      body: {"model": {"providerID": "opencode", "modelID": "<id>"},
             "parts": [{"type": "text", "text": "..."}]}
  DELETE /session/{id}                  -> session teardown

`info.modelID` in a v1 reply is the model that actually answered, so the caller
never has to trust its own request. A reply naming a different model than the
one pinned is reported as `model_mismatch` rather than hidden, so a silent
fallback shows up in the result instead of quietly relabelling the answer.

v2 `generate/text` reports only text. The served model is therefore the one that
was asked for, and no mismatch claim is made that the server did not support.

Sessions are pooled, not shared. A single warm session id handed to concurrent
callers is unsafe on this server: four concurrent messages posted to one session
were all served by that session's pinned model and every caller received the same
reply, which would have made two run workers record each other's extractions. A
session is now owned by exactly one in-flight call and recycled afterwards, so
warm-start is kept and cross-serving is not. Creation stays locked so a parallel
fan-out cannot stampede session creation.
"""
import asyncio
import base64
import json
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AppError, provider_fatal, provider_transient
from app.core.logging import log

#: Idle session pool (LIFO). Bounded by _SESSION_POOL_MAX; entries are retired
#: after _SESSION_MAX_USES or _SESSION_TTL_S.
_session_pool: list[dict] = []
_SESSION_POOL_MAX = 4
_SESSION_MAX_USES = 6
_SESSION_TTL_S = 300.0
_open_lock = asyncio.Lock()

#: Confirmed wire per base URL ("v1"/"v2"). Only positive detections are cached:
#: caching "v1" for an unreachable host would pin the wrong wire after it comes
#: back as v2.
_wire_cache: dict[str, str] = {}


def _reset_session_cache() -> None:
    _session_pool.clear()
    _wire_cache.clear()



def reset_session_cache() -> None:
    """Public alias (tests + config reloads)."""
    _reset_session_cache()


def _base() -> str:
    return (settings.OPENCODE_BASE_URL or "").rstrip("/")


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if settings.OPENCODE_PASSWORD:
        token = base64.b64encode(
            f"opencode:{settings.OPENCODE_PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return headers


def _preflight() -> None:
    """Rung disabled / unconfigured are fatal configuration, not a blip."""
    if not settings.OPENCODE_ENABLED:
        raise provider_fatal("OpenCode rung disabled (OPENCODE_ENABLED=false)")
    if not _base():
        raise provider_fatal("OpenCode rung unconfigured (OPENCODE_BASE_URL empty)")


def configured() -> bool:
    """Can this rung be attempted at all?

    Disabled (`OPENCODE_ENABLED=false`) or unaddressed (`OPENCODE_BASE_URL`
    empty) is a deployment *choice*, not a broken provider. The cascade reads
    this so it can skip the rung and let the next provider serve, instead of
    raising the fatal `_preflight` error and failing a request Groq could have
    answered. `preflight()` makes the same distinction, which is why a run
    starts fine in that configuration; the chain used to disagree with it.

    Strict mode deliberately ignores this: there the local reader is the only
    permitted provider, so an unconfigured rung must be reported, not routed
    around.
    """
    return bool(settings.OPENCODE_ENABLED and _base())


def _split_model(spec: str) -> tuple[str, str | None]:
    """Accept both `model-id` and the `provider/model-id` form.

    Existing .env files carry `OPENCODE_MODEL=opencode/muse-spark-1.3-contributor-free`
    (that is how opencode's own config addresses Zen models), but the v1 wire
    format wants the provider and the model as separate fields. Sending the
    prefixed string as `modelID` 404s / silently falls back, so the prefix is
    split off here and the provider is honoured.
    """
    spec = (spec or "").strip()
    if "/" in spec:
        provider, _, model = spec.partition("/")
        if provider and model:
            return model, provider
    return spec, None


def _part_text(parts: Any) -> str:
    """Assistant text from a v1 message reply.

    `synthetic` parts are server-generated scaffolding (tool plumbing, notices),
    not the model's answer, so they are excluded.
    """
    out: list[str] = []
    for p in parts if isinstance(parts, list) else []:
        if not isinstance(p, dict) or p.get("type") != "text":
            continue
        if p.get("synthetic") or p.get("ignored"):
            continue
        text = p.get("text")
        if text:
            out.append(str(text))
    return "\n".join(out).strip()


async def _delete_session(client: Any, sid: str, headers: dict) -> None:
    try:
        await client.delete(f"{_base()}/session/{sid}", headers=headers)
    except Exception:  # noqa: BLE001 (hygiene only; sessions expire server-side)
        pass


async def _drop_session(client: Any, sid: str, headers: dict) -> None:
    """Delete and evict — for a call that failed, so nothing broken is recycled."""
    _session_pool[:] = [e for e in _session_pool if e["sid"] != sid]
    await _delete_session(client, sid, headers)


async def _acquire_session(client: Any, headers: dict) -> tuple[str, bool]:
    """Check a session out EXCLUSIVELY. Returns (sid, fresh).

    A single shared session id is not safe under concurrency. Measured against a
    live server: four concurrent messages posted to one session were all served
    by whichever model that session was pinned to, and all four callers received
    the *same* reply. Two run workers extracting two different pages would
    silently record each other's records. A session is therefore owned by exactly
    one in-flight call and returned to the idle pool only when that call ends.
    """
    loop = asyncio.get_running_loop()
    async with _open_lock:
        now = loop.time()
        while _session_pool:
            entry = _session_pool.pop()
            if (entry["uses"] < _SESSION_MAX_USES
                    and now - entry["at"] < _SESSION_TTL_S):
                entry["uses"] += 1
                entry["at"] = now
                return entry["sid"], False
            await _delete_session(client, entry["sid"], headers)  # stale
        try:
            r = await client.post(f"{_base()}/session", json={}, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise provider_transient(f"OpenCode unreachable: {str(e)[:120]}")
        if r.status_code in (401, 403):
            raise provider_fatal(f"OpenCode auth failed (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise provider_transient(f"OpenCode session refused (HTTP {r.status_code})")
        try:
            new_sid = (r.json() or {}).get("id", "")
        except Exception:  # noqa: BLE001
            new_sid = ""
        if not new_sid:
            raise provider_transient("OpenCode session created without id")
        return new_sid, True


async def _release_session(client: Any, sid: str, headers: dict) -> None:
    """Return a healthy session to the idle pool; delete it if there is no room."""
    loop = asyncio.get_running_loop()
    async with _open_lock:
        if len(_session_pool) < _SESSION_POOL_MAX:
            _session_pool.append({"sid": sid, "uses": 1, "at": loop.time()})
            return
    await _delete_session(client, sid, headers)


def _status_error(status: int, body: str) -> Exception:
    """4xx = the request is wrong (stop). 5xx/429 = unlucky (cascade)."""
    if status in (401, 403):
        return provider_fatal(f"OpenCode auth failed (HTTP {status})")
    if status == 404:
        # Measured, do not speculate. A 404 here is NOT a version mismatch: the
        # same endpoint answered 200 for every other model in the same run, and a
        # genuinely unknown model id returns 500 UnknownError, not 404. Read as
        # "this model was not served on this request" and retryable, because
        # that is the only cause actually observed - a claim of a version
        # mismatch here is an unearned guess that sends people to debug the
        # wrong thing.
        return provider_transient(
            f"OpenCode did not serve this model on this request (HTTP 404). "
            f"The endpoint itself is reachable; treat as a retryable miss.")
    if status == 429:
        return provider_transient(f"OpenCode rate-limited (HTTP 429)")
    if status >= 500:
        return provider_transient(f"OpenCode server error (HTTP {status}): {body[:120]}")
    return provider_fatal(f"OpenCode refused request (HTTP {status}): {body[:120]}")


def _is_json(r: Any) -> bool:
    ctype = str((getattr(r, "headers", {}) or {}).get("content-type", "") or "").lower()
    return "json" in ctype


async def _detect_wire(client: Any, headers: dict) -> str:
    """'v2' or 'v1' for the configured server. One probe per process.

    `/api/info` exists only on v2, so a JSON 200 is conclusive. Any other
    outcome falls through to the v1 session flow, which is where the error that
    actually explains the fault is produced (auth, unreachable, wrong wire).
    """
    cached = _wire_cache.get(_base())
    if cached:
        return cached
    try:
        r = await client.get(f"{_base()}/api/info", headers=headers)
    except Exception:  # noqa: BLE001 (the v1 path reports unreachable honestly)
        return "v1"
    if r.status_code == 200 and _is_json(r):
        _wire_cache[_base()] = "v2"
        return "v2"
    if r.status_code == 404:
        _wire_cache[_base()] = "v1"
    return "v1"


def _join_prompt(system: str | None, prompt: str) -> str:
    """The v2 one-shot endpoint takes text, not parts; keep the order v1 uses."""
    return f"{system}\n\n{prompt}" if system else (prompt or "")


def _v2_detail(body: str) -> str:
    """Turn the v2 error envelope into one line a reader can act on.

    Measured shape: {"_tag": "ProviderRequestError", "reason": "rate_limit",
    "message": "...", "status": 429, "retryAfterMs": 65111000}. The HTTP status
    can be 502 while the provider's own status is 429, and losing that
    difference turns "quota exhausted" into "server broken".
    """
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return body[:200]
    if not isinstance(data, dict):
        return body[:200]
    bits = [str(data[k]) for k in ("_tag", "reason", "message") if data.get(k)]
    if data.get("retryAfterMs"):
        bits.append(f"retry after {int(data['retryAfterMs']) / 1000:.0f}s")
    return "; ".join(bits)[:280]


def _v2_status_error(status: int, body: str) -> Exception:
    """Same classes as v1, with one measured difference: v2 wraps provider
    failures in 502. A rate limit is transient, not a broken server."""
    detail = _v2_detail(body)
    if status in (401, 403):
        return provider_fatal(f"OpenCode auth failed (HTTP {status})")
    if status == 422:
        return provider_fatal(
            f"OpenCode does not support this model (HTTP 422): {detail}")
    if status == 400:
        return provider_fatal(f"OpenCode rejected request (HTTP 400): {detail}")
    if status == 404:
        return provider_transient(
            f"OpenCode did not serve this model on this request (HTTP 404): {detail}")
    if status == 409:
        return provider_transient(f"OpenCode session conflict (HTTP 409): {detail}")
    if status == 429:
        return provider_transient(f"OpenCode rate-limited (HTTP 429): {detail}")
    if status >= 500:
        return provider_transient(
            f"OpenCode provider error (HTTP {status}): {detail}")
    return provider_fatal(f"OpenCode refused request (HTTP {status}): {detail}")


async def _v2_chat(client: Any, headers: dict, model: str, provider: str,
                   prompt_text: str, started: float) -> dict[str, Any]:
    """One stateless generation over the v2 wire.

    `Model.Ref` is `{providerID, id}` — not the v1 `{providerID, modelID}`. The
    model travels in the same body rather than being pinned on a session.
    """
    r = await client.post(
        f"{_base()}/api/experimental/generate/text",
        json={"prompt": prompt_text,
              "model": {"providerID": provider, "id": model}},
        headers=headers)
    if r.status_code >= 400:
        raise _v2_status_error(r.status_code, r.text)
    try:
        payload = r.json() or {}
    except Exception as e:  # noqa: BLE001
        raise provider_transient(f"OpenCode v2 reply not JSON: {str(e)[:100]}")
    text = str(((payload.get("data") or {}).get("text") or "")).strip()
    if not text:
        raise provider_transient("OpenCode v2 returned no text")
    return {"text": text,
            "model": model,
            "model_requested": model,
            # `generate/text` returns text only; claiming a served-model check
            # the wire does not provide would be a made-up provenance.
            "model_mismatch": False,
            "provider": provider,
            "cost": None,
            "tokens": {"input": 0, "output": 0, "reasoning": 0},
            "latency_ms": int((time.perf_counter() - started) * 1000)}


async def chat(model: str, prompt: str, *, system: str | None = None,
               timeout: float | None = None) -> dict[str, Any]:
    """One pinned, synchronous OpenCode call.

    Returns {"text", "model", "provider", "latency_ms", "cost", "tokens"}.
    Raises AppError — fatal for auth/4xx, transient for 5xx/429/timeout —
    so the caller's cascade behaviour stays correct.
    """
    _preflight()
    # The REQUESTED model wins; OPENCODE_MODEL is only the fallback.
    # Regression, measured: `settings.OPENCODE_MODEL or model` silently pinned
    # every call to muse-spark-1.3, so a fan-out asking for four different
    # models got four answers from that one model — the registry, the
    # per-model provenance and the agreement score were all fiction.
    model, spec_provider = _split_model(model or settings.OPENCODE_MODEL)
    if not model:
        raise provider_fatal("OpenCode rung unconfigured (OPENCODE_MODEL empty)")
    provider = spec_provider or settings.OPENCODE_PROVIDER_ID or "opencode"

    parts: list[dict] = []
    if system:
        parts.append({"type": "text", "text": system})
    parts.append({"type": "text", "text": prompt or ""})
    body = {"model": {"providerID": provider, "modelID": model},
            "parts": parts}
    headers = _headers()
    limit = timeout or settings.OPENCODE_TIMEOUT_S
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(limit, connect=10)) as client:
            if await _detect_wire(client, headers) == "v2":
                return await _v2_chat(client, headers, model, provider,
                                      _join_prompt(system, prompt), started)
            sid, _fresh = await _acquire_session(client, headers)

            try:
                r = await client.post(f"{_base()}/session/{sid}/message",
                                      json=body, headers=headers)
            except Exception as e:  # noqa: BLE001
                await _drop_session(client, sid, headers)
                raise provider_transient(f"OpenCode message failed: {str(e)[:120]}")
            if r.status_code >= 400:
                await _drop_session(client, sid, headers)
                raise _status_error(r.status_code, r.text)
            try:
                payload = r.json() or {}
            except Exception as e:  # noqa: BLE001
                await _drop_session(client, sid, headers)
                raise provider_transient(f"OpenCode reply not JSON: {str(e)[:100]}")

            info = payload.get("info") or {}
            # 200 can still carry a model-side failure.
            if info.get("error"):
                await _drop_session(client, sid, headers)
                raise provider_transient(
                    f"OpenCode model error: {str(info['error'])[:140]}")
            text = _part_text(payload.get("parts"))
            if not text:
                await _drop_session(client, sid, headers)
                raise provider_transient("OpenCode returned no text")
            tokens = info.get("tokens") or {}
            # Provenance is taken from the server, never from the request. A
            # silent fallback is reported, not hidden and not thrown away:
            # dropping the answer would cost real coverage, and labelling it
            # with the requested model would make the fan-out agreement score
            # a fiction (one model asked four times looks like 4/4 consensus).
            served = info.get("modelID") or model
            await _release_session(client, sid, headers)
            return {"text": text,
                    "model": served,
                    "model_requested": model,
                    "model_mismatch": bool(served and served != model),
                    "provider": info.get("providerID") or provider,
                    "cost": info.get("cost"),
                    "tokens": {"input": tokens.get("input", 0),
                               "output": tokens.get("output", 0),
                               "reasoning": tokens.get("reasoning", 0)},
                    "latency_ms": int((time.perf_counter() - started) * 1000)}
    except asyncio.CancelledError:
        raise
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 (anything ambiguous fails soft)
        raise provider_transient(f"OpenCode error: {str(e)[:150]}")


async def available() -> dict[str, Any]:
    """Cheap health probe for the model endpoint. Never raises.

    v2 is asked first (`GET /api/info`), because a v2 server answers `GET
    /session` with its web UI. Both wires are supported, so the point of the
    probe is reachability, not a version judgement.
    """
    out: dict[str, Any] = {"reachable": False, "base_url": _base(),
                           "enabled": bool(settings.OPENCODE_ENABLED),
                           "error": ""}
    if not out["enabled"]:
        out["error"] = "disabled"
        return out
    if not _base():
        out["error"] = "unconfigured"
        return out
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{_base()}/api/info", headers=_headers())
            out["http_status"] = r.status_code
            if r.status_code == 200 and _is_json(r):
                try:
                    info = r.json() or {}
                except Exception:  # noqa: BLE001 (reachable is already true)
                    info = {}
                out["reachable"] = True
                out["wire"] = "v2"
                out["version"] = str(info.get("version") or "")
                _wire_cache[_base()] = "v2"
                return out
            if r.status_code in (401, 403):
                out["auth_failed"] = True
                out["error"] = (
                    f"the server rejected this process's credentials (HTTP "
                    f"{r.status_code}). Its password does not match "
                    f"OPENCODE_PASSWORD in .env — restart it with "
                    f"scripts\\llm.ps1 so both read the same value.")
                return out
            r = await client.get(f"{_base()}/session", headers=_headers())
        out["http_status"] = r.status_code
        # 401/403 is not "reachable". It is the server up and refusing us, and
        # calling it reachable is what let a run spend twelve fetches and three
        # minutes before discovering that every extract would 401 — the health
        # check had already reported green. A server that answers 401 to a
        # session listing cannot serve a single extraction.
        ctype = str((getattr(r, "headers", {}) or {}).get("content-type", "") or "").lower()
        if r.status_code in (401, 403):
            out["reachable"] = False
            out["auth_failed"] = True
            out["error"] = (
                f"the server rejected this process's credentials (HTTP "
                f"{r.status_code}). Its password does not match OPENCODE_PASSWORD "
                f"in .env — restart it with scripts\\llm.ps1 so both read the same "
                f"value.")
        elif ctype and "json" not in ctype:
            # Neither wire answered. Measured against opencode 2.2.0 without
            # credentials: the web UI is served for any unknown path, so a 200
            # text/html here is a server whose API this client cannot address,
            # not a healthy reader.
            out["reachable"] = False
            out["wire_mismatch"] = True
            out["error"] = (
                f"GET /session answered {ctype} (HTTP {r.status_code}) and "
                f"GET /api/info was not JSON, so neither the v1 session API nor "
                f"the v2 API is answering. Point OPENCODE_BASE_URL at an "
                f"OpenCode server and check OPENCODE_PASSWORD.")
        else:
            out["reachable"] = r.status_code < 500
            out["wire"] = "v1"
            if not out["reachable"]:
                out["error"] = f"HTTP {r.status_code}"
            else:
                _wire_cache[_base()] = "v1"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    return out


def explain_unusable(state: dict[str, Any]) -> str:
    """One sentence naming what is wrong and how to fix it.

    Split out of `preflight` so `scripts/preflight.py` can print the same text a
    run would refuse with. Two descriptions of the same fault is how they drift.
    """
    error = state.get("error") or "unreachable"
    base = state.get("base_url") or "(unset)"

    if error == "disabled":
        return ("OPENCODE_ENABLED is false, so no local reader is configured; "
                "set it to true and start the server with `opencode serve`")
    if error == "unconfigured":
        return ("OPENCODE_BASE_URL is not set, so there is no server to ask; "
                f"set it (currently {base})")
    if state.get("auth_failed"):
        why = state.get("error") or "the server rejected this process's credentials"
        return f"{why}; check OPENCODE_PASSWORD"
    if state.get("wire_mismatch"):
        return (state.get("error") or
                "the server answered neither the v1 session API nor the v2 API; "
                "point OPENCODE_BASE_URL at an OpenCode server")
    return (f"{state.get('error') or 'unreachable'} at {base}; start it with "
            f"`opencode serve` on {base}, or point OPENCODE_BASE_URL at the "
            f"right address")


async def preflight() -> str | None:
    """Can this process actually extract? Returns a reason, or None if it can.

    Deliberately the same question the first extraction call would ask, asked
    before the run spends anything. Run start-up calls it so an unusable provider
    is a refusal in under a second rather than a completed run with zero records
    three minutes later.

    Under `OPENCODE_STRICT` this provider is the *only* reader, so an unusable
    one is fatal. The normal reason for tolerating an unusable primary — "another
    provider may serve this run" — is exactly what strict mode forbids, so
    tolerating it there produces the failure this function exists to prevent: a
    run that searches, crawls, extracts nothing and reports success. Strict mode
    therefore fails closed on every unusable state, including `disabled` and
    `unconfigured`.
    """
    state = await available()
    if state.get("reachable"):
        return None

    detail = explain_unusable(state)

    if settings.OPENCODE_STRICT:
        return (f"{detail}; OPENCODE_STRICT means there is no fallback provider "
                f"to try after it, so this run cannot extract. Set "
                f"OPENCODE_STRICT=false to let a cloud provider serve it.")

    if state.get("error") in ("disabled", "unconfigured"):
        # A deliberate configuration, and a cloud fallback may serve this run.
        # Refusing every run because the user turned the primary off would be a
        # worse failure than the one this check exists to prevent.
        log.warning("primary reader switched off; a cloud provider will serve the run",
                    extra={"data": {"error": detail}})
        return None

    # Unreachable, or answering with an auth failure, is a fault rather than a
    # choice — and a cloud fallback is only a *fallback* if a cloud key is
    # actually configured. Reported either way, because a run that silently
    # starts paying a different provider is the thing to avoid.
    #
    # Which meant refusing every run without a local `opencode serve`, even with
    # GEMINI_API_KEY and GROQ_API_KEY both set and working -- the plan compiler
    # had just used Gemini successfully in the same process. The check the
    # comment describes was never implemented, so a configured fallback was
    # treated the same as no fallback at all, and the pipeline was unrunnable
    # without a separate local server that nothing documented as a requirement.
    #
    # The distinction is the one the comment already draws. Without any cloud key
    # there is genuinely nothing left to try, and refusing is exactly the failure
    # this function exists to prevent: a run that searches, crawls, extracts
    # nothing and reports success. With one, the run can proceed -- loudly.
    if not (settings.GEMINI_API_KEY or settings.GROQ_API_KEY):
        return detail

    log.warning("primary reader unreachable; a cloud provider will serve the run",
                extra={"data": {"error": detail}})
    return None
