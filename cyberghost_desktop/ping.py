# A cancellable, bounded ICMP probe using the current IPv4 route.

from concurrent.futures import CancelledError
import math
import os
import re
import shutil
import subprocess
from threading import Event
import time

PING_TARGET = "1.1.1.1"
PING_UNAVAILABLE = "Ping is unavailable. Install the ping utility and try again."
PING_FAILED = "The ping test could not run on this system."
PING_INVALID = "The ping command returned an unexpected response."


class PingError(RuntimeError):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


def measure_ping(cancel: Event) -> float | None:
    # One probe at a time; None means no reply, never a zero-millisecond result.
    if cancel.is_set():
        raise CancelledError()
    binary = shutil.which("ping")
    if binary is None:
        raise PingError(PING_UNAVAILABLE)
    command = [binary, "-n", "-4", "-q", "-c", "1", "-W", "2", "-w", "3", PING_TARGET]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, "LC_ALL": "C"})
    except OSError as error:
        raise PingError(PING_FAILED, str(error)) from error
    deadline = time.monotonic() + 4
    try:
        while True:
            if cancel.is_set():
                raise CancelledError()
            if time.monotonic() >= deadline:
                return None
            try:
                output, errors = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if cancel.is_set():
            raise CancelledError()
        if process.returncode == 1:
            return None
        if process.returncode != 0:
            raise PingError(PING_FAILED, errors.strip()[:500])
        # Read the average from the summary, retaining precision for replies <1 ms.
        match = re.search(r"^(?:rtt|round-trip) [^=\n]+ = [\d.]+/(\d+(?:\.\d+)?)/", output, re.M)
        if match is None or not math.isfinite(value := float(match[1])):
            raise PingError(PING_INVALID)
        return value
    finally:
        # Stop cancels the actual process as well as its UI callback.
        if process.poll() is None:
            process.kill()
        process.communicate()
