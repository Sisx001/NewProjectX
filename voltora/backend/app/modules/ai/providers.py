"""OpenAI-compatible chat-completions connector.

Presets only pre-fill a documented base URL; nothing is "supported" until the owner's connection test lists models.
Keys are unsealed only inside this module, sent only in the Authorization header, and scrubbed from errors.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import AppError
from ..webhooks.outbound import request, scrub, validate_url
from .models import AiProvider

PRESETS: dict[str, dict] = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "private": False,
               "docs": "https://platform.openai.com/docs/api-reference/chat"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "private": False,
                   "docs": "https://openrouter.ai/docs/api-reference/overview"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "private": False,
                 "docs": "https://api-docs.deepseek.com/"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "private": False,
             "docs": "https://console.groq.com/docs/openai"},
    "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "private": False,
                 "docs": "https://docs.together.ai/docs/openai-api-compatibility"},
    "xai": {"label": "xAI", "base_url": "https://api.x.ai/v1", "private": False, "docs": "https://docs.x.ai/"},
    "gemini": {"label": "Google Gemini (OpenAI-compatible endpoint)",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "private": False,
               "docs": "https://ai.google.dev/gemini-api/docs/openai"},
    "ollama": {"label": "Ollama (self-hosted)", "base_url": "http://127.0.0.1:11434/v1", "private": True,
               "docs": "https://github.com/ollama/ollama/blob/main/docs/openai.md"},
    "llamacpp": {"label": "llama.cpp server (self-hosted)", "base_url": "http://127.0.0.1:8080/v1", "private": True,
                 "docs": "https://github.com/ggml-org/llama.cpp/tree/master/tools/server"},
    "custom": {"label": "Custom OpenAI-compatible endpoint", "base_url": "", "private": False, "docs": ""},
}

_sleep = time.sleep  # patched in tests


def key_ref(provider_id) -> str:
    return f"ai.provider.{provider_id}.api_key"


def allow_private() -> bool:
    return get_settings().ai_allow_private_endpoints


def validate_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    validate_url(url, allow_private=allow_private())
    return url


def api_key(db: Session, p: AiProvider) -> str | None:
    from ..webhooks.service import read_secret

    return read_secret(db, p.store_id, key_ref(p.id))


@dataclass
class ProviderError(Exception):
    code: str
    message: str
    status: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def _headers(key: str | None) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "VOLTORA-AI/1"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _error_from(status: int, data, text: str, key: str | None) -> ProviderError:
    msg = None
    if isinstance(data, dict):
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else data.get("message")
    msg = scrub(str(msg or text[:300] or f"HTTP {status}"), key)
    code = {401: "auth_failed", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(status, "provider_error")
    return ProviderError(code, f"Provider returned HTTP {status}: {msg}", status, status == 429 or status >= 500)


def _call(db: Session, p: AiProvider, method: str, path: str, body: dict | None = None) -> dict:
    key = api_key(db, p)
    try:
        r = request(method, p.base_url + path, json_body=body, headers=_headers(key),
                    timeout=float(p.timeout_seconds), allow_private=allow_private())
    except AppError as exc:
        raise ProviderError(exc.code, scrub(exc.message, key)) from exc
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", f"Provider did not answer within {p.timeout_seconds}s", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("network_error", scrub(f"{type(exc).__name__}: {exc}", key), retryable=True) from exc
    if not 200 <= r.status < 300:
        raise _error_from(r.status, r.json, r.text, key)
    if not isinstance(r.json, dict):
        raise ProviderError("bad_response", "Provider returned a non-JSON response")
    return r.json


def list_models(db: Session, p: AiProvider) -> list[str]:
    data = _call(db, p, "GET", "/models")
    items = data.get("data") or data.get("models") or []
    ids = [str(m.get("id") or m.get("name")) for m in items if isinstance(m, dict) and (m.get("id") or m.get("name"))]
    return sorted(set(ids))[:500]


def chat(db: Session, p: AiProvider, *, model: str, messages: list[dict], tools: list[dict] | None,
         temperature: float, max_tokens: int) -> tuple[dict, list[dict]]:
    """Returns (response, attempt log). Retries retryable errors with backoff."""
    body: dict = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools and p.supports_tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    attempts: list[dict] = []
    last: ProviderError | None = None
    for i in range(max(0, p.max_retries) + 1):
        t0 = time.monotonic()
        try:
            data = _call(db, p, "POST", "/chat/completions", body)
            attempts.append({"model": model, "status": "ok", "ms": int((time.monotonic() - t0) * 1000)})
            if not data.get("choices"):
                raise ProviderError("bad_response", "Provider response has no choices")
            return data, attempts
        except ProviderError as exc:
            attempts.append({"model": model, "status": "error", "code": exc.code, "error": exc.message,
                             "ms": int((time.monotonic() - t0) * 1000)})
            last = exc
            if not exc.retryable or i == p.max_retries:
                break
            _sleep(min(8.0, 0.5 * 2 ** i))
    assert last is not None
    last.attempts = attempts  # type: ignore[attr-defined]
    raise last


def get_provider(db: Session, store_id, provider_id) -> AiProvider | None:
    if provider_id is None:
        return None
    return db.execute(select(AiProvider).where(AiProvider.id == provider_id, AiProvider.store_id == store_id)
                      ).scalar_one_or_none()
