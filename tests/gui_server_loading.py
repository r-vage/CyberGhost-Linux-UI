"""Control slow server responses in GTK without invoking CyberGhost or polkit."""

from concurrent.futures import Future
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import Choice, CliError, Status  # noqa: E402
from cyberghost_desktop.firewall import GuardStatus  # noqa: E402


def pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class ControlledExecutor:
    def submit(self, _work):
        future = Future()
        future.set_running_or_notify_cancel()
        return future

    def shutdown(self, **_kwargs):
        pass


app = Application(demo=True, tray=False)
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
with patch('cyberghost_desktop.app.ThreadPoolExecutor', return_value=ControlledExecutor()):
    try:
        Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
        app.activate()
        window = app.window
        window._futures['status'].set_result(Status('connected', 'Demo VPN connected'))
        window._futures['locations'].set_result(window.client.locations('traffic'))
        pump()
        germany = next(row for row in window.location_list.get_children() if row.location.code == 'DE')
        window.location_list.select_row(germany)
        window._futures['cities'].set_result([Choice('Berlin', 'Berlin · 110 servers'), Choice('Frankfurt', 'Frankfurt · 993 servers')])
        pump()
        window.city.set_active_id('Berlin')
        pending = window._futures['servers']
        picker = window.server
        assert len(picker.get_model()) == 1
        picker.button.clicked()
        pump()
        assert picker.popover.get_visible()
        assert picker.status_box.get_visible()
        assert picker.status_label.get_visible()
        assert picker.status_label.get_text() == 'Loading servers…'
        assert picker.status_spinner.get_property('active')
        pending.set_result([Choice('berlin-1', 'Berlin 1 · 40% load')])
        pump()
        assert [row[1] for row in picker.get_model()] == ['', 'berlin-1']
        assert not picker.status_box.get_visible()
        assert not picker.status_spinner.get_property('active')
        picker.popover.popdown()

        window.city.set_active_id('Frankfurt')
        stale = window._futures['servers']
        window.city.set_active_id('Berlin')
        refresh = window._futures['servers']
        assert [row[1] for row in picker.get_model()] == ['', 'berlin-1'], 'Show cached entries before the refresh returns'
        assert picker.status_label.get_text() == 'Refreshing servers…'
        picker.set_active_id('berlin-1')
        stale.set_result([Choice('frankfurt-1', 'Frankfurt 1')])
        pump()
        assert picker.status_spinner.get_property('active'), 'A stale response must not clear the current loading state'
        refresh.set_result([Choice('berlin-2', 'Berlin 2 · 30% load'), Choice('berlin-1', 'Berlin 1 · 50% load')])
        pump()
        assert picker.get_active_id() == 'berlin-1', 'Keep the user selection while updating load and order'
        assert len(picker.get_model()) == 3, 'Refreshing replaces rows instead of duplicating them'
        window._load_servers()
        window._futures['servers'].set_exception(CliError('Catalogue temporarily unavailable'))
        pump()
        assert len(picker.get_model()) == 3 and picker.get_active_id() == 'berlin-1'
        assert not picker.status_spinner.get_property('active')
        assert 'saved servers' in picker.status_label.get_text()

        # Country/city totals can be cached even when a city has never had its
        # individual servers fetched. Reconnection must start that fetch.
        window._apply_guard(GuardStatus('on', 'Demo protection enabled'))
        window._apply_status(Status('disconnected', 'Demo disconnected'))
        last = window._futures['servers']
        window.city.set_active_id('Frankfurt')
        key = ('servers', 'traffic', 'DE', 'Frankfurt')
        assert key not in window._cache, 'Do not turn a missing offline list into a cached empty response'
        assert window._futures['servers'] is last, 'Offline browsing must not make a blocked API call'
        assert picker.status_label.get_text() == 'Connect to load servers'
        window._apply_status(Status('connected', 'Demo reconnected'))
        assert window._futures['servers'] is not last
        assert picker.status_label.get_text() == 'Loading servers…'
        window._futures['servers'].set_result([Choice('frankfurt-2', 'Frankfurt 2')])
        pump()
        assert [row[1] for row in picker.get_model()] == ['', 'frankfurt-2']
        assert window.city.get_active_id() == 'Frankfurt'

        window.city.set_active_id('Berlin')
        pending = window._futures['servers']
        window.city.set_active_id('')
        pending.set_result([Choice('stale', 'Stale')])
        pump()
        assert len(picker.get_model()) == 1 and not picker.status_box.get_visible()
        assert not picker.status_spinner.get_property('active')
        print('GTK server checks passed: delayed response, open loading menu, cached refresh, selection retention, stale replies, failure recovery and automatic loading after reconnect.')
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()
        pump()
