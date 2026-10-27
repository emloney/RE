"""One small interface for every LLM provider.

Groq, OpenAI, Gemini and Ollama all offer an OpenAI-compatible
/chat/completions endpoint, so a single client covers them. Pick one with:

    LLM_PROVIDER=groq     (default)  GROQ_API_KEY=...     LLM_MODEL=openai/gpt-oss-20b
    LLM_PROVIDER=openai              OPENAI_API_KEY=...   LLM_MODEL=<your choice>
    LLM_PROVIDER=gemini              GEMINI_API_KEY=...   LLM_MODEL=<your choice>
    LLM_PROVIDER=ollama              (no key)             LLM_MODEL=<a model you pulled>
                                     OLLAMA_BASE_URL=http://localhost:11434/v1

To add another provider, add an entry to PROVIDERS, or implement the LLM protocol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from internradar.http import FetchError, PoliteClient


class LLM(Protocol):
    @property
    def name(self) -> str:
        """provider:model, stored with each score."""

    def complete(self, system: str, user: str) -> str:
        """Return the model's reply text. Raises LLMError on failure."""


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class Provider:
    base_url: str
    key_env: str | None
    default_model: str | None
    # Ask the API for a JSON object. Off where the provider's docs don't confirm
    # support; the scorer extracts and validates JSON from the text either way.
    json_mode: bool
    min_interval: float  # seconds between calls (stay under rate limits)


PROVIDERS: dict[str, Provider] = {
    # Free tier for openai/gpt-oss-20b: 30 requests/min, 8K tokens/min (checked 2026-10-27).
    "groq": Provider(
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "openai/gpt-oss-20b",
        json_mode=True,
        min_interval=2.0,
    ),
    "openai": Provider(
        "https://api.openai.com/v1", "OPENAI_API_KEY", None, json_mode=True, min_interval=0.5
    ),
    "gemini": Provider(
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        None,
        json_mode=False,
        min_interval=4.0,
    ),
    "ollama": Provider("http://localhost:11434/v1", None, None, json_mode=True, min_interval=0.0),
}


class OpenAICompatibleLLM:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str | None,
        *,
        base_url: str | None = None,
        http: PoliteClient | None = None,
    ) -> None:
        self.provider = PROVIDERS[provider]
        self.provider_name = provider
        self.model = model
        self._url = f"{(base_url or self.provider.base_url).rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # LLM calls are slow; give them a longer timeout than career pages.
        self._http = http or PoliteClient(timeout=60.0, min_interval=self.provider.min_interval)

    @property
    def name(self) -> str:
        return f"{self.provider_name}:{self.model}"

    def complete(self, system: str, user: str) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if self.provider.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._http.post_json(self._url, body, headers=self._headers)
            return data["choices"][0]["message"]["content"] or ""
        except FetchError as exc:  # message has the URL and status only, never the key
            raise LLMError(str(exc)) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape from the LLM API") from exc


def llm_from_env() -> tuple[LLM | None, str]:
    """Build the configured LLM. Returns (llm, explanation) and never raises."""
    provider = (os.environ.get("LLM_PROVIDER") or "groq").strip().lower()
    if provider not in PROVIDERS:
        return None, f"unknown LLM_PROVIDER {provider!r} (use one of {', '.join(PROVIDERS)})"
    spec = PROVIDERS[provider]
    model = (os.environ.get("LLM_MODEL") or "").strip() or spec.default_model
    if not model:
        return None, f"set LLM_MODEL for provider {provider!r}"
    key = os.environ.get(spec.key_env, "").strip() if spec.key_env else None
    if spec.key_env and not key:
        return None, f"{spec.key_env} is not set, so jobs won't be scored"
    base_url = os.environ.get("OLLAMA_BASE_URL") if provider == "ollama" else None
    return OpenAICompatibleLLM(provider, model, key, base_url=base_url), f"{provider}:{model}"
