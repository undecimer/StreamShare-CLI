"""Outbound fetch guard for user-influenced URLs (SSRF mitigation).

Provider-supplied thumbnails and creator-configured image URLs are attacker-
influenced input: without a guard, a crafted URL could reach the internal
`pelican_nw` bridge, cloud metadata endpoints, or an arbitrary local address.

`guarded_get` enforces:
  - http/https scheme only
  - every resolved host IP must be globally routable (blocks private,
    loopback, link-local, CGNAT/Tailnet, multicast, reserved, unspecified,
    and IPv4-mapped IPv6 literals)
  - each redirect hop is re-validated (redirect-based bypass protection)
  - a hard response size cap

Residual: DNS rebinding between the check and the connect remains a narrow
TOCTOU window; pinning the resolved IP would require a custom transport
adapter and is recorded as a hardening follow-up.
"""

import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests

_MAX_REDIRECTS = 3
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not ip.is_global


def _resolved_ips(hostname: str) -> set:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return set()
    return {info[4][0] for info in infos}


def guard_url(url: str) -> str:
    """Validate that `url` is a publicly-routable http(s) fetch target."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme or 'none'}' is not allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    ips = _resolved_ips(host)
    if not ips:
        raise ValueError(f"Host '{host}' does not resolve")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValueError(f"Host '{host}' resolves to a non-public address")
    return url


def guarded_get(url: str, *, timeout: float = 10.0, max_bytes: int = _DEFAULT_MAX_BYTES) -> bytes:
    """Fetch `url` with scheme/DNS validation, bounded redirects, and a size cap."""
    current = guard_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        response = requests.get(current, timeout=timeout, allow_redirects=False, stream=True)
        try:
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect without Location header")
                current = guard_url(urljoin(current, location))
                continue
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Response exceeds maximum size of {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()
    raise ValueError(f"Too many redirects (>{_MAX_REDIRECTS})")
