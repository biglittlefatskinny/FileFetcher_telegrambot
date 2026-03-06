"""URL validation and SSRF protection for Open Sneak Bot.

Threat model
------------
An attacker submits a URL that resolves to an internal address (127.x, 10.x,
172.16-31.x, 192.168.x, 169.254.x/link-local, metadata endpoints, etc.) to
probe the VPS's private network or instance metadata services.

Defence layers
--------------
1. Regex: reject anything that doesn't look like http(s)://…
2. Domain allowlist: optional – when set, reject any domain not on the list.
3. DNS pre-flight: resolve the hostname *before* fetching and block every
   returned IP that is private/loopback/link-local/multicast/reserved.
4. Redirect validation: called after aiohttp follows redirects so the *final*
   destination is also checked (prevents open-redirect SSRF).
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from .config import Settings

_URL_RE = re.compile(r"^https?://[^\s]{3,2048}$", re.IGNORECASE)

# Cloud metadata endpoints that aren't covered by standard IP classifications
_METADATA_IPS = frozenset(
    {
        "169.254.169.254",   # AWS / GCP / Azure / DigitalOcean / Linode IMDS
        "100.100.100.200",   # Alibaba Cloud ECS metadata
        "192.0.0.1",         # iOS Captive-portal detection
        "192.0.0.2",
        "fd00:ec2::254",     # AWS IPv6 IMDS
    }
)


class UrlValidationError(ValueError):
    """Raised when a URL fails format, allowlist, or SSRF checks."""


def _ip_is_blocked(ip_str: str) -> bool:
    """Return True if *ip_str* represents an address that must be blocked."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block

    if ip_str in _METADATA_IPS:
        return True

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url(raw_url: str, settings: Settings) -> str:
    """Return the cleaned URL or raise :class:`UrlValidationError`.

    Steps:
      1. Strip & regex-match.
      2. Scheme must be http or https.
      3. Hostname must be present.
      4. Domain allowlist check (when configured).
      5. DNS resolution + SSRF IP check.
    """
    url = raw_url.strip()

    if not _URL_RE.match(url):
        raise UrlValidationError(
            "Please send a complete URL starting with https:// or http://\n"
            "Example: https://www.bbc.com"
        )

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UrlValidationError("Only http:// and https:// URLs are supported.")

    hostname = parsed.hostname
    if not hostname:
        raise UrlValidationError("Invalid URL: hostname is missing.")

    if len(hostname) > 253:
        raise UrlValidationError("Hostname is too long.")

    # ── Domain allowlist ──────────────────────────────────────────────────────
    if settings.domain_allowlist:
        h = hostname.lower()
        if not any(
            h == d or h.endswith("." + d) for d in settings.domain_allowlist
        ):
            raise UrlValidationError(
                "This bot only allows specific domains. "
                "This domain is not on the approved list."
            )

    # ── DNS pre-flight + SSRF check ───────────────────────────────────────────
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrs = socket.getaddrinfo(
            hostname, port, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise UrlValidationError(
            f"Could not resolve '{hostname}'. Check that the domain exists."
        ) from exc

    if not addrs:
        raise UrlValidationError(f"No addresses found for '{hostname}'.")

    for entry in addrs:
        ip_str = entry[4][0]
        if _ip_is_blocked(ip_str):
            raise UrlValidationError(
                "This destination resolves to a private or reserved address "
                "and cannot be accessed."
            )

    return url


def validate_redirect_target(final_url: str, settings: Settings) -> None:
    """SSRF-only check on the final redirect destination.

    The domain allowlist applies only to the URL the user originally
    submitted — not to where that domain chooses to redirect (e.g. GitHub
    releases redirect to Amazon CloudFront CDN, which is legitimate).
    We still block redirects to private/reserved IP addresses.
    """
    url = final_url.strip()
    if not _URL_RE.match(url):
        raise UrlValidationError("Redirect destination is not a valid URL.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UrlValidationError("Redirect destination uses a non-HTTP scheme.")

    hostname = parsed.hostname
    if not hostname:
        raise UrlValidationError("Redirect destination has no hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrs = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UrlValidationError(
            f"Could not resolve redirect destination '{hostname}'."
        )

    for entry in addrs:
        ip_str = entry[4][0]
        if _ip_is_blocked(ip_str):
            raise UrlValidationError(
                "The URL redirected to a private or reserved address."
            )
