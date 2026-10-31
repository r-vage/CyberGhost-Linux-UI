# Exercise real GTK update controls with deferred results and no external actions.

from concurrent.futures import Future
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop import __version__  # noqa: E402
from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.self_update import GIT_REQUIRED, LOCAL_CHANGES, InstallError, InstallResult  # noqa: E402
from cyberghost_desktop.updates import NETWORK_ERROR, UPDATE_GUIDE_URL, VERSION_ERROR, UpdateError, UpdateInfo  # noqa: E402


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
    deadline = GLib.timeout_add_seconds(5, timeout)
    loop.run()
    if failed:
        GLib.source_remove(tick)
        raise AssertionError("GTK update test timed out")
    GLib.source_remove(deadline)


class DeferredExecutor:
    def __init__(self):
        self.tasks = []
        self.work = []

    def submit(self, work):
        future = Future()
        future.set_running_or_notify_cancel()
        self.tasks.append(future)
        self.work.append(work)
        return future

    def shutdown(self, **_kwargs):
        pass


def external_action(*_args, **_kwargs):
    raise AssertionError("Update UI tests must not invoke network or system commands")


with (patch("subprocess.run", side_effect=external_action),
      patch("subprocess.Popen", side_effect=external_action),
      patch("cyberghost_desktop.updates.build_opener", side_effect=external_action)):
    app = Application(demo=True, tray=False)
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        app.activate()
        window = app.window
        until(lambda: len(window.locations) == 8 and window.state.state == "disconnected")
        assert __version__ in window.get_titlebar().get_title()
        window._about()
        dialog = window.about_dialog
        assert __version__ in dialog.current_version.get_text()
        assert not dialog.check_button.get_sensitive()
        dialog._check()
        assert "app-update" not in window._requests, "Demo must not check online"
        dialog.destroy()
        assert window.about_dialog is None

        row = next(row for row in window.location_list.get_children() if row.location.code == "DE")
        window.location_list.select_row(row)
        until(lambda: len(window.city.get_model()) == 3)
        window.connect_button.clicked()
        until(lambda: window.state.state == "connected" and not window.control_busy)
        window.guard_toggle.set_active(True)
        until(lambda: window.guard_state.state == "on" and not window.control_busy)

        executor, real_pool = DeferredExecutor(), window._pool
        selection, command = window._selection(), window.command_label.get_text()
        connection, guard, startup = window.state, window.guard_state, window.startup_state
        requests = dict(window._requests)
        try:
            with (patch.object(window, "_pool", executor), patch.object(window.client, "demo", False),
                  patch.object(window, "installation", SimpleNamespace(needs_setup=False)),
                  patch("cyberghost_desktop.about_dialog.checkout_available", return_value=True)):
                window._about()
                dialog = window.about_dialog
                window._about()
                assert window.about_dialog is dialog, "Reuse an already-open dialog"
                assert not executor.tasks, "Opening About must not check automatically"
                assert dialog.update_link.get_uri() == UPDATE_GUIDE_URL
                assert dialog.status.get_text() == "Not checked yet"
                dialog.check_button.clicked()
                assert dialog.busy and not dialog.check_button.get_sensitive()
                assert dialog.status.get_text() == "Checking for updates…"
                dialog._check()
                assert len(executor.tasks) == 1, "Ignore duplicate requests"
                responsive = []
                GLib.idle_add(lambda: responsive.append(True) and False)
                pump()
                assert responsive and dialog.busy, "GTK must keep running while the check is pending"
                executor.tasks[-1].set_result(UpdateInfo("9.0.0", "available"))
                pump()
                assert dialog.status.get_text() == "Update available: 9.0.0."
                assert dialog.latest_version.get_visible() and dialog.check_button.get_sensitive()
                for code, catalog in app.translator.catalogs.items():
                    window.language.set_active_id(code)
                    assert dialog.get_title() == catalog.messages["About and updates"]
                    assert dialog.check_button.get_label() == catalog.messages["Check for updates"]
                    assert dialog.install_button.get_label() == catalog.messages["Install update"]
                    assert dialog.restart_button.get_label() == catalog.messages["Restart app"]
                    assert dialog.status.get_text() == catalog.messages["Update available: {version}."].format(version="9.0.0")
                window.language.set_active_id("en")

                for state, expected in [("current", "You’re up to date."),
                                        ("ahead", "This version is newer than the published version.")]:
                    dialog.check_button.clicked()
                    assert not dialog.latest_version.get_visible()
                    executor.tasks[-1].set_result(UpdateInfo(__version__, state))
                    pump()
                    assert dialog.status.get_text() == expected
                for error in (UpdateError(NETWORK_ERROR), UpdateError(VERSION_ERROR), ValueError("internal detail")):
                    dialog.check_button.clicked()
                    executor.tasks[-1].set_exception(error)
                    pump()
                    assert dialog.status.get_text() == (str(error) if isinstance(error, UpdateError) else NETWORK_ERROR)
                    assert dialog.check_button.get_sensitive() and not dialog.latest_version.get_visible()

                assert window._selection() == selection and window.command_label.get_text() == command
                assert (window.state, window.guard_state, window.startup_state) == (connection, guard, startup)
                assert not window.control_busy and window.client.connected and app.guard.enabled
                assert {key: value for key, value in window._requests.items() if key != "app-update"} == requests

                dialog.check_button.clicked()
                closed_request = executor.tasks[-1]
                dialog.destroy()
                window._about()
                reopened = window.about_dialog
                closed_request.set_result(UpdateInfo("8.0.0", "available"))
                pump()
                assert reopened.status.get_text() == "Not checked yet", "Ignore results for a closed dialog"
                reopened.check_button.clicked()
                executor.tasks[-1].set_result(UpdateInfo("9.0.0", "available"))
                pump()
                dialog = reopened
                assert dialog.install_button.get_visible() and dialog.install_button.get_sensitive()
                dialog.can_install = False
                dialog.sync_actions()
                assert not dialog.install_button.get_sensitive() and dialog.install_notice.get_visible()
                dialog._install()
                assert dialog.status.get_text() == GIT_REQUIRED
                dialog.can_install = True
                window.control_busy = True
                before = len(executor.tasks)
                dialog._install()
                assert len(executor.tasks) == before, "Do not overlap VPN and installation operations"
                window.control_busy = False
                dialog.sync_actions()
                dialog.install_button.clicked()
                assert window.update_busy and window.control_busy and dialog.busy
                assert not dialog.close_button.get_sensitive() and not dialog.check_button.get_sensitive()
                assert not window.disconnect_button.get_sensitive() and not window.guard_toggle.get_sensitive()
                assert not window.language.get_sensitive() and not window.setup_button.get_sensitive()
                assert not window.startup_save.get_sensitive() and not window.refresh_button.get_sensitive()
                assert window._delete(), "Window close must wait for Git to finish"
                app.request_quit()
                dialog.response(Gtk.ResponseType.CLOSE)
                app.request_restart()
                assert window.alive and dialog.alive and not app.restart_requested
                before = len(executor.tasks)
                dialog._install()
                assert len(executor.tasks) == before, "Ignore duplicate installation requests"
                executor.tasks[-1].set_exception(InstallError(LOCAL_CHANGES, "test local edits"))
                pump()
                assert dialog.status.get_text() == LOCAL_CHANGES
                assert not window.update_busy and not window.update_ready and not window.control_busy
                assert dialog.install_button.get_sensitive() and dialog.close_button.get_sensitive()
                assert window.disconnect_button.get_sensitive() and window.guard_toggle.get_sensitive()

                dialog.install_button.clicked()
                with patch("cyberghost_desktop.about_dialog.install_update", return_value=InstallResult("9.0.1")) as install:
                    executor.tasks[-1].set_result(executor.work[-1]())
                    install.assert_called_once_with("9.0.0", locale_directory=app.locale_directory)
                pump()
                assert not window.update_busy and window.update_ready and window.control_busy
                assert dialog.status.get_text() == "Update installed: 9.0.1."
                assert dialog.restart_button.get_visible() and dialog.restart_button.get_sensitive()
                assert not dialog.install_button.get_visible() and not dialog.check_button.get_sensitive()
                assert not window.disconnect_button.get_sensitive() and not window.guard_toggle.get_sensitive()
                assert "Restart" in window.operation_note.get_text()
                assert __version__ in dialog.current_version.get_text(), "Loaded code remains the old version until restart"
                dialog.response(Gtk.ResponseType.CLOSE)
                window._about()
                dialog = window.about_dialog
                assert dialog.status.get_text() == "Update installed: 9.0.1."
                assert dialog.restart_button.get_visible()
                assert (window.state, window.guard_state, window.startup_state) == (connection, guard, startup)
                assert window._selection() == selection and window.command_label.get_text() == command
                assert window.client.connected and app.guard.enabled, "Updating leaves the VPN and protection active"
                dialog.restart_button.clicked()
                pump()
                assert app.restart_requested and not window.alive
                assert window.client.connected and app.guard.enabled, "Restarting must not disconnect the VPN"
                assert not app.ui._properties, "Closing must release translation bindings"
        finally:
            real_pool.shutdown(wait=True, cancel_futures=True)
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()

print("GTK updates passed: checks, all languages, install/retry, operation and quit guards, restart, unchanged VPN/protection and widget cleanup.")
