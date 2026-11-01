# Check the real ping controls with demo data, deferred replies and no network.

from concurrent.futures import Future
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import Status  # noqa: E402
from cyberghost_desktop.ping import PING_UNAVAILABLE, PingError  # noqa: E402
from cyberghost_desktop.startup import StartupState  # noqa: E402
from cyberghost_desktop.vpn_guard import GuardStatus  # noqa: E402


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def until(condition):
    if condition():
        return
    loop = GLib.MainLoop()
    failed = []

    def check():
        if condition():
            loop.quit()
            return False
        return True

    def timeout():
        failed.append(True)
        loop.quit()
        return False

    tick = GLib.timeout_add(10, check)
    deadline = GLib.timeout_add_seconds(6, timeout)
    loop.run()
    if failed:
        GLib.source_remove(tick)
        raise AssertionError("GTK ping test timed out")
    GLib.source_remove(deadline)


class DeferredExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, work):
        future = Future()
        future.set_running_or_notify_cancel()
        self.tasks.append((work, future))
        return future

    def shutdown(self, **_kwargs):
        pass


def external_action(*_args, **_kwargs):
    raise AssertionError("Ping GUI tests must not run commands or contact the network")


with (patch("subprocess.run", side_effect=external_action),
      patch("subprocess.Popen", side_effect=external_action)):
    app = Application(demo=True, tray=False)
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        app.activate()
        window = app.window
        until(lambda: len(window.locations) == 8 and window.state.state == "disconnected"
              and not window.status_pending)
        germany = next(row for row in window.location_list.get_children() if row.location.code == "DE")
        window.location_list.select_row(germany)
        until(lambda: len(window.city.get_model()) == 3)
        window._pool.shutdown(wait=True)
        pump()
        executor = DeferredExecutor()
        window._pool = executor
        assert "ping" not in window._requests, "Never probe automatically"
        assert window.ping_button.get_sensitive()

        def start():
            window.ping_button.clicked()
            assert window._ping_cancel is not None
            assert window.ping_button.get_label() == "Stop ping"
            return window._ping_cancel, executor.tasks[-1][1]

        cancel, task = start()
        assert window.ping_label.get_text() == "Testing…"
        assert executor.tasks[-1][0]() == 24.0, "Demo must use simulated latency"
        task.set_result(24.0)
        pump()
        assert window.ping_label.get_text() == "Demo · 24.0 ms"
        assert window._ping_timer is not None
        count = len(executor.tasks)
        until(lambda: len(executor.tasks) > count)
        assert window._ping_timer is None, "Wait for the reply before scheduling another probe"
        pending = executor.tasks[-1][1]
        window.ping_button.clicked()
        assert cancel.is_set() and window._ping_cancel is None
        assert window.ping_label.get_text() == "Demo · 24.0 ms"
        pending.set_result(999.0)
        pump()
        assert window.ping_label.get_text() == "Demo · 24.0 ms"
        assert window._ping_timer is None

        # A stopped request must not overwrite a restarted test.
        old_cancel, old_task = start()
        window.ping_button.clicked()
        assert window.ping_label.get_text() == "— ms", "Stopping before a result clears Testing"
        new_cancel, new_task = start()
        assert old_cancel.is_set() and not new_cancel.is_set()
        old_task.set_result(900.0)
        pump()
        assert window.ping_label.get_text() == "Testing…"
        new_task.set_result(None)
        pump()
        assert window.ping_label.get_text() == "Demo · No reply"
        assert window._ping_timer is not None, "A missed reply keeps testing"
        window.ping_button.clicked()

        _, task = start()
        task.set_exception(PingError(PING_UNAVAILABLE))
        pump()
        assert window._ping_cancel is None and window._ping_timer is None
        assert window.ping_label.get_text() == "Ping unavailable"
        assert "Install" in window.ping_label.get_tooltip_text()
        assert window.ping_button.get_sensitive(), "Allow retry after fixing the tool"

        # Language changes update both states while preserving the active test.
        cancel, task = start()
        task.set_result(18.5)
        pump()
        for language in app.translator.catalogs:
            window.language.set_active_id(language)
            assert window.ping_button.get_label() == app.translator.text("Stop ping")
            assert "18.5" in window.ping_label.get_text()
            assert window._ping_cancel is cancel and not cancel.is_set()
        window.language.set_active_id("en")
        window.ping_button.clicked()

        # Connection actions, setup, startup and uncertain/protected routes stop probes.
        changes = [
            ("control_busy", True),
            ("setup_dialog", SimpleNamespace(busy=True)),
            ("_startup_ready", False),
            ("startup_state", StartupState(running=True)),
            ("state", Status("unknown", "Checking…")),
            ("guard_state", GuardStatus("on", "Blocked")),
            ("guard_state", GuardStatus("unknown", "Checking firewall…")),
        ]
        for attribute, value in changes:
            cancel, task = start()
            previous = getattr(window, attribute)
            setattr(window, attribute, value)
            window._sync_controls()
            assert cancel.is_set(), attribute
            assert window._ping_cancel is None and window._ping_timer is None, attribute
            assert window.ping_label.get_text() == "— ms", attribute
            assert not window.ping_button.get_sensitive(), attribute
            task.set_result(700.0)
            pump()
            assert window.ping_label.get_text() == "— ms", attribute
            setattr(window, attribute, previous)
            window._sync_controls()

        cancel, task = start()
        window._apply_status(Status("connected", "Demo VPN active"))
        assert cancel.is_set() and window.ping_label.get_text() == "— ms"
        assert window.ping_button.get_sensitive()
        task.set_result(800.0)
        pump()
        assert window.ping_label.get_text() == "— ms"
        window._apply_guard(GuardStatus("on", "VPN only on"))
        assert window.ping_button.get_sensitive(), "Ping through an active VPN with protection enabled"

        # Confirm the requested placement and inspect all translations at minimum size.
        window.resize(900, 780)
        until(lambda: window.get_allocated_width() < 1080)
        for language in app.translator.catalogs:
            window.language.set_active_id(language)
            window.queue_resize()
            drawn = []
            signal = window.connect_after("draw", lambda *_: drawn.append(True) and False)
            window.queue_draw()
            until(lambda: bool(drawn))
            window.disconnect(signal)
            disconnect_x, _ = window.disconnect_button.translate_coordinates(window, 0, 0)
            ping_x, ping_y = window.ping_button.translate_coordinates(window, 0, 0)
            ip_x, ip_y = window.current_ip_label.translate_coordinates(window, 0, 0)
            assert ping_x >= disconnect_x + window.disconnect_button.get_allocated_width(), language
            assert abs(ping_x - ip_x) <= 2 and ping_y > ip_y, language
            label_x, _ = window.ping_label.translate_coordinates(window, 0, 0)
            assert label_x + window.ping_label.get_allocated_width() <= window.get_allocated_width(), language
        window.language.set_active_id("en")
        window.resize(1080, 780)
        _, task = start()
        task.set_result(24.0)
        pump()
        destination = os.environ.get("CYBERGHOST_PING_SCREENSHOT")
        if destination:
            import cairo
            drawn = []
            signal = window.connect_after("draw", lambda *_: drawn.append(True) and False)
            window.queue_draw()
            until(lambda: bool(drawn))
            window.disconnect(signal)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, window.get_allocated_width(), window.get_allocated_height())
            window.draw(cairo.Context(surface))
            surface.write_to_png(destination)

        # Closing to the tray also stops traffic, retaining the last result.
        cancel = window._ping_cancel
        app.tray = object()
        assert window._delete() is True
        assert cancel.is_set() and window._ping_timer is None
        app.tray = None
        app.activate()
        cancel, task = start()
        window.destroy()
        assert cancel.is_set() and window._ping_timer is None
        task.set_result(600.0)
        pump()
        assert not window.alive
        print("GTK ping checks passed: start/stop, repetition, stale replies, routing states, translations and layout")
    finally:
        if app.window.alive:
            app.window.destroy()
        app.quit()
