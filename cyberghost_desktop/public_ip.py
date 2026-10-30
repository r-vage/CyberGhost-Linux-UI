"""Small, bounded public IPv4 lookup; addresses are not persisted or logged."""

from dataclasses import dataclass
import ipaddress
import re
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

TRACE_URL = "https://1.1.1.1/cdn-cgi/trace"


@dataclass(frozen=True)
class PublicIP:
    address: str
    country: str = ""

    @property
    def display(self):
        return self.address + (f" · {self.country}" if self.country else "")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(request.full_url, code, "Unexpected redirect from IP check", headers, fp)


def fetch_public_ip() -> PublicIP:
    # Ignore application proxy environment variables: show this computer's
    # network exit, not a separate HTTP proxy's address. TLS remains verified.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(TRACE_URL, headers={"User-Agent": "CyberGhostDesktop/0.1", "Accept": "text/plain"})
    with opener.open(request, timeout=6) as response:
        body = response.read(16385)
    if len(body) > 16384:
        raise ValueError("IP check response was too large")
    fields = dict(line.split("=", 1) for line in body.decode("utf-8").splitlines() if "=" in line)
    address = ipaddress.ip_address(fields.get("ip", ""))
    if address.version != 4 or not address.is_global or address.is_multicast:
        raise ValueError("IP check did not return a public IPv4 address")
    country = fields.get("loc", "")
    if not re.fullmatch(r"[A-Z]{2}", country):
        country = ""
    return PublicIP(str(address), country)
