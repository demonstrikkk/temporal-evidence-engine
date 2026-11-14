"""Endpoints worth offering, so a user does not have to know a base URL.

Every entry is OpenAI-compatible: that is the one wire format this client
speaks, and it is the reason "any model" is a claim the code can keep. The
presets are a convenience, not an allowlist — a custom base URL is a first-class
option, which is what open-weight and self-hosted deployments need.

`key_url` is where a user gets a key, because "paste your key" without saying
where to find one is a dead end in a demo.

Two sources are offered. `gateway` is this deployment's own route through
Bifrost, which needs no key from the reader; every other entry is a bring-your-
own-key `endpoint`. The gateway entry is only offered when the deployment can
actually reach it — a preset that always fails is worse than no preset.
"""
from __future__ import annotations

from app.core.config import settings

_ENDPOINTS: list[dict] = [
    {
        "id": "ollama",
        "label": "Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.2",
        "local": True,
        "key_required": False,
        "note": "Runs on your machine. Start it with `ollama serve`.",
    },
    {
        "id": "lmstudio",
        "label": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "local": True,
        "key_required": False,
        "note": "Load a model and start the local server first.",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "local": False,
        "key_required": True,
        "key_url": "https://platform.openai.com/api-keys",
    },
    {
        "id": "mistral",
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "local": False,
        "key_required": True,
        "key_url": "https://console.mistral.ai/api-keys",
    },
    {
        "id": "groq",
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "local": False,
        "key_required": True,
        "key_url": "https://console.groq.com/keys",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "local": False,
        "key_required": True,
        "key_url": "https://openrouter.ai/keys",
        "note": "One key, most open-weight models.",
    },
    {
        "id": "together",
        "label": "Together",
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "local": False,
        "key_required": True,
        "key_url": "https://api.together.ai/settings/api-keys",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "local": False,
        "key_required": True,
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    {
        "id": "gemini",
        "label": "Gemini (OpenAI compat)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "local": False,
        "key_required": True,
        "key_url": "https://aistudio.google.com/apikey",
    },
    {
        "id": "custom",
        "label": "Custom endpoint",
        "base_url": "",
        "model": "",
        "local": False,
        "key_required": False,
        "note": "Any OpenAI-compatible server: vLLM, llama.cpp, TGI, a gateway.",
    },
]

#: Stamped rather than repeated on every entry: the source is identical for all
#: of them, and twelve copies of the same string is twelve chances to get one
#: wrong.
PRESETS: list[dict] = [{**preset, "source": "endpoint"} for preset in _ENDPOINTS]


def gateway_preset() -> dict | None:
    """This deployment's own route, when it has one to offer.

    The model is the one routing the verifier already uses, so the playground
    demonstrates the same provider that produced the run a reader just watched.
    `key_required` is false because the key is the deployment's and it never
    leaves the server: there is nothing for the reader to paste.
    """
    base = str(settings.BIFROST_BASE_URL or "").strip()
    key = str(settings.BIFROST_VIRTUAL_KEY or "").strip()
    if not base or not key:
        return None
    model = str(settings.BIFROST_MODEL_EXTRACTOR or "").strip()
    if not model:
        return None
    provider = model.split("/", 1)[0] if "/" in model else ""
    label = provider.capitalize() if provider else "This deployment"
    return {
        "id": "gateway",
        "label": f"{label} · this deployment",
        "base_url": "",
        "model": model,
        "provider": provider,
        "local": False,
        "source": "gateway",
        "key_required": False,
        "note": (
            "Routed through the same gateway the verification uses. The key "
            "stays on the server; nothing to paste."
        ),
    }

