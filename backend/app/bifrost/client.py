"""The single model interface. There is no provider SDK anywhere else.

The backend holds exactly one model credential — a Bifrost virtual key — and
speaks exactly one protocol: OpenAI-compatible `POST /v1/chat/completions`.
Bifrost owns provider keys, routing, fallback order, retries and budgets. A
Python class named `Bifrost` that called Groq directly would pass a code review
and fail the product claim, so `tests/verify/test_no_direct_providers.py`
parses this package boundary instead of trusting anyone's memory.

Why the cache lives here rather than in a decorator: a replayed call must be
labelled `snapshot` and carry its **original** timestamp. That is a property of
the call record, and only the code that writes the record knows it.

Failure taxonomy, deliberately small:

* `transient` — retried, and if it still fails the run reports a partial run;
* `fatal` — the gateway refused (bad key, unknown model, exhausted budget);
* `cache_miss` — `CACHE_MODE=read-only` and this prompt was never recorded.

A model failure never becomes a verdict. The caller must abstain.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from app.cache.coordinator import CacheCoordinator
from app.cache.keys import model_kv_key
from app.core.config import settings
from app.core.logging import log
from app.verify.store import VerifyStore, iso, new_id, now, parse_iso
from app.verify.otel import headers_for_outgoing_call

#: Stage -> the model routed for it, when the caller does not name one.
_STAGE_MODEL = {
    "extract_claims": "BIFROST_MODEL_EXTRACTOR",
    "repair_coverage": "BIFROST_MODEL_EXTRACTOR",
    "propose_relationship": "BIFROST_MODEL_JUDGE",
    "replacement_value": "BIFROST_MODEL_JUDGE",
    "plan_research": "BIFROST_MODEL_EXTRACTOR",
    "synthesize_answer": "BIFROST_MODEL_SYNTHESIZER",
    "repair_answer": "BIFROST_MODEL_SYNTHESIZER",
}

Kind = Literal["transient", "fatal", "cache_miss", "budget"]

#: Characters per token for the pacing estimate. Four is the usual English
#: average for this family of models; it is deliberately not exact, because the
#: number exists to stop the limiter believing a 2,700-token call costs 500.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(system: str, prompt: str, max_tokens: int) -> int:
    """What this call will cost the provider's per-minute budget.

    Counting only `max_tokens // 2` made the limiter blind: it recorded ~500
    tokens for a call the provider billed at 2,671 input, so the window never
    filled, the header check never tripped, and the run walked straight into a
    429 that a 25-second pause would have avoided.
    """
    return max(
        200,
        (len(system) + len(prompt)) // _CHARS_PER_TOKEN + int(max_tokens),
    )


class BifrostError(RuntimeError):
    def __init__(self, kind: Kind, message: str, *, status: int | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.detail = detail or {}

    @property
    def retryable(self) -> bool:
        return self.kind == "transient"


@dataclass(frozen=True)
class BifrostResult:
    text: str
    provider: str
    model: str
    request_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    fallback_attempts: int
    #: `live` — this process called the gateway. `snapshot` — replayed from
    #: the cache, and `created_at` is when it was originally obtained.
    status: Literal["live", "snapshot"]
    created_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)
    #: Where `cost_usd` came from. `gateway` - the completion reported it.
    #: `catalogue` - the gateway reported no cost, so the call was priced from
    #: the prices the gateway publishes in `/v1/models`. `none` - unpriced.
    #: Never conflate the first two: an estimate is not a bill.
    cost_source: str = "gateway"

    def json(self) -> Any:
        from app.schemas.evidence import parse_llm_json
        return parse_llm_json(self.text)


def prompt_hash(system: str, prompt: str, model: str, stage: str,
                temperature: float, max_tokens: int) -> str:
    blob = "\x00".join([system or "", prompt or "", model, stage,
                        f"{temperature:.3f}", str(max_tokens)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _header(headers: httpx.Headers, *names: str) -> str:
    for name in names:
        value = headers.get(name)
        if value:
            return str(value)
    return ""


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_number(*values: Any) -> float | None:
    """The first value that is actually a number, or None.

    `_num` maps a missing value to 0.0, which is indistinguishable from a caller
    that reported a real zero. That difference matters for cost: a gateway that
    says `cost: null` has not said the call was free, and the old `a or b or c`
    chain silently turned "unpriced" into "$0.00".
    """
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _retry_delay(exc: BifrostError, attempt: int, cap: float) -> float:
    """How long to wait before retrying a transient gateway failure.

    A rate limit states its own window in `Retry-After`. Retrying after 0.4 s
    spends every retry inside the same exhausted minute and converts a temporary
    refusal into a `run.failed`, which is what happened to the first live gold
    run. The requested delay is honoured up to `cap`: a verifier may wait for a
    provider, but it must not hang on one that asks for an hour.
    """
    raw = (exc.detail or {}).get("retry_after")
    try:
        asked = float(str(raw))
    except (TypeError, ValueError):
        asked = 0.0
    try:
        from app.bifrost.scheduler import scheduler
        return scheduler.retry_policy.calculate_delay(attempt, retry_after=asked, cap=cap)
    except Exception:
        delay = 0.4 * (2 ** attempt)
        if asked > 0:
            delay = max(delay, min(asked, cap))
        return delay


class BifrostClient:
    """OpenAI-compatible gateway client with a durable, labelled cache."""

    def __init__(self, store: VerifyStore, run_id: str | None = None, *,
                 base_url: str | None = None, virtual_key: str | None = None,
                 timeout: int | None = None, retries: int | None = None,
                 budget_usd: float | None = None,
                 cache_mode: str | None = None) -> None:
        self.store = store
        self.run_id = run_id
        self.base_url = (base_url or settings.BIFROST_BASE_URL).rstrip("/")
        self.virtual_key = virtual_key if virtual_key is not None \
            else settings.BIFROST_VIRTUAL_KEY
        self.timeout = timeout or settings.BIFROST_TIMEOUT_S
        self.retries = settings.BIFROST_MAX_RETRIES if retries is None else retries
        self.retry_after_cap = settings.BIFROST_RETRY_AFTER_CAP_S
        self.budget_usd = settings.BIFROST_BUDGET_USD if budget_usd is None \
            else budget_usd
        self.cache_mode = cache_mode or settings.CACHE_MODE
        self.cache = CacheCoordinator(store)
        #: model -> (monotonic fetch time, prompt/completion price per token)
        self._price_cache: dict[str, tuple[float, dict[str, float]]] = {}

    # -- configuration ----------------------------------------------------
    def model_for(self, stage: str) -> str:
        attr = _STAGE_MODEL.get(stage, "BIFROST_MODEL_EXTRACTOR")
        return str(getattr(settings, attr, "") or settings.BIFROST_MODEL_EXTRACTOR)

    def configured(self) -> bool:
        return bool(self.base_url and self.virtual_key)

    def health(self) -> dict[str, Any]:
        """What the gateway says about itself. Never raises: a health check that
        can take the health endpoint down is not a health check."""
        if not self.configured():
            return {"ok": False, "detail": "BIFROST_VIRTUAL_KEY is not set",
                    "base_url": self.base_url}
        # `x-bf-vk` is the documented virtual-key header; `Authorization` is the
        # OpenAI-shaped one. Sending both means a gateway whose key does not use
        # the `sk-bf-` prefix still authenticates: such keys are only read from
        # `x-bf-vk`.
        headers = {"Authorization": f"Bearer {self.virtual_key}",
                   "x-bf-vk": self.virtual_key}
        for path in ("/v1/models", "/health", "/metrics"):
            try:
                with httpx.Client(timeout=5.0) as client:
                    response = client.get(f"{self.base_url}{path}", headers=headers)
                if response.status_code < 400:
                    return {"ok": True, "endpoint": path,
                            "detail": response.text[:400]}
            except httpx.HTTPError as exc:
                return {"ok": False, "detail": str(exc)[:200],
                        "base_url": self.base_url}
        return {"ok": False, "detail": "gateway did not answer a health probe",
                "base_url": self.base_url}

    def catalogue_prices(self, model: str) -> dict[str, float]:
        """What the gateway says one token of `model` costs.

        Mistral completions come back with `cost: null` while the same gateway
        publishes `pricing.prompt` and `pricing.completion` for that model in
        `/v1/models`. Reporting the null as $0.00 told the operator a run was
        free when it had spent about half a cent, so an unpriced completion is
        priced from the catalogue and the row is marked `catalogue` - never
        `gateway`, because an estimate is not a bill.

        Best effort by design: pricing must never be able to fail a completion.
        Cached for an hour per model, so the extra request is one per run.
        """
        cached = self._price_cache.get(model)
        if cached and (time.monotonic() - cached[0]) < 3600:
            return cached[1]
        headers = {"Authorization": f"Bearer {self.virtual_key}",
                   "x-bf-vk": self.virtual_key}
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(f"{self.base_url}/v1/models",
                                      headers=headers)
            if response.status_code >= 400:
                return {}
            for entry in (response.json().get("data") or []):
                if str(entry.get("id")) != model:
                    continue
                pricing = entry.get("pricing") or {}
                prices = {"prompt": _num(pricing.get("prompt")),
                          "completion": _num(pricing.get("completion"))}
                if prices["prompt"] or prices["completion"]:
                    self._price_cache[model] = (time.monotonic(), prices)
                    return prices
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("price catalogue unavailable", extra={
                "data": {"model": model, "error": str(exc)[:160]}})
        return {}

    def diagnostics(self) -> dict[str, Any]:
        """Complete diagnostics including health, rate limiter, circuit breaker, and models."""
        h = self.health()
        from app.bifrost.model_profiles import model_registry
        from app.bifrost.scheduler import scheduler
        sched_stats = scheduler.stats()
        return {
            "health": h,
            "configured": self.configured(),
            "base_url": self.base_url,
            "budget_usd": self.budget_usd,
            "spent_usd": self.spent(),
            "cache_mode": self.cache_mode,
            "models": model_registry.describe_all(),
            "scheduler": sched_stats,
            "ollama_available": False,  # Ollama fallback is not operational on this host
        }

    # -- budget -----------------------------------------------------------
    def spent(self) -> float:
        try:
            return float(self.store.gateway_stats(None).get("cost_usd") or 0.0)
        except Exception:  # noqa: BLE001 — a stats failure must not block a run
            return 0.0

    # -- the call ---------------------------------------------------------
    def complete(self, prompt: str, *, stage: str, system: str = "",
                 model: str | None = None, claim_id: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1400,
                 json_mode: bool = True) -> BifrostResult:
        model = model or self.model_for(stage)
        digest = prompt_hash(system, prompt, model, stage, temperature, max_tokens)
        kv_key = model_kv_key(model, stage, system, prompt, temperature,
                              max_tokens, json_mode)

        # L2: Check CacheCoordinator exact KV cache first
        if self.cache_mode != "off":
            cached = self.cache.get_l2(
                model=model, stage=stage, system=system, user=prompt,
                temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)
            if cached:
                log.info("bifrost l2 cache hit", extra={"data": {
                    "stage": stage, "model": model, "key": kv_key[:16]}})
                return BifrostResult(
                    text=str(cached.get("text") or ""),
                    provider=str(cached.get("provider") or ""),
                    model=str(cached.get("resolved_model")
                          or cached.get("model") or model),
                    request_id=str(cached.get("request_id") or ""),
                    input_tokens=_int(cached.get("input_tokens")),
                    output_tokens=_int(cached.get("output_tokens")),
                    cost_usd=_num(cached.get("cost_usd")),
                    latency_ms=_int(cached.get("latency_ms")),
                    fallback_attempts=_int(cached.get("fallback_attempts")),
                    status="snapshot", created_at=parse_iso(cached.get("created_at")) or now(),
                    raw=cached.get("raw") or {},
                    cost_source=str(cached.get("cost_source") or "gateway"))

        if self.cache_mode != "off":
            cached = self.store.find_model_call(digest, model, stage)
            if cached:
                payload = cached.get("response_json") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                created = parse_iso(cached.get("created_at")) or now()
                log.info("bifrost replay", extra={"data": {
                    "stage": stage, "model": model,
                    "request_id": cached.get("bifrost_request_id")}})
                return BifrostResult(
                    text=str(payload.get("text") or ""),
                    provider=str(cached.get("provider") or ""),
                    model=str(cached.get("resolved_model")
                              or cached.get("model") or model),
                    request_id=str(cached.get("bifrost_request_id") or ""),
                    input_tokens=_int(cached.get("input_tokens")),
                    output_tokens=_int(cached.get("output_tokens")),
                    cost_usd=_num(cached.get("cost_usd")),
                    latency_ms=_int(cached.get("latency_ms")),
                    fallback_attempts=_int(cached.get("fallback_attempts")),
                    status="snapshot", created_at=created, raw=payload.get("raw") or {},
                    cost_source=str(cached.get("cost_source") or "gateway"))
            if self.cache_mode == "read-only":
                raise BifrostError(
                    "cache_miss",
                    f"CACHE_MODE=read-only but no recorded call for stage "
                    f"{stage!r} (model {model!r}). A snapshot run cannot invent "
                    f"a model call.")

        if not self.configured():
            raise BifrostError("fatal",
                               "Bifrost is not configured: BIFROST_BASE_URL and "
                               "BIFROST_VIRTUAL_KEY are required for a live run.")

        if self.budget_usd and self.spent() > self.budget_usd:
            raise BifrostError("budget",
                               f"Bifrost budget of ${self.budget_usd:.2f} is "
                               f"already spent (${self.spent():.2f}).")

        body: dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        last_error: BifrostError | None = None
        for attempt in range(max(0, self.retries) + 1):
            try:
                from app.bifrost.scheduler import scheduler
                result = scheduler.execute(
                    lambda: self._post(body, stage=stage, claim_id=claim_id,
                                       digest=digest, model=model,
                                       started=started, system=system,
                                       prompt=prompt, temperature=temperature,
                                       max_tokens=max_tokens,
                                       json_mode=json_mode),
                    stage=stage,
                    estimated_tokens=_estimate_tokens(system, prompt, max_tokens),
                    timeout=float(self.timeout),
                )
                return result
            except BifrostError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.retries:
                    break
                time.sleep(_retry_delay(exc, attempt, self.retry_after_cap))
        assert last_error is not None
        self._record_failure(last_error, stage=stage, claim_id=claim_id,
                             digest=digest, model=model, latency_ms=int(
                                 (time.perf_counter() - started) * 1000))
        raise last_error

    def _post(self, body: dict[str, Any], *, stage: str, claim_id: str | None,
              digest: str, model: str, started: float, system: str = "",
              prompt: str = "", temperature: float = 0.0,
              max_tokens: int = 1400, json_mode: bool = True) -> BifrostResult:
        headers = {"Authorization": f"Bearer {self.virtual_key}",
                   "Content-Type": "application/json",
                   "x-bf-vk": self.virtual_key}
        # W3C Trace Context: propagate the current run trace to the gateway so a
        # verification appears as one trace across FastAPI, Bifrost and Tavily.
        # Auth/key headers are deliberately not merged into OTLP attributes —
        # only traceparent/tracestate travel here.
        headers.update(headers_for_outgoing_call())
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/v1/chat/completions",
                                       json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise BifrostError("transient", f"Bifrost timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise BifrostError("transient", f"Bifrost transport error: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            from app.bifrost.scheduler import scheduler
            scheduler.limiter.update_from_headers(response.headers)
        except Exception:
            pass

        if response.status_code == 429 or response.status_code >= 500:
            raise BifrostError(
                "transient", f"Bifrost answered {response.status_code}: "
                             f"{response.text[:200]}", status=response.status_code,
                detail={"retry_after": response.headers.get("retry-after")})
        if response.status_code >= 400:
            # A 400 is usually our fault (bad key, unknown model, malformed body)
            # and must not be retried. `json_validate_failed` is the exception:
            # the provider's constrained decoder rejected the *model's own*
            # generation, which is a stochastic failure wearing a 4xx. Measured
            # on Groq: two of thirty propose calls died this way and their sources
            # were discarded, while a second attempt on the same prompt succeeded.
            generation_failed = ("json_validate_failed" in response.text
                                 or "Failed to generate JSON" in response.text)
            raise BifrostError(
                "transient" if generation_failed else "fatal",
                f"Bifrost refused the request ({response.status_code}): "
                f"{response.text[:300]}", status=response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise BifrostError("transient",
                               f"Bifrost returned non-JSON: {response.text[:200]}") from exc

        if payload.get("error"):
            err = payload["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise BifrostError("fatal", f"Bifrost error: {str(message)[:300]}")

        choices = payload.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):  # some gateways return content parts
                text = "".join(str(part.get("text", "")) for part in content
                               if isinstance(part, dict))
            else:
                text = str(content or "")
        if not text.strip():
            raise BifrostError("transient", "Bifrost returned an empty completion")

        usage = payload.get("usage") or {}
        extra = payload.get("extra_fields") or payload.get("extra") or {}
        provider = (str(extra.get("provider") or payload.get("provider")
                        or _header(response.headers, "x-bifrost-provider",
                                   "x-bf-provider"))
                    or (model.split("/")[0] if "/" in model else ""))
        request_id = (str(payload.get("id") or "")
                      or _header(response.headers, "x-bifrost-request-id",
                                 "x-bf-request-id", "x-request-id"))
        input_tokens = _int(usage.get("prompt_tokens"))
        output_tokens = _int(usage.get("completion_tokens"))
        cost = _first_number(extra.get("cost"), usage.get("cost"),
                             _header(response.headers, "x-bifrost-cost"))
        cost_source = "gateway" if cost is not None else "none"
        if cost is None:
            prices = self.catalogue_prices(model)
            if prices:
                cost = (input_tokens * prices["prompt"]
                        + output_tokens * prices["completion"])
                cost_source = "catalogue"
        fallbacks = (_int(extra.get("fallback_index"))
                     or _int(_header(response.headers, "x-bifrost-fallbacks")))

        result = BifrostResult(
            text=text, provider=provider, model=str(payload.get("model") or model),
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost or 0.0), latency_ms=latency_ms,
            fallback_attempts=fallbacks, status="live", created_at=now(),
            raw=payload, cost_source=cost_source)

        # The audit row is not the cache. `CACHE_MODE=off` means "never read,
        # never write the cache"; it does not mean "do not say what you spent".
        # Gating the row on the cache mode was a real defect: with the cache off
        # the budget check (which sums `model_calls`) read zero, and the gateway
        # panel — the surface whose job is to prove the calls happened — showed
        # an empty table while the gateway was answering.
        #
        # The row is written either way. With the cache off it is `cacheable:
        # false`, which is exactly how a failure is stored: an audit fact that
        # can never be replayed, because `find_model_call` only reads cacheable
        # rows.
        self.store.add_model_call({
            "id": new_id(), "run_id": self.run_id, "claim_id": claim_id,
            "stage": stage, "provider": provider,
            # `model` is the key this row can be replayed under: the string the
            # caller asked for. `resolved_model` is what the gateway actually
            # used, which differs whenever routing picked a fallback. Keying on
            # the resolved name made the key depend on a runtime outcome, so an
            # identical request never hit the cache once a fallback had fired.
            "model": model, "resolved_model": result.model,
            "bifrost_request_id": request_id, "prompt_hash": digest,
            "response_json": {"text": text, "raw": payload},
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd, "cost_source": cost_source,
            "latency_ms": latency_ms,
            "fallback_attempts": fallbacks, "status": "ok",
            "cacheable": self.cache_mode != "off",
            "created_at": iso(result.created_at),
        })
        # L2: Write successful response to CacheCoordinator KV cache
        if self.cache_mode != "off":
            self.cache.put_l2(
                model=model, stage=stage, system=system, user=prompt,
                payload={"text": text, "raw": payload, "request_id": request_id,
                         "provider": provider, "resolved_model": result.model,
                         "input_tokens": result.input_tokens,
                         "output_tokens": result.output_tokens,
                         "cost_usd": result.cost_usd, "latency_ms": latency_ms,
                         "fallback_attempts": fallbacks,
                         "cost_source": cost_source,
                          "created_at": iso(result.created_at)},
                temperature=0.0, max_tokens=max_tokens, json_mode=json_mode)
        log.info("bifrost call", extra={"data": {
            "stage": stage, "model": result.model, "provider": provider,
            "request_id": request_id, "latency_ms": latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens, "cost_usd": cost,
            "cost_source": cost_source}})
        # Quota accounting reads these rows through the ledger; without this
        # call the quota rail was a permanently-zero panel.
        from app.governance.quota_ledger import quota_ledger
        quota_ledger.set_store(self.store)
        quota_ledger.record_model_usage(
            result.input_tokens + result.output_tokens, provider=provider)
        return result

    def _record_failure(self, error: BifrostError, *, stage: str,
                        claim_id: str | None, digest: str, model: str,
                        latency_ms: int) -> None:
        try:
            self.store.add_model_call({
                "id": new_id(), "run_id": self.run_id, "claim_id": claim_id,
                "stage": stage, "provider": "", "model": model,
                "bifrost_request_id": "", "prompt_hash": digest,
                "response_json": {"error": str(error), "kind": error.kind,
                                  "status_code": error.status},
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                "latency_ms": latency_ms, "fallback_attempts": 0,
                "status": "error", "cacheable": False,
                "created_at": iso(now()),
            })
        except Exception as exc:  # noqa: BLE001 — never mask the real failure
            log.warning("could not record a failed model call",
                        extra={"data": {"error": str(exc)[:200]}})
