"""Reproduce login-before-network ordering with real GTK and fake processes."""

from concurrent.futures import Future
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import CliError, Location, NetworkUnavailable, Status  # noqa: E402
from cyberghost_desktop.firewall import GuardStatus  # noqa: E402
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
        window._futures["status"].set_result(Status("disconnected", "No VPN connections found."))
        pump()
        assert window.before_ip is None, "Do not capture a baseline before the initial boot-service check finishes"
        window._futures["startup-status"].set_result(StartupState(True, running=True, detail="Connecting…"))
        pump()
        until(lambda: len(retry_ticks) >= 2)
        assert "locations" not in window._futures, "Do not load the catalog while the boot service is connecting"
        assert window.state.state == "disconnected", "Read-only connection status remains available"
        assert window.before_ip is None, "A boot connection must not race a before-VPN IP request"

        window.refresh_status()
        old_status = window._futures["status"]
        app.guard.demo = False  # Still a fake guard; control its reply order.
        window.refresh_guard()
        old_guard = window._futures["guard-status"]
        window.refresh_startup()
        window._futures["startup-status"].set_result(StartupState(True, detail="Startup VPN connected."))
        pump()
        assert window._futures["status"] is not old_status
        assert window._futures["guard-status"] is not old_guard
        old_status.set_result(Status("disconnected", "Snapshot taken before boot VPN connected"))
        old_guard.set_result(GuardStatus("off", "Snapshot taken before protection was enabled"))
        pump()
        assert window.status_pending and window._guard_pending, "Old boot replies must not replace the fresh checks"
        assert not window._futures["status"].done(), "Boot completion must recheck connection status"
        assert window.before_ip is None
        window._futures["status"].set_result(Status("connected", "Demo boot VPN connected"))
        pump()
        assert window.current_ip is None, "Wait for the post-boot firewall check as well as connection status"
        app.guard.demo = True
        app.guard.enabled = True
        window._futures["guard-status"].set_result(app.guard.status())
        until(lambda: "locations" in window._futures)
        assert window.before_ip is None and window.before_ip_label.get_text() == "No baseline recorded"
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

        # A routed tunnel can still have broken DNS/API access. Previously its
        # connected state suppressed the cache, leaving the list empty until
        # VPN-only protection was disabled and a network request succeeded.
        window._apply_status(Status("connected", "Demo tunnel is routed"))
        window._apply_guard(GuardStatus("on", "Demo protection enabled"))
        window.preferences.data["last_location"] = "traffic:DE:"
        window.refresh_locations()
        pending = window._futures["locations"]
        assert len(window.locations) == 8, "Saved destinations must appear while the live catalog is pending"
        assert "saved locations" in window.location_count.get_text()
        assert window.location.code == "DE"
        assert "cities" not in window._futures, "Restoring a saved country must wait for catalog/DNS recovery before fetching cities"
        pending.set_exception(NetworkUnavailable("CyberGhost's server name could not be resolved.", trace))
        pump()
        assert len(window.locations) == 8 and window.location.code == "DE"
        assert window.connect_button.get_sensitive(), "Saved destinations must remain usable with protection on"
        assert not window.error_box.get_visible()
        assert "cities" not in window._futures
        activity = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert activity.count("CyberGhost's server name could not be resolved.") == 1 and "Traceback" not in activity
        until(lambda: window._futures["locations"] is not pending)
        assert window.guard_state.state == "on" and window.state.state == "connected"
        updated = [*window.client.locations("traffic"), Location("Spain", "ES")]
        window._futures["locations"].set_result(updated)
        pump()
        assert len(window.locations) == 9 and window.preferences.offline_locations("traffic") == updated
        assert window.location.code == "DE" and "cities" in window._futures
        window._futures["cities"].set_result(window.client.cities(window._selection()))
        pump()
        assert len(window.city.get_model()) == 3, "Load the deferred cities automatically after catalog recovery"
        window.refresh_locations()
        window.city.set_active_id("Berlin")
        window._futures["locations"].set_result(updated)
        pump()
        assert window.location.code == "DE" and window.city.get_active_id() == "Berlin"
        assert window._locations_retry is None, "Selecting a saved country/city must not invalidate the live refresh"

        # A refresh can remove a destination. Clear its dependent selection
        # and discard any late city reply instead of connecting to stale data.
        france = next(row for row in window.location_list.get_children() if row.location.code == "FR")
        window.location_list.select_row(france)
        cities = window._futures["cities"]
        window.refresh_locations()
        window._futures["locations"].set_result([Location("Spain", "ES")])
        pump()
        cities.set_result(window.client.cities(window._selection()))
        pump()
        assert window.location is None and not window.connect_button.get_sensitive()
        assert len(window.city.get_model()) == 1
        assert len(window.locations) == 1 and window.locations[0].code == "ES"

        window.refresh_locations()
        window._futures["locations"].set_exception(CliError("Account setup required"))
        pump()
        assert window.error_box.get_visible() and window._locations_retry is None
        assert len(window.locations) == 1 and "saved locations" in window.location_count.get_text()

        window.error_box.hide()
        with patch("cyberghost_desktop.app.time.monotonic", return_value=0):
            window.refresh_locations()
        with patch("cyberghost_desktop.app.time.monotonic", return_value=181):
            window._futures["locations"].set_exception(NetworkUnavailable("DNS still unavailable", trace))
            pump()
        assert window.error_box.get_visible() and window._locations_retry is None, "Network retries must be bounded"
        assert len(window.locations) == 1 and "refresh failed" in window.location_count.get_text()

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
        print("GTK startup checks passed: boot/IP ordering, stale boot replies, deferred catalogs, cached destinations with VPN only, selection retention, DNS recovery, bounded retries, permanent errors and shutdown.")
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()
        pump()
