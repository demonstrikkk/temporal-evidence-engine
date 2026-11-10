"""Prove the fallback is real, and print the evidence.

The product claims every model call goes through Bifrost, and that a provider
failure degrades to the fallback instead of failing the run. Both are easy to
assert and easy to get wrong. This script asks the gateway for a model the
primary provider cannot serve and then reports what actually answered, read from
the gateway's own response headers — not from a log line, and not from our
opinion of the configuration.

    backend\\.venv\\Scripts\\python.exe scripts\\bifrost_fallback_demo.py
    backend\\.venv\\Scripts\\python.exe scripts\\bifrost_fallback_demo.py --model nope/does-not-exist

Exit codes: 0 = a fallback answered, 1 = no fallback (the gateway is up but the
claim is false right now), 2 = the gateway could not be reached at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "groq/definitely-not-a-real-model"
INTERESTING_HEADERS = (
    "x-bifrost-provider",
    "x-bifrost-resolved-model",
    "x-bifrost-original-model",
    "x-bifrost-fallback-index",
    "x-bifrost-routing-info-provider",
    "x-bifrost-routing-info-model",
    "x-bifrost-routing-info-is-fallback",
    "x-bifrost-request-id",
)


def call(base_url: str, key: str, model: str) -> tuple[int, dict, dict]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "max_tokens": 16,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "x-bf-vk": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            headers = {h.lower(): v for h, v in response.getheaders()}
            return response.status, json.loads(response.read() or b"{}"), headers
    except urllib.error.HTTPError as exc:
        headers = {h.lower(): v for h, v in exc.headers.items()}
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {"raw": "<not json>"}
        return exc.code, payload, headers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url",
                        default=os.environ.get("BIFROST_BASE_URL",
                                               "http://127.0.0.1:8080"))
    parser.add_argument("--key",
                        default=os.environ.get("BIFROST_VIRTUAL_KEY",
                                               "sk-bf-tev-local-dev"))
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="A model the primary provider cannot serve")
    args = parser.parse_args()

    status, payload, headers = call(args.base_url, args.key, args.model)
    print(f"requested: {args.model}")
    print(f"http:      {status}")
    for name in INTERESTING_HEADERS:
        if name in headers:
            print(f"{name}: {headers[name]}")

    routing = (payload.get("extra_fields") or {}).get("routing_info") or {}
    if routing:
        print("routing:   " + json.dumps(routing, sort_keys=True))

    attempts = payload.get("extra_fields", {}).get("fallback_attempts") or []
    if attempts:
        print("fallback attempts recorded by the gateway:")
        for attempt in attempts:
            print("  - " + json.dumps(attempt, sort_keys=True))

    answered = status == 200 and bool(payload.get("choices"))
    is_fallback = str(headers.get("x-bifrost-fallback-index", "0")) not in {
        "", "0", "-1"}
    if answered and (is_fallback or attempts):
        provider = headers.get("x-bifrost-provider", "unknown")
        model = headers.get("x-bifrost-resolved-model", "unknown")
        print(f"\nRESULT: answered by the fallback ({provider} / {model}).")
        return 0
    if answered:
        print("\nRESULT: answered by the primary. The fallback was not exercised, "
              "so this run does not prove it.")
        return 1
    print("\nRESULT: no fallback answered. Check GROQ_API_KEY, OLLAMA_BASE_URL "
          "and that the fallback model is pulled.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
