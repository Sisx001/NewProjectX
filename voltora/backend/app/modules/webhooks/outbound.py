"""Single outbound HTTP path for AI providers, webhooks and plugin endpoints.

Every call goes through `validate_outbound_url` (SSRF) and the installation's network policy; redirects are never
followed (a redirect could point at an internal address after validation).
"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ...config import get_settings
from ...core.errors import AppError
from ...core.security import consteq, hmac_sha256
from ...core.ssrf import validate_outbound_url

SIGNATURE_HEADER = "X-Voltora-Signature"
TIMESTAMP_TOLERANCE = 300


def validate_url(url: str, *, allow_private: bool = False, allowed_hosts: list[str] | None = None,
                 resolve: bool = True) -> str:
    return validate_outbound_url(url, allow_private=allow_private, allowed_hosts=allowed_hosts, resolve=resolve)


def _resolves_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    ips = {i[4][0] for i in infos}
    return bool(ips) and all(ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
                             for ip in ips)


def ensure_network(url: str, *, allow_private: bool = False) -> None:
    """External hosts need VOLTORA_EXTERNAL_NETWORK; private self-hosted endpoints only need the private flag."""
    s = get_settings()
    if s.external_network:
        return
    if allow_private and _resolves_private(urlparse(url).hostname or ""):
        return
    raise AppError("network_disabled",
                   "Outbound network calls are disabled on this installation (set VOLTORA_EXTERNAL_NETWORK=true "
                   "after reviewing provider credentials).", status=409)


@dataclass
class HttpResult:
    status: int
    text: str
    json: object | None
    ms: int


def request(method: str, url: str, *, json_body: object | None = None, content: bytes | None = None,
            headers: dict | None = None, timeout: float = 15.0, allow_private: bool = False,
            allowed_hosts: list[str] | None = None, max_bytes: int = 2_000_000) -> HttpResult:
    validate_url(url, allow_private=allow_private, allowed_hosts=allowed_hosts)
    ensure_network(url, allow_private=allow_private)
    t0 = time.monotonic()
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        r = c.request(method, url, json=json_body if content is None else None, content=content, headers=headers)
    body = r.content[:max_bytes].decode("utf-8", "replace")
    try:
        parsed = r.json() if len(r.content) <= max_bytes else None
    except ValueError:
        parsed = None
    return HttpResult(r.status_code, body, parsed, int((time.monotonic() - t0) * 1000))


# ---------------------------------------------------------------- HMAC signing (webhooks + plugin calls)
def sign(secret: str, body: bytes, ts: int | None = None) -> tuple[str, int]:
    ts = int(time.time()) if ts is None else ts
    return f"t={ts},v1={hmac_sha256(secret, str(ts).encode() + b'.' + body)}", ts


def verify_signature(secret: str, header: str, body: bytes, tolerance: int = TIMESTAMP_TOLERANCE) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    return consteq(hmac_sha256(secret, str(ts).encode() + b"." + body), parts.get("v1", ""))


def scrub(text: str, *secrets: str | None) -> str:
    """Remove secret values from error text (httpx errors can echo URLs that embed tokens)."""
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text
