# Exercise saved catalogs and the default kill-switch policy in actual GTK.
# All VPN, firewall and boot work uses controlled demo backends.

from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import Choice, CliError, Location, Status  # noqa: E402
from cyberghost_desktop.preferences import Preferences  # noqa: E402
from cyberghost_desktop.startup import DemoStartup, StartupState  # noqa: E402


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class ControlledExecutor:
    def submit(self, _work):
        result = Future()
        result.work = _work
        result.set_running_or_notify_cancel()
        return result

    def shutdown(self, **_kwargs):
        pass


def check(startup_state, index):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'preferences.json'
        prefs = Preferences(path)
        prefs.remember_locations('traffic', [Location('Germany', 'DE'), Location('France', 'FR')])
        prefs.remember_locations('torrent', [Location('Germany', 'DE')])
        prefs.remember_choices(('traffic', 'DE'), [Choice('Berlin', 'Berlin')])
        prefs.remember_choices(('servers', 'traffic', 'DE', 'Berlin'), [Choice('berlin-1', 'Berlin 1', 30)])
        prefs.data['last_location'] = 'traffic:DE:'
        prefs.save()
        startup = DemoStartup()
        startup.demo = False
        app = Application(demo=True, tray=False, preferences=Preferences(path), startup=startup)
        app.set_application_id(f'io.github.rvage.CyberGhostDesktop.Policy{index}')
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        with patch('cyberghost_desktop.app.ThreadPoolExecutor', return_value=ControlledExecutor()):
            try:
                app.activate()
                window = app.window
                assert len(window.locations) == 2 and window.location.code == 'DE'
                assert window.guard_toggle.get_active() and window.guard_state.state == 'off'
                assert 'Not active yet' in window.guard_label.get_text()
                assert not app.guard.enabled, 'Opening the app never silently changes firewall state'
                assert len(window.city.get_model()) == 2
                window.city.set_active_id('Berlin')
                window.server.set_active_id('berlin-1')
                assert window._selection().server == 'berlin-1'
                assert all(key not in window._futures for key in ('locations', 'cities', 'servers'))
                window._futures['status'].set_result(Status('disconnected', 'Initial snapshot'))
                window._futures['startup-status'].set_result(startup_state)
                pump()
                if startup_state.running:
                    assert not window.connect_button.get_sensitive()
                    app.guard.enabled = startup_state.vpn_only
                    window._apply_startup(replace(startup_state, running=False))
                    window._futures['status'].set_result(Status('connected', 'Boot VPN ready'))
                    pump()
                assert all(key not in window._futures for key in ('locations', 'cities', 'servers'))

                # Browsing another previously fetched category is also offline.
                window.mode.set_active_id('torrent')
                window.mode.set_active_id('traffic')
                assert 'locations' not in window._futures
                window.location_list.select_row(window.location_list.get_children()[0])
                window.city.set_active_id('Berlin')
                window.server.set_active_id('berlin-1')
                window.refresh_locations()
                assert 'locations' in window._futures, 'Explicit Refresh must request current data'
                window._futures['locations'].set_result([Location('Germany', 'DE'), Location('France', 'FR')])
                pump()
                window._futures['cities'].set_result([Choice('Berlin', 'Berlin')])
                pump()
                window._futures['servers'].set_result([Choice('berlin-1', 'Berlin 1', 50)])
                pump()
                assert window._selection().server == 'berlin-1', 'Refresh must preserve the chosen city/server'
                assert Preferences(path).choice_cache()[('servers', 'traffic', 'DE', 'Berlin')][0].load == 50

                if not startup_state.enabled:
                    window._control('connect')
                    assert window.control_busy
                    future = window._futures['control']
                    future.set_result(future.work())
                    assert window.client.last_vpn_only
                    pump()
                    window._futures['status'].set_result(Status('connected', 'Demo connected'))
                    pump()
                    assert app.guard.enabled and window.guard_state.state == 'on'
                    window._control('disconnect')
                    future = window._futures['control']
                    future.set_result(future.work())
                    pump()
                    window._futures['status'].set_result(Status('disconnected', 'Demo disconnected'))
                    pump()
                    assert app.guard.enabled, 'Disconnect must keep internet blocking enabled'
                    window.guard_toggle.set_active(False)
                    window._futures['guard-change'].set_exception(CliError('Authorization cancelled'))
                    pump()
                    assert window.guard_toggle.get_active() and window.preferences.data['vpn_only']
                    window.guard_toggle.set_active(False)
                    result = app.guard.change(False)
                    window._futures['guard-change'].set_result(result)
                    pump()
                    assert not window.preferences.data['vpn_only'] and not Preferences(path).data['vpn_only']
            finally:
                if app.window and app.window.alive:
                    app.window.destroy()
                app.quit()
                pump()


states = (StartupState(), StartupState(True, running=True), StartupState(True, vpn_only=True, running=True),
          StartupState(True, vpn_only=True, running=True, block_until_connected=True))
if len(sys.argv) == 1:
    # GTK owns process-wide application registration; each scenario gets a
    # fresh process as well as fresh preferences, like reopening the app.
    for index in range(len(states)):
        subprocess.run([sys.executable, __file__, str(index)], check=True, timeout=15)
    print('GTK policy matrix passed: cached restart, manual refresh, no boot setup, ordinary boot, post-connect protection, early blocking, protected disconnect and explicit opt-out.')
else:
    Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
    index = int(sys.argv[1])
    check(states[index], index)
