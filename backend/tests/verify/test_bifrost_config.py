"""The fallback route is configuration, so nothing at runtime notices when it is
not there.

Measured on this machine, with the same committed config: a gateway started
while Ollama was down exposed **11** models for the virtual key
(`GET /v1/models`), none of them Ollama; the same config started with Ollama up
exposed **13**, including `ollama/llama3.2:1b`. A wildcard in `allowed_models`
is expanded against the provider's reachable catalogue when the gateway boots,
so `["*"]` alone does not prove the fallback model is reachable - and when it is
not, every Groq rate limit fails the call instead of degrading to the local
model. The run still looks like a normal `partial`: the gateway answered 429 and
nothing said the fallback had silently disappeared.

Listing the fallback model explicitly makes the route independent of the order
in which the processes happen to start. The wildcard cannot be combined with an
explicit entry either: the gateway refuses to boot with `invalid allowed_models:
wildcard '*' cannot be used with other values in the whitelist`, so the list for
a provider is a choice between `["*"]` and names. This test asserts that
property, plus the two ways the file has already broken for real: a fallback
naming a provider with no key, and a UTF-8 BOM (Bifrost refuses to unmarshal
one).
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "bifrost" / "config.json"


def _config() -> dict:
    raw = CONFIG.read_bytes()
    assert raw[:1] == b"{", (
        "bifrost/config.json must start with `{`. A UTF-8 BOM makes Bifrost fail "
        "to boot with `invalid character '\\ufeff' looking for beginning of value`.")
    return json.loads(raw.decode("utf-8"))


def _models(entry: str) -> tuple[str, str]:
    provider, _, model = entry.partition("/")
    assert provider and model, f"fallback {entry!r} is not provider/model"
    return provider, model


def test_every_fallback_model_is_allowed_by_name_not_by_wildcard():
    config = _config()
    keys = config["governance"]["virtual_keys"]
    assert keys, "no virtual key: every call would be refused"
    rules = config["governance"]["routing_rules"]

    checked = 0
    for key in keys:
        allowed = {entry["provider"]: list(entry.get("allowed_models") or [])
                   for entry in key.get("provider_configs") or []}
        for rule in rules:
            for entry in rule.get("fallbacks") or []:
                provider, model = _models(entry)
                assert provider in allowed, (
                    f"rule {rule['id']} falls back to {entry}, but virtual key "
                    f"{key['id']} has no provider_config for {provider!r}")
                assert model in allowed[provider], (
                    f"rule {rule['id']} falls back to {entry}, but virtual key "
                    f"{key['id']} allows {allowed[provider]} for {provider} - a "
                    "wildcard is expanded against the provider catalogue at boot, "
                    "so it does not guarantee this model survives a start where "
                    "the local provider is down")
                checked += 1
    assert checked, "no fallback is configured: a Groq rate limit fails the run"


def test_every_routing_target_has_a_configured_provider():
    config = _config()
    providers = config["providers"]
    for rule in config["governance"]["routing_rules"]:
        for target in rule.get("targets") or []:
            provider = target["provider"]
            assert provider in providers, (
                f"rule {rule['id']} targets {provider!r}, which has no provider "
                "block - the request would fail before reaching a model")
            assert providers[provider].get("keys"), (
                f"provider {provider!r} is targeted but has no key")


def test_every_provider_key_resolves_a_credential_from_the_environment_or_a_local_host():
    # `env.NAME` is read by the gateway from its own environment; a literal is a
    # local provider that needs no credential (the Ollama key is a placeholder
    # by design). Anything else would ship a secret in a public repository.
    config = _config()
    for name, provider in config["providers"].items():
        for key in provider.get("keys") or []:
            value = str(key.get("value") or "")
            assert value, f"provider {name!r} has a key with no value"
            local = (key.get("ollama_key_config") or {}).get("url")
            if value.startswith("env."):
                continue
            assert local, (f"provider {name!r} stores key {key.get('name')!r} as "
                           "a literal; only a local host may do that")
