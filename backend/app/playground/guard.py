"""Where the playground is allowed to send a request, and where it is not.

The playground takes a base URL from the browser, which makes this backend an
outbound request proxy with a user-supplied destination — a server-side request
forgery surface. And the interesting destinations are not model providers: they
are the cloud metadata service, an admin port on loopback, and anything else
sitting on the private network with no auth in front of it.

So the rules are explicit:

* **https only, unless the host is loopback.** A bearer key over plain http to a
  public host is a credential in cleartext, and the user typed that key here.
* **Metadata addresses are refused outright** — ``169.254.169.254``,
  ``100.100.100.200``, ``metadata.google.internal`` — including when they arrive
  by name.
* **Every resolved address must be global** for a public host. A DNS name that
  resolves to ``10.0.0.5`` is the same request as typing ``10.0.0.5``.
* **Loopback is allowed on a short port list.** Ollama (11434), LM Studio
  (1234), llama.cpp (8080), vLLM (8000) and a spare. Running an open-weight
  model on your own machine is the point of a bring-your-own-key playground;
  refusing it would refuse the case it was built for. A port allowlist keeps the
  hole narrow without pretending it is not there.

Residual risk, stated rather than hidden: resolution happens before the
connection, so a hostile DNS server could answer with a public address here and
a private one to httpx (DNS rebinding). Pinning the validated address would fix
that and would also break every virtual host expecting its own name in the TLS
handshake. This is a local, single-user tool, and the rule is proportionate.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.errors import validation

#: Refused even when the resolver is honest. The first is the IPv4 link-local
#: metadata endpoint, the second Alibaba's, the third Google's name for it.
METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "100.100.100.200",
    "metadata.google.internal",
    "metadata.goog",
})

#: Ports a local model server actually listens on. Everything else on loopback
#: stays unreachable, because "reach anything on localhost" is the SSRF.
LOCAL_PORTS = frozenset({11434, 1234, 8000, 8080, 12345})

#: Shown to a user who hit the loopback rule, so the message is actionable.
_LOCAL_PORT_HINT = ", ".join(str(port) for port in sorted(LOCAL_PORTS))


def _resolve(host: str) -> list[str]:
    """Every address the name stands for. Raises rather than guessing."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise validation(f"Could not resolve “{host}”. Check the base URL.") from exc
    addresses = sorted({
        str(info[4][0]).split("%", 1)[0]  # drop the IPv6 zone id
        for info in infos
        if info[4] and info[4][0]
    })
    if not addresses:
        raise validation(f"Could not resolve “{host}”. Check the base URL.")
    return addresses


def _address(value: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def validate_endpoint(base_url: str, resolve=None) -> tuple[str, bool]:
    """Normalise a chat base URL, or refuse it with a reason a user can act on.

    Returns ``(endpoint, is_local)``. ``endpoint`` has no trailing slash and no
    query string, so the caller can append a path without producing ``//``.

    ``resolve`` is injected by tests. It is resolved inside the call rather than
    bound as a default so patching the module's resolver has an effect — a
    default argument captures the function object at import time, and a test
    that patched it would silently keep talking to real DNS.
    """
    resolve = resolve or _resolve
    raw = str(base_url or "").strip()
    if not raw:
        raise validation("Enter the base URL of an OpenAI-compatible endpoint.")
    if "://" not in raw:
        raise validation(
            "Include the scheme, e.g. https://api.mistral.ai/v1 — a bare host "
            "is ambiguous between http and https."
        )

    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise validation("Only http and https endpoints can be reached.")
    if parsed.username or parsed.password:
        raise validation(
            "Do not put credentials in the URL. Use the API key field — the URL "
            "is echoed back in errors and would leak them."
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise validation("That URL has no host.")
    if host in METADATA_HOSTS:
        raise validation("That host serves instance metadata, not a model.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise validation("That port is not a number.") from exc
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    addresses = resolve(host)
    parsed_ips = [_address(value) for value in addresses]
    if any(ip is None for ip in parsed_ips):
        raise validation(f"“{host}” did not resolve to an IP address.")

    if all(ip.is_loopback for ip in parsed_ips):
        is_local = True
    else:
        is_local = False
        private = [str(ip) for ip in parsed_ips if not ip.is_global]
        if private:
            raise validation(
                f"“{host}” resolves to a private address ({private[0]}). The "
                "playground reaches the public internet or a local model "
                "server, not the private network."
            )

    if is_local:
        if port not in LOCAL_PORTS:
            raise validation(
                f"Loopback endpoints are limited to local model servers "
                f"(ports {_LOCAL_PORT_HINT}). Port {port} is not one of them."
            )
    elif parsed.scheme != "https":
        raise validation(
            "Use https:// for a remote endpoint. A key sent over plain http "
            "travels in cleartext."
        )

    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}", is_local


def redact(text: str, secret: str, limit: int = 400) -> str:
    """Provider error text, with the key removed and the tail cut off.

    Two different jobs that always travel together here. A provider that echoes
    the authorization header back in an error would otherwise print the user's
    key into an HTTP response and into whatever reads it; and an unbounded
    upstream body is not an error message.
    """
    body = str(text or "")
    if secret:
        body = body.replace(secret, "[redacted]")
        # A provider that echoes a truncated key leaves a prefix behind.
        for size in (12, 8, 4):
            if len(secret) > size:
                body = body.replace(secret[:size], "[redacted]")
                break
    body = " ".join(body.split())
    return body[:limit]
