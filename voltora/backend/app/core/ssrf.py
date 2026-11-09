"""Outbound URL validation (SSRF defence) for AI endpoints, webhooks and plugin hosts."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .errors import AppError

BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "169.254.169.254"}


def _is_private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified


def validate_outbound_url(url: str, *, allow_private: bool = False, allowed_hosts: list[str] | None = None,
                          resolve: bool = True) -> str:
    p = urlparse(url)
    if p.scheme not in ("https", "http"):
        raise AppError("unsafe_url", "Only http(s) URLs are allowed")
    host = (p.hostname or "").lower()
    if not host:
        raise AppError("unsafe_url", "URL has no host")
    if host in BLOCKED_HOSTS:
        raise AppError("unsafe_url", "Cloud metadata endpoints are blocked")
    if allowed_hosts is not None and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise AppError("unsafe_url", f"Host {host} is not in the approved host list")
    if p.scheme == "http" and not allow_private:
        raise AppError("unsafe_url", "Use https for external endpoints")
    if p.username or p.password:
        raise AppError("unsafe_url", "Credentials in URLs are not allowed")
    if resolve:
        try:
            infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise AppError("unsafe_url", f"Host {host} cannot be resolved") from exc
        for info in infos:
            ip = info[4][0]
            if ipaddress.ip_address(ip) in ipaddress.ip_network("169.254.0.0/16"):
                raise AppError("unsafe_url", "Link-local destinations are blocked")
            if _is_private(ip) and not allow_private:
                raise AppError("unsafe_url",
                               "Private-network destinations need the self-hosted endpoint permission "
                               "(VOLTORA_AI_ALLOW_PRIVATE_ENDPOINTS)")
    return url
