"""The playground's outbound call, against a mock transport.

Hermetic: no socket is opened. What is asserted is the part a user would
otherwise have to trust — that the tool loop puts the model's own calls in the
receipt, that an invented function is recorded as a failure instead of dropped,
and that the key cannot come back out in an error message or a JSON body.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.core import config as config_mod
from app.core.errors import AppError
from app.playground import client as playground
from app.playground import guard as playground_guard
from app.playground.schemas import PlaygroundChatIn

KEY = "sk-test-0123456789abcdef"


@pytest.fixture(autouse=True)
def _tavily_key(monkeypatch):
    """The tool loop only exists when search can actually run."""
    monkeypatch.setattr(config_mod.settings, "TAVILY_API_KEY", "tvly-test", raising=False)


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """No test here opens a socket, including the endpoint check."""

    def resolve(host):
        return ["127.0.0.1"] if host in ("localhost", "127.0.0.1") else ["93.184.216.34"]

    monkeypatch.setattr(playground_guard, "_resolve", resolve)


def body(**overrides):
    payload = {
        "base_url": "https://api.example/v1",
        "model": "test-model",
        "api_key": KEY,
        "messages": [{"role": "user", "content": "What does Acme Pro cost now?"}],
    }
    payload.update(overrides)
    return PlaygroundChatIn(**payload)


def completion(content="", *, tool_calls=None, prompt=10, out=20, finish="stop"):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "cmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": out},
    }


def tool_call(name="web_search", arguments=None, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments if arguments is not None else {"query": "acme pro price"}),
        },
    }


def run(responses, *, search=None, payload=None, seen=None):
    """Drive `chat` against a scripted endpoint and return the receipt."""
    queue = list(responses)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if not queue:
            raise AssertionError("more model calls than the test scripted")
        item = queue.pop(0)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)

    result = asyncio.run(
        playground.chat(
            payload or body(),
            transport=httpx.MockTransport(handler),
            search=search,
        )
    )
    if seen is not None:
        seen.extend(requests)
    return result


def test_a_plain_answer_comes_back_with_its_own_usage():
    result = run([completion("Acme Pro is $99 per month.", prompt=120, out=18)])
    assert result["answer"] == "Acme Pro is $99 per month."
    assert result["usage"] == {
        "input_tokens": 120,
        "output_tokens": 18,
        "total_tokens": 138,
    }
    assert result["rounds"] == 1
    assert result["tool_calls"] == []
    assert result["endpoint"] == "https://api.example/v1"


def test_the_generation_time_is_recorded_because_a_scan_needs_it():
    result = run([completion("done")])
    stamp = result["generated_at"]
    assert stamp.endswith("Z")
    assert len(stamp) == 20


def test_the_key_never_appears_in_the_receipt():
    result = run([completion("done")])
    assert KEY not in json.dumps(result)


def test_a_local_endpoint_without_a_key_sends_no_authorization_header():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=completion("ok"))

    payload = body(
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        allow_tools=False,
    )
    asyncio.run(
        playground.chat(payload, transport=httpx.MockTransport(handler), search=None)
    )
    assert "authorization" not in {name.lower() for name in captured[0].headers}


def test_the_tool_call_is_executed_and_recorded_with_what_it_returned():
    seen: list[dict] = []
    searched: list[str] = []

    async def fake_search(query, limit=4, **_):
        searched.append(query)
        return [
            {"title": "Acme pricing", "url": "https://acme.test/pricing", "snippet": "$99"},
            {"title": "Acme docs", "url": "https://acme.test/docs", "snippet": "$89"},
        ][:limit]

    result = run(
        [
            completion("", tool_calls=[tool_call()], finish="tool_calls"),
            completion("Acme Pro is $99 per month.", prompt=200, out=30),
        ],
        search=fake_search,
        seen=seen,
    )

    assert searched == ["acme pro price"]
    assert len(result["tool_calls"]) == 1
    receipt = result["tool_calls"][0]
    assert receipt["name"] == "web_search"
    assert receipt["ok"] is True
    assert receipt["results"] == 2
    assert receipt["urls"] == ["https://acme.test/pricing", "https://acme.test/docs"]
    assert receipt["round"] == 1

    # The second request must carry the model's own call and our result, or the
    # model is being asked to answer from a conversation it never had.
    second = seen[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "web_search",
        "content": second[-1]["content"],
    }
    assert "https://acme.test/pricing" in second[-1]["content"]


def test_usage_accumulates_across_rounds():
    result = run(
        [
            completion("", tool_calls=[tool_call()], prompt=100, out=10, finish="tool_calls"),
            completion("final", prompt=300, out=40),
        ],
        search=lambda query, limit=4, **_: _results(),
    )
    assert result["usage"]["input_tokens"] == 400
    assert result["usage"]["output_tokens"] == 50


async def _results():
    return [{"title": "t", "url": "https://acme.test/a", "snippet": "s"}]


def test_a_function_the_model_invented_is_recorded_as_a_failure():
    result = run(
        [
            completion(
                "",
                tool_calls=[tool_call(name="delete_everything", arguments={"x": 1})],
                finish="tool_calls",
            ),
            completion("I could not check that."),
        ],
        search=lambda query, limit=4, **_: _results(),
    )
    receipt = result["tool_calls"][0]
    assert receipt["name"] == "delete_everything"
    assert receipt["ok"] is False
    assert receipt["error"] == "no such tool"


def test_broken_tool_arguments_are_a_recorded_failure_not_a_crash():
    broken = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "web_search", "arguments": "{not json"},
    }
    result = run(
        [completion("", tool_calls=[broken], finish="tool_calls"), completion("ok")],
        search=lambda query, limit=4, **_: _results(),
    )
    assert result["tool_calls"][0]["ok"] is False
    assert "JSON" in result["tool_calls"][0]["error"]


def test_a_search_failure_is_reported_on_the_receipt_and_the_run_continues():
    async def failing(query, limit=4, **_):
        raise AppError("E_PROVIDER_TRANSIENT", "Tavily timeout", 502)

    result = run(
        [completion("", tool_calls=[tool_call()], finish="tool_calls"), completion("partial")],
        search=failing,
    )
    assert result["tool_calls"][0]["ok"] is False
    assert "timeout" in result["tool_calls"][0]["error"]
    assert result["answer"] == "partial"


def test_the_tool_budget_is_enforced_across_rounds():
    calls = [tool_call(call_id=f"call_{index}") for index in range(3)]
    result = run(
        [
            completion("", tool_calls=calls, finish="tool_calls"),
            completion("done"),
        ],
        search=lambda query, limit=4, **_: _results(),
        payload=body(max_tool_calls=2),
    )
    assert len(result["tool_calls"]) == 2


def test_tools_are_not_offered_when_the_caller_turns_them_off():
    seen: list[dict] = []
    result = run(
        [completion("no tools needed", tool_calls=[tool_call()], finish="tool_calls")],
        payload=body(allow_tools=False),
        seen=seen,
    )
    assert "tools" not in seen[0]
    assert result["tools_enabled"] is False
    assert result["tool_calls"] == []


def test_a_rejected_key_is_reported_without_repeating_it():
    endpoint_echo = httpx.Response(
        401,
        json={"error": {"message": f"Incorrect API key provided: {KEY}"}},
    )
    with pytest.raises(AppError) as caught:
        run([endpoint_echo])
    assert caught.value.code == "E_PROVIDER_FATAL"
    assert "refused that API key" in caught.value.message
    assert KEY not in caught.value.message
    # The page offers a recovery from this, so the reason is a field rather
    # than a sentence it has to pattern-match.
    assert caught.value.details["reason"] == "auth"
    assert caught.value.details["endpoint"] == "https://api.example/v1"
    assert KEY not in json.dumps(caught.value.details)


def test_a_wrong_base_url_says_what_is_probably_wrong():
    with pytest.raises(AppError) as caught:
        run([httpx.Response(404, json={"error": {"message": "not found"}})])
    assert "ends in /v1" in caught.value.message


def test_a_rate_limit_is_transient_so_the_ui_does_not_blame_the_key():
    with pytest.raises(AppError) as caught:
        run([httpx.Response(429, json={"error": {"message": "slow down"}})])
    assert caught.value.code == "E_PROVIDER_TRANSIENT"
    assert "quota" in caught.value.message


def test_an_empty_answer_with_no_tools_is_an_error_not_a_blank_scan():
    with pytest.raises(AppError) as caught:
        run([completion("")])
    assert "empty answer" in caught.value.message


def test_a_non_json_body_means_the_endpoint_is_not_openai_compatible():
    with pytest.raises(AppError) as caught:
        run([httpx.Response(200, text="<html>hello</html>")])
    assert "not an OpenAI-compatible" in caught.value.message


def test_content_returned_as_parts_is_still_read():
    payload = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "part one "}, {"type": "text", "text": "part two"}],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"input_tokens": 5, "output_tokens": 6},
    }
    result = run([payload])
    assert result["answer"] == "part one part two"
    assert result["usage"]["input_tokens"] == 5


def test_a_conversation_must_end_on_the_user_turn():
    with pytest.raises(ValidationError):
        body(
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        )


# --- the gateway source -------------------------------------------------------
#
# The page's default route. The gateway client is replaced here, never hit: a
# test that needed a live Bifrost would be a test nobody runs, and the routing
# facts are the gateway's report, so the interesting assertion is that they are
# carried through unchanged rather than reconstructed from the request.


def gateway_reply(content="", *, tool_calls=None, finish="stop", provider="mistral",
                  resolved="mistral-small-latest", request_id="req-1", cost=None):
    body = {
        "id": request_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, **(
                    {"tool_calls": tool_calls} if tool_calls else {})},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    return {
        "body": body,
        "provider": provider,
        "resolved_model": resolved,
        "request_id": request_id,
        "cost_usd": cost,
        "latency_ms": 12,
    }


def gateway_source(payload=None):
    return PlaygroundChatIn(
        source="gateway",
        model="mistral/mistral-small-latest",
        messages=[{"role": "user", "content": "What does Acme Pro cost now?"}],
        **(payload or {}),
    )


def test_the_gateway_source_needs_no_url_and_no_key():
    # The deployment already holds both. Requiring them anyway would make the
    # default path fail validation on fields it never reads.
    request = gateway_source()
    assert request.base_url == ""
    assert request.api_key == ""


def test_the_endpoint_source_still_needs_a_url():
    with pytest.raises(ValidationError):
        PlaygroundChatIn(
            source="endpoint",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
        )


def test_an_answer_from_the_gateway_reports_the_route_that_served_it(monkeypatch):
    seen: list[dict] = []

    async def fake(payload):
        seen.append(payload)
        return gateway_reply("Acme Pro is $99 per month.", cost=0.0004)

    monkeypatch.setattr(playground, "gateway_completion", fake)
    result = asyncio.run(playground.chat(gateway_source()))

    assert result["answer"] == "Acme Pro is $99 per month."
    assert result["source"] == "gateway"
    # The provider and model are the gateway's report, not the request echoed.
    assert result["provider"] == "mistral"
    assert result["resolved_model"] == "mistral-small-latest"
    assert result["request_id"] == "req-1"
    assert result["cost_usd"] == 0.0004
    assert result["endpoint"].endswith("/v1")
    # Nothing that reached the gateway carried a credential in the payload.
    assert "api_key" not in json.dumps(seen)


def test_the_gateway_tool_loop_is_real_and_its_cost_accumulates(monkeypatch):
    replies = [
        gateway_reply("", tool_calls=[tool_call()], finish="tool_calls",
                      request_id="req-1", cost=0.001),
        gateway_reply("Acme Pro costs $99.", request_id="req-2", cost=0.002),
    ]
    sent: list[dict] = []

    async def fake(payload):
        sent.append(payload)
        return replies.pop(0)

    async def search(query, limit=4, **_):
        return [{"title": "Pricing", "url": "https://acme.example/pricing",
                 "snippet": "Acme Pro is $99."}]

    monkeypatch.setattr(playground, "gateway_completion", fake)
    result = asyncio.run(playground.chat(gateway_source(), search=search))

    assert result["rounds"] == 2
    assert [call["name"] for call in result["tool_calls"]] == ["web_search"]
    assert result["tool_calls"][0]["ok"] is True
    assert result["request_id"] == "req-2", "the receipt names the last call"
    assert result["cost_usd"] == pytest.approx(0.003)
    assert sent[0]["tool_choice"] == "auto"
    assert sent[1]["messages"][-1]["role"] == "tool"


def test_a_gateway_refusal_raises_and_produces_no_answer(monkeypatch):
    async def fake(payload):
        raise AppError("E_PROVIDER_FATAL", "The gateway refused the call (HTTP 401).", 424)

    monkeypatch.setattr(playground, "gateway_completion", fake)
    with pytest.raises(AppError) as caught:
        asyncio.run(playground.chat(gateway_source()))
    assert caught.value.code == "E_PROVIDER_FATAL"
