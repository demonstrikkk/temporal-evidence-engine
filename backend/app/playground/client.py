"""The playground's two outbound model calls.

**The default source is the gateway.** The page selects a gateway preset that
names no source, and the router routes it through Bifrost with this deployment's
virtual key — the same route a verification takes, so provider keys, fallback,
budgets and logging are the ones that already exist, and the browser is never
handed a credential. A reader who just watched a run succeed is not then asked
for a key the deployment already owns. That call lives in `app.bifrost.client`,
because the one completions URL belongs to that module (invariant 4).

**The second source is an explicit, documented exception to invariant 4.** A
user may bring their own base URL and key for a model this deployment has never
heard of — a local vLLM, an open-weight endpoint, their own gateway. Bifrost
cannot serve that: its virtual keys are the deployment's, and routing elsewhere
would either fail for an unconfigured model or bill the operator for someone
else's play. It is also the API's default when no source is named, because
explicitly spending the operator's money is not something a caller should do by
omission.

What the exception costs, and what is done about it:

* the playground makes **no** verdict and writes **no** verification row, so a
  Bifrost-bypassing call can never produce a status;
* the key is a function argument, never an attribute, never logged, never
  stored, and scrubbed from provider error text before it can reach a response;
* the receipt a caller gets back is the provider's own `usage` block, not an
  estimate presented as a bill — this client cannot know a price it was never
  told, so it does not print one.

The tool loop is real rather than decorative: when the user enables it, the
tools are offered to the model, executed here, and every call is recorded in the
returned receipt, including the ones that failed. A tool the model invented is
recorded as a failure, never silently dropped.
"""
from __future__ import annotations

import json
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from app.bifrost.client import gateway_completion
from app.core.config import settings
from app.core.errors import provider_fatal, provider_transient, validation
from app.core.logging import log
from app.playground.guard import redact, validate_endpoint
from app.playground.schemas import PlaygroundChatIn

#: Appended to the user's base URL. Deliberately not written as a full
#: version-qualified path: the base URL is the user's, and it carries its own
#: version segment (`/v1`, `/openai/v1`, `/v1beta/openai`). Hard-coding the
#: version here would break two of the presets.
_CHAT_PATH = "/chat/completions"

#: One round trip, not the whole loop. A tool-using conversation is several
#: calls and the cap belongs on the count, not on the clock.
_TIMEOUT = httpx.Timeout(90.0, connect=10.0)

_MAX_ANSWER = 200_000
_MAX_TOOL_BODY = 8_000
_MAX_TOOL_RESULTS = 5

_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the live web and return ranked results with a title, a URL "
            "and a short extract. Use it whenever the answer depends on a fact "
            "that could have changed — prices, versions, policies, people in "
            "roles, dates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A specific search query, under 300 characters.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "How many results to return. Default 4.",
                },
            },
            "required": ["query"],
        },
    },
}

#: Injected in tests. Real search is the Tavily adapter, so the playground's
#: research is the same research the verifier does, with the same receipts.
SearchFn = Callable[..., Awaitable[list[dict[str, Any]]]]


async def _tavily(query: str, limit: int = 4, **_: Any) -> list[dict[str, Any]]:
    from app.providers.search import tavily  # local: keeps import cost off startup

    return await tavily.search(query, limit=limit)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _content_text(message: dict[str, Any]) -> str:
    """The assistant text, whatever shape the endpoint used.

    OpenAI returns a string. Several compatible servers return a list of parts,
    and a few return ``None`` beside a tool call. Treating "not a string" as
    "empty answer" would report a working model as broken.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def _usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return int(prompt or 0), int(completion or 0)


def _provider_message(response: httpx.Response) -> str:
    """The provider's own words, when it bothered to explain itself."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or ""
        return str(message)
    return str(payload.get("message") or "")


def _status_error(response: httpx.Response, key: str, endpoint: str):
    status = response.status_code
    detail = redact(_provider_message(response), key)
    suffix = f" Provider said: {detail}" if detail else ""
    # `reason` and `endpoint` are machine-readable so the page can offer the
    # right recovery instead of string-matching an English sentence. The
    # endpoint is already echoed in the 404 message, so nothing new leaks.
    facts = {"status": status, "endpoint": endpoint}
    if status in (401, 403):
        return provider_fatal(
            f"The endpoint refused that API key (HTTP {status}).{suffix}",
            {**facts, "reason": "auth"},
        )
    if status == 404:
        return provider_fatal(
            f"No chat completions path at {endpoint}{_CHAT_PATH} (HTTP 404). "
            "Check the base URL — it usually ends in /v1." + suffix,
            {**facts, "reason": "no_path"},
        )
    if status == 429:
        return provider_transient(
            "The endpoint rate-limited this call or the key is out of quota "
            f"(HTTP 429).{suffix}",
            {**facts, "reason": "rate_limit"},
        )
    if status >= 500:
        return provider_transient(
            f"The endpoint failed (HTTP {status}).{suffix}",
            {**facts, "reason": "upstream"},
        )
    return provider_fatal(
        f"The endpoint rejected the request (HTTP {status}).{suffix}",
        {**facts, "reason": "rejected"},
    )


async def _call(
    client: httpx.AsyncClient,
    endpoint: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = await client.post(
            f"{endpoint}{_CHAT_PATH}", headers=headers, json=payload
        )
    except httpx.TimeoutException as exc:
        raise provider_transient(
            "The endpoint did not answer within 90 seconds."
        ) from exc
    except httpx.HTTPError as exc:
        raise provider_transient(
            f"Could not reach the endpoint: {redact(str(exc), key)}"
        ) from exc

    if response.status_code >= 400:
        raise _status_error(response, key, endpoint)

    try:
        body = response.json()
    except ValueError as exc:
        raise provider_transient(
            "The endpoint answered with something that is not JSON. It is "
            "probably not an OpenAI-compatible API."
        ) from exc
    if not isinstance(body, dict):
        raise provider_transient("The endpoint returned a non-object JSON body.")
    return body


def _first_choice(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise provider_transient(
            "The endpoint returned no choices. That is a protocol error, not an "
            "empty answer."
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise provider_transient("The endpoint returned a malformed choice.")
    return choice


async def _run_tool(
    call: dict[str, Any],
    search: SearchFn,
) -> tuple[dict[str, Any], str]:
    """Execute one tool call and return (receipt, message text for the model)."""
    started = time.monotonic()
    function = call.get("function") if isinstance(call, dict) else None
    name = str((function or {}).get("name") or "")
    raw_arguments = (function or {}).get("arguments")
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except (TypeError, ValueError):
            arguments = None

    receipt: dict[str, Any] = {
        "id": str(call.get("id") or ""),
        "name": name,
        "arguments": arguments if isinstance(arguments, dict) else str(raw_arguments or ""),
        "ok": False,
        "results": 0,
        "urls": [],
        "error": "",
        "latency_ms": 0,
    }

    if not isinstance(arguments, dict):
        receipt["error"] = "arguments were not valid JSON"
        text = json.dumps({"error": "the tool arguments were not valid JSON"})
    elif name != "web_search":
        # A model that invents a function gets told, not crashed at. The
        # receipt keeps the invented name, because "the model wanted something
        # nobody offered" is exactly the kind of thing this panel exists to show.
        receipt["error"] = "no such tool"
        text = json.dumps({"error": f"no tool named {name!r} is available"})
    else:
        query = str(arguments.get("query") or "").strip()[:300]
        try:
            limit = int(arguments.get("max_results") or 4)
        except (TypeError, ValueError):
            limit = 4
        limit = max(1, min(_MAX_TOOL_RESULTS, limit))
        if not query:
            receipt["error"] = "empty query"
            text = json.dumps({"error": "query must not be empty"})
        else:
            try:
                results = await search(query, limit=limit)
            except Exception as exc:  # AppError from the adapter, or a bug
                receipt["error"] = redact(str(getattr(exc, "message", exc)), "", 300)
                text = json.dumps({"error": receipt["error"]})
            else:
                rows = [
                    {
                        "title": str(item.get("title") or "")[:300],
                        "url": str(item.get("url") or ""),
                        "snippet": str(item.get("snippet") or "")[:1_500],
                        "published_date": str(item.get("published_date") or ""),
                    }
                    for item in results[:_MAX_TOOL_RESULTS]
                ]
                receipt["ok"] = True
                receipt["results"] = len(rows)
                receipt["urls"] = [row["url"] for row in rows if row["url"]]
                text = json.dumps({"query": query, "results": rows}, ensure_ascii=False)

    receipt["latency_ms"] = int((time.monotonic() - started) * 1000)
    return receipt, text[:_MAX_TOOL_BODY]


async def chat(
    body: PlaygroundChatIn,
    *,
    transport: httpx.BaseTransport | None = None,
    search: SearchFn | None = None,
) -> dict[str, Any]:
    """Ask a model a question, optionally with web search, and report.

    Two sources, and the difference is whose credential is used:

    * ``gateway`` — this deployment's virtual key, through Bifrost. The same
      route a verification takes, so the provider keys, routing, fallback and
      logging are the ones that already exist. The page selects this by
      default, because a reader who watched a run succeed should not then be
      asked for a key the deployment already has.
    * ``endpoint`` — the user's base URL and key, for a model this deployment
      has never heard of. The documented exception (D-034), and the default
      here so a request that names no source costs the deployment nothing.

    The return value is a receipt, not just an answer: usage, per-call tool
    records, round count and the moment the answer existed. The last field is
    the one the verifier needs — a scan is only meaningful against a generation
    time, and this is the one case where the product knows it exactly.
    """
    search_fn = search or _tavily

    messages: list[dict[str, Any]] = [
        {"role": message.role, "content": message.content} for message in body.messages
    ]
    tools_available = bool(_tavily_enabled()) and body.max_tool_calls > 0
    offer_tools = bool(body.allow_tools) and tools_available

    calls: list[dict[str, Any]] = []
    input_tokens = 0
    output_tokens = 0
    answer = ""
    finish_reason = ""
    rounds = 0
    started = time.monotonic()

    # One extra round than the tool budget: the round that consumes the last
    # result still has to produce prose, and a loop capped at the budget would
    # end with tool output and no answer.
    max_rounds = body.max_tool_calls + 1 if offer_tools else 1

    # Routing facts. For `endpoint` there are none beyond the URL the user
    # typed; for `gateway` these are the gateway's own report of who answered.
    provider = ""
    resolved_model = ""
    request_id = ""
    billed: float | None = None

    async with AsyncExitStack() as stack:
        if body.source == "gateway":
            endpoint = _gateway_base()

            async def send(payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal provider, resolved_model, request_id, billed
                reply = await gateway_completion(payload)
                provider = reply.get("provider") or provider
                resolved_model = reply.get("resolved_model") or resolved_model
                request_id = reply.get("request_id") or request_id
                if reply.get("cost_usd") is not None:
                    billed = (billed or 0.0) + float(reply["cost_usd"])
                return reply["body"]
        else:
            endpoint, _is_local = validate_endpoint(body.base_url)
            http = await stack.enter_async_context(
                httpx.AsyncClient(timeout=_TIMEOUT, transport=transport)
            )

            async def send(payload: dict[str, Any]) -> dict[str, Any]:
                return await _call(http, endpoint, body.api_key, payload)

        while rounds < max_rounds:
            rounds += 1
            payload: dict[str, Any] = {
                "model": body.model,
                "messages": messages,
                "temperature": body.temperature,
                "max_tokens": body.max_output_tokens,
            }
            if offer_tools:
                payload["tools"] = [_TOOL_SPEC]
                payload["tool_choice"] = "auto"

            response = await send(payload)
            choice = _first_choice(response)
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            prompt_tokens, completion_tokens = _usage(response)
            input_tokens += prompt_tokens
            output_tokens += completion_tokens
            finish_reason = str(choice.get("finish_reason") or "")
            answer = _content_text(message)
            tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []

            if not tool_calls:
                break
            if not offer_tools:
                # The model asked for tools nobody offered. Returning the text
                # it did produce is honest; inventing a tool result is not.
                break
            if len(calls) >= body.max_tool_calls:
                break
            if not answer and finish_reason == "tool_calls":
                # Normal: a tool-calling turn usually has no prose.
                pass

            messages.append({
                "role": "assistant",
                "content": answer or None,
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                if len(calls) >= body.max_tool_calls:
                    break
                receipt, text = await _run_tool(call, search_fn)
                receipt["round"] = rounds
                calls.append(receipt)
                messages.append({
                    "role": "tool",
                    "tool_call_id": receipt["id"] or f"call_{len(calls)}",
                    "name": receipt["name"] or "web_search",
                    "content": text,
                })

    latency_ms = int((time.monotonic() - started) * 1000)

    if not answer.strip() and not calls:
        raise provider_transient(
            "The endpoint returned an empty answer. Nothing to scan."
        )

    log.info("playground chat", extra={"data": {
        # Deliberately no key, no base URL auth, no message text.
        "model": body.model,
        "rounds": rounds,
        "tool_calls": len(calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
    }})

    return {
        "answer": answer[:_MAX_ANSWER],
        "model": body.model,
        "source": body.source,
        "endpoint": endpoint,
        # For the gateway these are its report, not a restatement of the
        # request: the provider that served a call and the model that answered
        # are not always what was asked for, and a receipt that echoed the
        # question would be a guess wearing a fact's clothes.
        "provider": provider,
        "resolved_model": resolved_model,
        "request_id": request_id,
        "cost_usd": billed,
        "generated_at": _now_iso(),
        "latency_ms": latency_ms,
        "rounds": rounds,
        "finish_reason": finish_reason,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "tool_calls": calls,
        "tools_enabled": offer_tools,
        "tools_available": tools_available,
        "truncated": len(answer) > _MAX_ANSWER,
    }


def _gateway_base() -> str:
    """Where this deployment's gateway lives, as the receipt reports it.

    The endpoint is not a secret — it is a loopback URL the operator configured
    — and a receipt that said "somewhere" would be unauditable. It is reported,
    never sent back to a client as something to call.
    """
    base = str(settings.BIFROST_BASE_URL or "").rstrip("/")
    if not base:
        raise validation(
            "This deployment has no Bifrost URL configured. Choose another "
            "endpoint and bring a key."
        )
    return f"{base}/v1"


def _tavily_enabled() -> bool:
    """Whether this deployment can actually run the search tool.

    Read at call time, not import time: the key arrives from the environment and
    a module-level snapshot would report the state the process started in.
    """
    from app.core.config import settings

    return bool(str(getattr(settings, "TAVILY_API_KEY", "") or "").strip())
