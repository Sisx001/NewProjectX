"""Shared helpers for AI / extension / webhook / automation tests (no test functions here).

Every outbound call in these tests is intercepted by respx; DNS for the fake hosts is patched so the SSRF guard sees a
public address (or a private one, when a test needs to prove rejection).
"""
from __future__ import annotations

import json
import socket

import pytest

PUBLIC_IP = "93.184.216.34"
PRIVATE_HOSTS = {"internal.example.test": "10.0.0.5", "localhost": "127.0.0.1"}
_real_getaddrinfo = socket.getaddrinfo


def fake_getaddrinfo(host, port, *args, **kwargs):
    if host in PRIVATE_HOSTS:
        ip = PRIVATE_HOSTS[host]
    elif host.replace(".", "").isdigit():
        ip = host
    else:
        ip = PUBLIC_IP
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 443))]


@pytest.fixture
def net(monkeypatch):
    """Outbound network allowed (to respx mocks only) with deterministic DNS."""
    from app.config import get_settings

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(get_settings(), "external_network", True)
    from app.modules.ai import providers

    monkeypatch.setattr(providers, "_sleep", lambda s: None)
    yield


def chat_response(content=None, tool_calls=None, prompt_tokens=100, completion_tokens=20):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": name, "arguments": json.dumps(args)}}
                             for i, (name, args) in enumerate(tool_calls)]
    return {"id": "cmpl-1", "object": "chat.completion", "choices": [{"index": 0, "message": msg,
                                                                      "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


BASE = "https://llm.example.com/v1"


def make_provider(api, key="sk-test-SUPERSECRET-123456", **kw):
    r = api.post("/api/ai/providers", json={"name": "Test LLM", "preset": "custom", "base_url": BASE,
                                            "api_key": key, "default_model": "test-model", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def make_agent(api, provider_id, tools, role="owner_copilot", **kw):
    body = {"name": "Copilot", "role": role, "provider_id": provider_id, "model": "test-model",
            "allowed_tools": tools, "enabled": True, **kw}
    r = api.post("/api/ai/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()
