# Read the published app version as data; never download or execute update code.

from dataclasses import dataclass
from http.client import HTTPException
import re
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import __version__

REPOSITORY_URL = "https://github.com/r-vage/CyberGhost-Linux-UI"
VERSION_URL = "https://raw.githubusercontent.com/r-vage/CyberGhost-Linux-UI/main/cyberghost_desktop/__init__.py"
UPDATE_GUIDE_URL = REPOSITORY_URL + "#update-move-or-uninstall"
MAX_VERSION_BYTES = 8192
CHECK_TIMEOUT = 8
NETWORK_ERROR = "Could not check for updates. Check your connection and try again."
VERSION_ERROR = "GitHub returned an invalid version. Try again later."


class UpdateError(RuntimeError):
    pass


class NoUpdateRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    state: str


def version_numbers(version):
    if not isinstance(version, str) or len(version) > 64:
        raise ValueError("Expected a stable major.minor.patch version")
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Expected a stable major.minor.patch version")
    return tuple(int(part) for part in version.split("."))


def published_version(body):
    try:
        if len(body) > MAX_VERSION_BYTES:
            raise ValueError("Version response too large")
        # Match one literal assignment. No import, eval or exec of remote data.
        matches = re.findall(r'''^__version__[ \t]*=[ \t]*(["'])([^"'\r\n]+)\1[ \t]*\r?$''',
                             body.decode("utf-8"), re.MULTILINE)
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous version")
        version = matches[0][1]
        version_numbers(version)
        return version
    except (UnicodeError, ValueError):
        raise UpdateError(VERSION_ERROR) from None


def check_for_updates(current_version=__version__):
    current = version_numbers(current_version)
    # Use this computer's current network route, including VPN-only rules,
    # without inheriting a separate proxy or following redirects elsewhere.
    opener = build_opener(ProxyHandler({}), NoUpdateRedirects())
    request = Request(VERSION_URL, headers={
        "User-Agent": f"CyberGhostDesktop/{current_version}",
        "Accept": "text/plain",
        "Cache-Control": "no-cache",
    })
    try:
        with opener.open(request, timeout=CHECK_TIMEOUT) as response:
            body = response.read(MAX_VERSION_BYTES + 1)
    except (OSError, HTTPException):
        raise UpdateError(NETWORK_ERROR) from None
    latest_version = published_version(body)
    latest = version_numbers(latest_version)
    state = "available" if latest > current else "current" if latest == current else "ahead"
    return UpdateInfo(latest_version, state)
