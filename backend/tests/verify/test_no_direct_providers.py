"""No module but `app/bifrost` may speak to a model provider.

The product claim is "every model call goes through Bifrost, where routing,
fallback, cost and logging live". That claim survives exactly as long as no
convenience import of `groq` or `openai` appears in the verifier. So the check
parses imports instead of relying on a review comment.

It also asserts the boundary is not vacuous: the gateway client must contain the
one chat-completions URL, and the verifier must actually call it.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "app"

#: Import roots that mean "a provider SDK". `tavily` is here because the
#: research client speaks HTTP directly: the SDK hides `failed_results`, which
#: this product must inspect.
FORBIDDEN = {
    "groq", "openai", "anthropic", "cohere", "mistralai", "ollama",
    "google.genai", "google.generativeai", "google", "tavily", "instructor",
    "litellm", "langchain", "langchain_core", "langchain_groq",
    "langchain_google_genai", "vertexai", "replicate",
}

GUARDED_PACKAGES = ("verify", "research", "api")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}" if node.module
                          else alias.name)
    return found


def _forbidden(module: str) -> str | None:
    for root in FORBIDDEN:
        if module == root or module.startswith(root + "."):
            return root
    return None


def test_guarded_packages_import_no_provider_sdk():
    offenders: list[str] = []
    for package in GUARDED_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            for module in _imports(path):
                root = _forbidden(module)
                if root:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()} "
                                     f"imports {module} (provider root {root!r})")
    assert not offenders, (
        "these imports bypass Bifrost, so provider keys, fallback, cost and "
        "logging would not be recorded:\n  " + "\n  ".join(offenders))


def test_the_bifrost_client_holds_the_only_completion_endpoint():
    hits: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "/v1/chat/completions" in text:
            hits.append(path.relative_to(ROOT).as_posix())
    assert hits == ["bifrost/client.py"], hits


def test_the_verifier_really_calls_the_gateway():
    calls = []
    for path in sorted((ROOT / "verify").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if ".complete(" in text:
            calls.append(path.relative_to(ROOT).as_posix())
    assert calls, "no module under app/verify calls BifrostClient.complete"
    assert "bifrost/client.py" in _imports(ROOT / "verify" / "extract.py") or any(
        "app.bifrost" in module for module in _imports(ROOT / "verify" / "extract.py"))
