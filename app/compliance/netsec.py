"""
Network-security helpers shared by the clients.

Two concerns live here:

  1. `safe_error_message` — turn an arbitrary upstream exception into a short,
     non-leaking string. Raw httpx errors can embed the request URL and (on some
     transports) body fragments; in the provider build that could surface PHI in
     a log line or an HTTP error returned to the caller. Callers should log/raise
     the sanitized form, never the original.

  2. `assert_safe_outbound_url` — block obviously dangerous outbound targets
     (non-HTTP schemes, and hosts that resolve into private / link-local /
     loopback ranges such as the cloud metadata endpoint 169.254.169.254). These
     URLs are operator-configured rather than user-supplied, so this is a
     defense-in-depth guard against misconfiguration or a tampered config store,
     not the primary control.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeOutboundURL(RuntimeError):
    pass


def safe_error_message(exc: Exception, *, context: str) -> str:
    """A stable, non-leaking description of an upstream failure."""
    # Deliberately drop the exception's stringified args (which may contain the
    # URL/body) and keep only the type + a fixed context label.
    return f"{context}: upstream call failed ({exc.__class__.__name__})."


def assert_safe_outbound_url(url: str, *, allow_private: bool = False) -> None:
    """
    Raise UnsafeOutboundURL if `url` is not a plain http(s) URL to a public host.

    Set allow_private=True only for explicitly trusted internal endpoints.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeOutboundURL(f"Refusing non-http(s) URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeOutboundURL("Refusing URL with no host.")
    if allow_private:
        return

    # Resolve and reject private / loopback / link-local / reserved addresses.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # If it doesn't resolve, let the HTTP client fail normally rather than
        # guessing; we only block addresses we can positively classify as unsafe.
        return
    for *_, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeOutboundURL(
                f"Refusing outbound URL resolving to non-public address ({ip})."
            )
