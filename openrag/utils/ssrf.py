"""Shared SSRF guards for outbound fetches of user/document-controlled URLs.

Two layers, mirroring the websearch content fetcher:

1. ``is_blocked_url_literal`` — a cheap, no-DNS pre-check that rejects
   non-HTTP(S) schemes and any *literal* IP that is not globally routable
   (loopback, private/RFC1918, link-local, reserved, ...).
2. ``guard_request`` — an ``httpx`` request hook that resolves the host and
   rejects the connection if *any* resolved address is non-global. Runs for
   the initial request and every redirect hop, so a public hostname that
   resolves (or redirects) to an internal address is caught before the socket
   is used.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from utils.logger import get_logger

logger = get_logger()


def is_blocked_url_literal(url: str) -> bool:
    """Block obviously-internal targets without a DNS lookup.

    Rejects non-HTTP(S) schemes and any literal IP that is not globally
    routable. Hostnames are passed through here and validated against their
    *resolved* IPs by :func:`guard_request`.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    host = parsed.hostname or ""
    if not host or host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False  # Regular hostname → checked after DNS resolution


async def guard_request(request: httpx.Request) -> None:
    """``httpx`` request event hook: block requests whose host resolves to a
    non-global IP. Runs for the initial request and every redirect hop.
    """
    host = request.url.host
    if not host:
        raise httpx.RequestError("Blocked request with no host", request=request)
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as e:
        raise httpx.RequestError(f"DNS resolution failed for {host}", request=request) from e
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            raise httpx.RequestError(f"Unparseable address for {host}", request=request)
        if not addr.is_global:
            logger.warning("Blocked SSRF attempt to non-global address", host=host, ip=ip)
            raise httpx.RequestError(f"Blocked non-global address {ip} for {host}", request=request)
