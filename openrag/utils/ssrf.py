"""SSRF guards for outbound fetches of URLs from documents or users.

is_blocked_url_literal is a no-DNS pre-check (scheme + literal IP). guard_request
is an httpx hook that resolves the host and rejects non-global addresses, on the
first request and on each redirect.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from utils.logger import get_logger

logger = get_logger()


def is_blocked_url_literal(url: str) -> bool:
    """Reject non-HTTP(S) schemes and non-global literal IPs (no DNS lookup).

    Hostnames pass here and are checked against their resolved IPs in guard_request.
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
    """httpx hook: block requests whose host resolves to a non-global IP."""
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
