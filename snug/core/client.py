"""Who is asking: the client's real IP address and user agent.

Caddy sits in front of Snug and appends the address it received each request
from to X-Forwarded-For. That header is believed only when the request came
straight from a trusted proxy; otherwise anyone could claim any address and
walk round the rate limits, the UK-only check and the audit log.
"""

import ipaddress

from django.conf import settings
from django.http import HttpRequest

MAX_USER_AGENT = 512


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address in network for network in settings.CONFIG.trusted_proxies)


def from_trusted_proxy(request: HttpRequest) -> bool:
    """Whether the request came straight from a trusted proxy, so its forwarded headers count."""
    peer = _parse(request.META.get("REMOTE_ADDR", ""))
    return peer is not None and _trusted(peer)


def client_ip(request: HttpRequest) -> str | None:
    """The client's address, or None if it cannot be determined."""
    peer = _parse(request.META.get("REMOTE_ADDR", ""))
    if peer is None:
        return None
    if not _trusted(peer):
        return str(peer)
    # Walk the forwarded chain from the right, skipping our own proxies. The
    # first address that is not a trusted proxy is the client. Anything to its
    # left was written by the client and cannot be believed.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not forwarded.strip():
        return str(peer)
    for hop in reversed(forwarded.split(",")):
        address = _parse(hop)
        if address is None:
            return None
        if not _trusted(address):
            return str(address)
    return str(peer)


def user_agent(request: HttpRequest) -> str:
    return str(request.META.get("HTTP_USER_AGENT", ""))[:MAX_USER_AGENT]
