"""Exercise real setup widgets with fake installation state and no privileged calls."""

from concurrent.futures import Future
from dataclasses import replace
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.authorization import AuthorizationStatus  # noqa: E402
from cyberghost_desktop.cli import DemoClient  # noqa: E402
from cyberghost_desktop.preferences import Preferences  # noqa: E402
from cyberghost_desktop.public_ip import PublicIP  # noqa: E402
from cyberghost_desktop.setup_tasks import Installation, SetupError  # noqa: E402
from cyberghost_desktop.vpn_guard import DemoGuard  # noqa: E402
from cyberghost_desktop.startup import DemoStartup  # noqa: E402


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class FakeClient(DemoClient):
    demo = False
    binary = None
    reads = 0

    def locations(self, mode):
        self.reads += 1
        return super().locations(mode)

    def status(self):
        self.reads += 1
        return super().status()

    def connect(self, _selection):
        raise AssertionError("Setup must never connect a VPN")

    def disconnect(self):
        raise AssertionError("Setup must never disconnect a VPN")


class ControlledExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, work):
        future = Future()
        future.set_running_or_notify_cancel()
        self.tasks.append((work, future))
        return future

    def drain(self):
        for _ in range(100):
            waiting = [(work, future) for work, future in self.tasks if not future.done()]
            if not waiting:
                return
            for work, future in waiting:
                future.set_result(work())
            pump()
        raise AssertionError("Setup scheduled too many requests")

    def shutdown(self, **_kwargs):
        for _work, future in self.tasks:
            future.cancel()


state = Installation(None, Path("/home/example/.cyberghost/config.ini"), "missing", "/usr/bin/pkexec",
                     "/usr/bin/sudo", ("/usr/bin/x-terminal-emulator", "-e"))

with (patch("cyberghost_desktop.app.inspect_installation", side_effect=lambda: state),
      patch("cyberghost_desktop.setup_tasks.inspect_installation", side_effect=lambda: state),
      patch("cyberghost_desktop.setup_tasks.terminal_command", return_value=["test-terminal"]) as terminal,
      patch("cyberghost_desktop.setup_tasks.run_in_terminal", return_value=None) as run):
    client = FakeClient()
    app = Application(tray=False, client=client, preferences=Preferences(persistent=False),
                      ip_lookup=lambda: PublicIP("203.0.113.42"), guard=DemoGuard(), startup=DemoStartup())
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        app.activate()
        window = app.window
        pump()
        dialog = window.setup_dialog
        assert dialog is not None, "Missing CLI should open Setup automatically"
        assert client.reads == 0, "Missing CLI must not trigger failed background requests"
        assert not window.connect_button.get_sensitive()
        assert window.status_title.get_text() == "Setup required"
        assert dialog.cli_status.get_text() == "CLI not installed"
        assert not dialog.account_button.get_sensitive()
        assert dialog.install_button.get_sensitive()

        screenshot = os.environ.get("CYBERGHOST_SETUP_SCREENSHOT")
        if screenshot:
            import cairo
            loop = GLib.MainLoop()
            drawn = []

            def on_draw(*_args):
                drawn.append(True)
                loop.quit()
                return False

            signal = dialog.connect_after("draw", on_draw)
            deadline = GLib.timeout_add_seconds(8, lambda: (loop.quit(), False)[1])
            dialog.resize(740, 900)
            dialog.queue_draw()
            loop.run()
            dialog.disconnect(signal)
            if drawn:
                GLib.source_remove(deadline)
            assert drawn, "Setup dialog did not finish rendering"
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, dialog.get_allocated_width(), dialog.get_allocated_height())
            dialog.draw(cairo.Context(surface))
            surface.write_to_png(screenshot)

        window._pool.shutdown(wait=True)
        controlled = ControlledExecutor()
        window._pool = controlled
        # Authorization caching is an explicit setup action, never an
        # automatic installation and never a VPN connection side effect.
        auth_state = AuthorizationStatus('off', 'Ask for each action')
        with (patch('cyberghost_desktop.setup_dialog.authorization.status', side_effect=lambda: auth_state),
              patch('cyberghost_desktop.setup_dialog.authorization.configure', return_value=None) as configure):
            dialog.recheck()
            assert dialog.retention_enable.get_sensitive()
            assert not dialog.retention_disable.get_sensitive()
            dialog.retention_enable.emit('clicked')
            assert dialog.busy and not dialog.close_button.get_sensitive()
            auth_state = AuthorizationStatus('ready', 'Remember briefly')
            controlled.drain()
            configure.assert_called_once_with(True)
            assert not dialog.busy and not dialog.retention_enable.get_sensitive()
            assert dialog.retention_disable.get_sensitive()
            dialog.retention_disable.emit('clicked')
            auth_state = AuthorizationStatus('off', 'Ask for each action')
            controlled.drain()
            assert configure.call_args.args == (False,)
            assert dialog.retention_enable.get_sensitive()
        state = replace(state, cli="/usr/bin/cyberghostvpn")
        dialog.check_button.emit("clicked")
        assert client.binary == state.cli, "CLI must be redetected without an app restart"
        assert dialog.account_button.get_sensitive()
        assert dialog.account_status.get_text() == "Account setup needed"

        dialog.account_button.emit("clicked")
        assert dialog.busy
        assert not dialog.close_button.get_sensitive()
        assert not window.disconnect_button.get_sensitive()
        assert not dialog.check_button.get_sensitive()
        dialog.response(Gtk.ResponseType.CLOSE)
        assert window.setup_dialog is dialog, "Closing must not interrupt interactive setup"
        app.request_quit()
        assert window.alive
        window._control("connect")
        state = replace(state, config_state="present")
        controlled.drain()
        run.assert_called_once_with("account", None)
        assert not dialog.busy
        assert dialog.account_status.get_text() == "Account configuration found"

        # A failed setup action restores usable controls and reports the failure.
        dialog.account_button.emit("clicked")
        controlled.tasks[-1][1].set_exception(SetupError("Authorization cancelled in terminal"))
        pump()
        assert not dialog.busy
        assert dialog.close_button.get_sensitive()
        assert "cancelled" in dialog.notice.get_text()

        dialog.response(Gtk.ResponseType.CLOSE)
        controlled.drain()
        assert window.setup_dialog is None
        assert len(window.locations) == 8
        assert window.state.state == "disconnected"
        row = next(row for row in window.location_list.get_children() if row.location.code == "DE")
        window.location_list.select_row(row)
        controlled.drain()
        assert window.connect_button.get_sensitive()

        # Setup remains available after installation; already configured launches
        # should not force the dialog to open again.
        window.setup_button.emit("clicked")
        assert window.setup_dialog is not None
        window.setup_dialog.response(Gtk.ResponseType.CLOSE)
        controlled.drain()
        window.destroy()
        app.activate()
        pump()
        assert app.window.setup_dialog is None
        terminal.assert_called()
        print("GTK setup checks passed: first launch, missing CLI, recheck, interactive setup guard, failure recovery and existing installation.")
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()
        pump()
