"""The playground is an outbound proxy with a user-supplied destination.

Which means the tests that matter are the refusals: plain http to a public host,
a name that resolves onto the private network, the metadata service, a loopback
port that is not a model server. Each of those is a request this backend would
otherwise make on someone else's behalf, and none of them throws on its own.
"""
from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.playground.guard import redact, validate_endpoint

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731 - a resolver, not a value


def resolver(mapping):
    def resolve(host):
        value = mapping.get(host)
        if value is None:
            raise AppError("E_VALIDATION", f"Could not resolve “{host}”.", 422)
        return value

    return resolve


def test_https_public_endpoint_is_accepted_and_normalised():
    url, local = validate_endpoint("https://api.mistral.ai/v1/", PUBLIC)
    assert url == "https://api.mistral.ai/v1"
    assert local is False


def test_a_path_is_preserved_because_presets_rely_on_it():
    url, _ = validate_endpoint(
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        resolver({"generativelanguage.googleapis.com": ["142.250.185.74"]}),
    )
    assert url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_plain_http_to_a_public_host_is_refused():
    with pytest.raises(AppError) as caught:
        validate_endpoint("http://api.mistral.ai/v1", PUBLIC)
    assert "https" in caught.value.message


def test_a_bare_host_is_refused_rather_than_guessed():
    with pytest.raises(AppError) as caught:
        validate_endpoint("api.mistral.ai/v1", PUBLIC)
    assert "scheme" in caught.value.message


def test_a_name_that_resolves_onto_the_private_network_is_refused():
    with pytest.raises(AppError) as caught:
        validate_endpoint(
            "https://internal.example/v1", resolver({"internal.example": ["10.0.0.5"]})
        )
    assert "private" in caught.value.message


def test_one_private_address_in_a_mixed_answer_is_enough_to_refuse():
    with pytest.raises(AppError):
        validate_endpoint(
            "https://split.example/v1",
            resolver({"split.example": ["93.184.216.34", "192.168.1.10"]}),
        )


def test_the_metadata_service_is_refused_by_name():
    with pytest.raises(AppError) as caught:
        validate_endpoint("http://169.254.169.254/latest", resolver({}))
    assert "metadata" in caught.value.message


def test_a_metadata_hostname_is_refused_before_it_is_resolved():
    called = []

    def explode(host):
        called.append(host)
        return ["93.184.216.34"]

    with pytest.raises(AppError):
        validate_endpoint("http://metadata.google.internal/v1", explode)
    assert called == [], "the refusal must not depend on what DNS says today"


def test_loopback_on_a_model_server_port_is_allowed_over_http():
    url, local = validate_endpoint(
        "http://127.0.0.1:11434/v1",
        resolver({"127.0.0.1": ["127.0.0.1"]}),
    )
    assert url == "http://127.0.0.1:11434/v1"
    assert local is True


def test_loopback_on_another_port_is_refused():
    with pytest.raises(AppError) as caught:
        validate_endpoint(
            "http://127.0.0.1:9200/v1", resolver({"127.0.0.1": ["127.0.0.1"]})
        )
    assert "local model server" in caught.value.message


def test_ipv6_loopback_is_recognised():
    _, local = validate_endpoint("http://[::1]:1234/v1", resolver({"::1": ["::1"]}))
    assert local is True


def test_credentials_in_the_url_are_refused_because_the_url_is_echoed():
    with pytest.raises(AppError) as caught:
        validate_endpoint("https://user:pass@api.example/v1", PUBLIC)
    assert "API key field" in caught.value.message


def test_an_unresolvable_host_says_so_instead_of_failing_at_connect_time():
    with pytest.raises(AppError) as caught:
        validate_endpoint("https://nope.invalid/v1", resolver({}))
    assert "resolve" in caught.value.message


def test_a_non_http_scheme_is_refused():
    for url in ("file:///etc/passwd", "ftp://example.com/v1", "gopher://x/v1"):
        with pytest.raises(AppError):
            validate_endpoint(url, PUBLIC)


def test_a_trailing_slash_never_produces_a_double_slash_path():
    url, _ = validate_endpoint("https://api.example/v1///", PUBLIC)
    assert url == "https://api.example/v1"


def test_redact_removes_the_key_and_its_prefix():
    key = "sk-live-abcdefghijklmnop"
    text = f"invalid key {key} (prefix sk-live-abcd) for request"
    cleaned = redact(text, key)
    assert key not in cleaned
    assert "sk-live-abcd" not in cleaned
    assert "[redacted]" in cleaned


def test_redact_caps_an_unbounded_upstream_body():
    assert len(redact("x" * 5_000, "", limit=400)) == 400
