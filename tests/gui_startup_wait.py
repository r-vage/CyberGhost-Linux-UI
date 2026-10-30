"""Reproduce login-before-network ordering with real GTK and fake processes."""

from concurrent.futures import Future
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import CliError, NetworkUnavailable, Status  # noqa: E402
from cyberghost_desktop.startup import DemoStartup, StartupState  # noqa: E402


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def until(condition):
    if condition():
        return
    loop = GLib.MainLoop()
    failure = []

    def check():
        if condition():
            loop.quit()
            return False
        return True

    def timeout():
        failure.append("GTK condition did not complete")
        loop.quit()
        return False

    tick = GLib.timeout_add(5, check)
    deadline = GLib.timeout_add(3000, timeout)
    loop.run()
    if failure:
        GLib.source_remove(tick)
        raise AssertionError(failure[0])
    GLib.source_remove(deadline)


class ControlledExecutor:
    def submit(self, _work):
        future = Future()
        # Simulate an already-running process, whose completion cannot be
        # cancelled. UI tokens must discard its eventual stale result.
        future.set_running_or_notify_cancel()
        return future

    def shutdown(self, **_kwargs):
        pass


startup = DemoStartup()
startup.demo = False  # Exercise asynchronous inspection, still no real service.
app = Application(demo=True, tray=False, startup=startup)
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
real_timeout = GLib.timeout_add_seconds
retry_ticks = []


def fast_retry(seconds, callback):
    if seconds != 3:
        return real_timeout(seconds, callback)

    def tick():
        retry_ticks.append(True)
        return callback()

    return GLib.timeout_add(20, tick)


with (patch("cyberghost_desktop.app.ThreadPoolExecutor", return_value=ControlledExecutor()),
      patch.object(GLib, "timeout_add_seconds", side_effect=fast_retry)):
    try:
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        app.activate()
        window = app.window
        assert "locations" not in window._futures, "Wait for initial startup inspection before calling the API"
        window._futures["startup-status"].set_result(StartupState(True, running=True, detail="Connecting…"))
        window._futures["status"].set_result(Status("disconnected", "No VPN connections found."))
        until(lambda: len(retry_ticks) >= 2)
        assert "locations" not in window._futures, "Do not load the catalog while the boot service is connecting"
        assert window.state.state == "disconnected", "Read-only connection status remains available"

        window.refresh_startup()
        window._futures["startup-status"].set_result(StartupState(True, detail="Startup VPN connected."))
        until(lambda: "locations" in window._futures)
        trace = "Traceback: socket.gaierror: DNS not ready"
        for _ in range(2):
            previous = window._futures["locations"]
            previous.set_exception(NetworkUnavailable("CyberGhost DNS is unavailable.", trace))
            pump()
            assert not window.error_box.get_visible(), "Transient startup errors should not flash a traceback"
            until(lambda: window._futures["locations"] is not previous)
        window._futures["locations"].set_result(window.client.locations("traffic"))
        pump()
        assert len(window.locations) == 8 and window._locations_retry is None
        buffer = window.log.get_buffer()
        activity = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert activity.count("Waiting for the network/DNS") == 1
        assert "Traceback" not in activity and "Loaded 8 traffic locations." in activity
        assert not window.client.connected, "Catalog retries must never initiate a VPN connection"

        window.refresh_locations()
        window._futures["locations"].set_exception(CliError("Account setup required"))
        pump()
        assert window.error_box.get_visible() and window._locations_retry is None

        window.error_box.hide()
        with patch("cyberghost_desktop.app.time.monotonic", return_value=0):
            window.refresh_locations()
        with patch("cyberghost_desktop.app.time.monotonic", return_value=181):
            window._futures["locations"].set_exception(NetworkUnavailable("DNS still unavailable", trace))
            pump()
        assert window.error_box.get_visible() and window._locations_retry is None, "Network retries must be bounded"

        # Refresh invalidates a running request even when the replacement is
        # deferred. It must not fill the list with the previous mode's results.
        window.refresh_locations()
        old = window._futures["locations"]
        window._startup_ready = False
        window.mode.set_active_id("streaming")
        old.set_result(window.client.locations("traffic"))
        pump()
        assert not window.locations
        window.refresh_startup()
        window._futures["startup-status"].set_result(StartupState(True))
        until(lambda: window._futures["locations"] is not old)
        window._futures["locations"].set_result(window.client.locations("streaming"))
        pump()
        assert len(window.locations) == 1 and window.locations[0].service

        window.refresh_locations()
        window._futures["locations"].set_exception(NetworkUnavailable("DNS unavailable"))
        pump()
        timer = window._locations_retry
        assert timer is not None
        window.destroy()
        assert GLib.MainContext.default().find_source_by_id(timer) is None
        print("GTK startup checks passed: deferred catalogs, boot progress, DNS recovery, bounded retries, permanent errors, stale requests and shutdown.")
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()
        pump()
