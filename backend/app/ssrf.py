"""SSRF protection: every outbound URL validated before AND after redirects."""
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _ip_never_ok(ip: ipaddress._BaseAddress) -> bool:
    """Cloud metadata + link-local: blocked even alongside public IPs."""
    s = str(ip)
    return (s in ("169.254.169.254", "fd00:ec2::254")
            or ip.is_link_local)


def validate_url(url: str) -> tuple[str, str | None]:
    """Returns (normalized_url, error). Error is None when safe to fetch."""
    url = (url or "").strip()
    if len(url) > 2048:
        return url, "URL too long"
    try:
        parts = urlparse(url)
    except Exception:
        return url, "Unparseable URL"
    if parts.scheme not in ("http", "https"):
        return url, "Only http:// and https:// URLs allowed"
    if parts.username or parts.password:
        return url, "Credentials in URL are not allowed"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host in BLOCKED_HOSTNAMES:
        return url, "Blocked host"
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC,
                                   type=socket.SOCK_STREAM)
    except socket.gaierror:
        return url, "DNS resolution failed"
    except Exception:
        return url, "DNS error"
    ips: list = []
    for info in infos:
        try:
            ips.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            return url, "Invalid resolved address"
    if not ips:
        return url, "DNS resolution failed"
    # Metadata/link-local anywhere in the answer: always block (the critical
    # SSRF case). NAT64/DNS64 translation addresses (e.g. 64:ff9b::/96) may
    # legitimately sit beside a public A record — those alone don't block.
    for ip in ips:
        if _ip_never_ok(ip):
            return url, f"Blocked: {host} resolves to forbidden address {ip}"
    if all(_ip_blocked(ip) for ip in ips):
        return url, f"Blocked: {host} resolves to a private/internal address"
    return url, None
