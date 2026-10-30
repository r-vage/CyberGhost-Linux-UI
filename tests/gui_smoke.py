"""Exercise actual GTK widgets using only the demo client, never the real VPN."""

from concurrent.futures import Future
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio, GLib, Gtk  # noqa: E402
from cyberghost_desktop.cli import Choice, CliError, Status  # noqa: E402
from cyberghost_desktop.public_ip import PublicIP  # noqa: E402
from cyberghost_desktop.startup import StartupState  # noqa: E402


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

    tick = GLib.timeout_add(10, check)
    deadline = GLib.timeout_add_seconds(8, timeout)
    loop.run()
    if failure:
        GLib.source_remove(tick)
        raise AssertionError(failure[0])
    GLib.source_remove(deadline)


def capture(window, destination):
    import cairo
    drawn = []
    signal = window.connect_after("draw", lambda *_: drawn.append(True) and False)
    window.queue_draw()
    until(lambda: bool(drawn))
    window.disconnect(signal)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, window.get_allocated_width(), window.get_allocated_height())
    window.draw(cairo.Context(surface))
    surface.write_to_png(destination)


class ControlledExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, work):
        future = Future()
        future.set_running_or_notify_cancel()
        self.tasks.append((work, future))
        return future

    def shutdown(self, **_kwargs):
        for _work, future in self.tasks:
            future.cancel()


app = Application(demo=True, tray=False)
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
try:
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    app.activate()
    window = app.window
    until(lambda: len(window.locations) == 8 and window.state.state == "disconnected")
    assert not window.client.connected
    assert not window.connect_button.get_sensitive()
    assert not window.setup_button.get_sensitive()
    assert window.guard_state.state == "off" and not app.guard.enabled
    assert not window.guard_toggle.get_sensitive()
    assert not window.startup_state.enabled
    assert not window.startup_toggle.get_sensitive()
    assert window.before_ip_label.get_text() == "203.0.113.42"
    window._show_setup()
    assert window.setup_dialog is None, "Demo mode must not open real CLI installation"

    def choose(code):
        row = next(row for row in window.location_list.get_children() if row.location.code == code)
        window.location_list.select_row(row)

    choose("DE")
    until(lambda: len(window.city.get_model()) == 3)
    assert window.connect_button.get_sensitive()
    assert window.startup_toggle.get_sensitive()
    window._apply_startup(StartupState(available=False, detail="Boot connection needs systemd"))
    assert not window.startup_toggle.get_sensitive() and not window.startup_guard.get_sensitive()
    assert window.connect_button.get_sensitive(), "Manual VPN connections must work without systemd"
    window._apply_startup(StartupState())
    window.startup_guard.set_active(True)
    window.startup_toggle.set_active(True)
    until(lambda: window.startup_state.enabled and not window.control_busy)
    assert not window.client.connected, "Saving startup must not connect now"
    assert window.startup_state.vpn_only
    assert window.startup_state.selection.country == "DE"
    startup_screenshot = os.environ.get("CYBERGHOST_STARTUP_SCREENSHOT")
    if startup_screenshot:
        window.startup_expander.set_expanded(True)
        window.resize(1080, 1280)
        capture(window, startup_screenshot)
        window.startup_expander.set_expanded(False)
        window.resize(1080, 780)
    window.protocol.set_active_id("openvpn")
    window.startup_save.emit("clicked")
    until(lambda: window.startup_state.selection.protocol == "openvpn" and not window.control_busy)
    assert not window.startup_state.vpn_only
    window.startup_toggle.set_active(False)
    until(lambda: not window.startup_state.enabled and not window.control_busy)
    assert not window.client.connected
    app.startup.state = StartupState(True, running=True, detail="Waiting for network…")
    window.refresh_startup()
    assert not window.connect_button.get_sensitive() and not window.disconnect_button.get_sensitive()
    assert not window.setup_button.get_sensitive()
    window._control("connect")
    assert not window.control_busy and not window.client.connected
    app.startup.state = StartupState()
    window.refresh_startup()
    window.protocol.set_active_id("wireguard")
    window._apply_status(Status("unknown", "Cannot verify WireGuard status"))
    assert not window.connect_button.get_sensitive(), "Unknown status must not invite repeated reconnects"
    assert window.disconnect_button.get_sensitive()
    window._apply_status(Status("connected", "CyberGhost WireGuard interface is active and the IPv4 internet route uses it."))
    assert "IPv4 internet route" in window.status_detail.get_text()
    assert not window.connect_button.get_sensitive()
    window._apply_status(Status("disconnected", "No VPN connections found."))
    assert window.connect_button.get_sensitive()
    assert not window.transport.get_sensitive()
    window.protocol.set_active_id("openvpn")
    window.transport.set_active_id("TCP")
    assert window.transport.get_sensitive()
    window.city.set_active_id("Frankfurt")
    until(lambda: len(window.server.get_model()) == 2)
    window.server.set_active_id("frankfurt-s415-i01")
    assert "--connection TCP" in window.command_label.get_text()
    assert "--server frankfurt-s415-i01" in window.command_label.get_text()
    window.favorite_button.set_active(True)
    window.favorites_only.set_active(True)
    assert len(window.location_list.get_children()) == 1
    window.favorites_only.set_active(False)
    window.search.set_text("germany")
    window._render_locations()
    assert len(window.location_list.get_children()) == 1
    window.search.set_text("")
    window._render_locations()

    window.connect_button.emit("clicked")
    until(lambda: window.state.state == "connected" and not window.control_busy)
    assert window.current_ip_label.get_text() == "198.51.100.24 · DE"
    assert window.before_ip_label.get_text() == "203.0.113.42"
    assert not window.guard_toggle.get_sensitive(), "OpenVPN is not supported by this WireGuard guard"
    window.protocol.set_active_id("wireguard")
    assert window.guard_toggle.get_sensitive()
    window.guard_toggle.set_active(True)
    until(lambda: window.guard_state.state == "on" and not window.control_busy)
    assert not window.connect_button.get_sensitive()
    assert window.disconnect_button.get_sensitive()
    window.disconnect_button.emit("clicked")
    until(lambda: window.state.state == "disconnected" and not window.control_busy)
    assert window.guard_state.state == "on" and app.guard.enabled
    assert not window.connect_button.get_sensitive()
    assert window.current_ip_label.get_text() == "Blocked by VPN only"
    window.guard_toggle.set_active(False)
    until(lambda: window.guard_state.state == "off" and not window.control_busy)
    assert window.connect_button.get_sensitive()

    window.mode.set_active_id("streaming")
    until(lambda: len(window.locations) == 1)
    window.location_list.select_row(window.location_list.get_children()[0])
    assert not window.city.get_sensitive()
    assert not window.server.get_sensitive()
    assert "--streaming 'Example streaming DE'" in window.command_label.get_text()

    window.mode.set_active_id("traffic")
    until(lambda: len(window.locations) == 8)
    choose("DE")
    until(lambda: len(window.city.get_model()) == 3)
    window.protocol.set_active_id("wireguard")
    until(lambda: window.get_allocated_width() > 900)
    pump()
    # A longer window grows the right pane only; location/service labels must
    # not move the divider or make the left panel grow with the window.
    initial_width = window.get_allocated_width()
    initial_client_width = window.get_size()[0]
    left_width = window.sidebar.get_allocated_width()
    right_width = window.content_scroll.get_allocated_width()
    window.resize(initial_client_width + 260, 900)
    until(lambda: window.get_allocated_width() >= initial_width + 260)
    assert abs(window.sidebar.get_allocated_width() - left_width) <= 1
    assert window.content_scroll.get_allocated_width() >= right_width + 259
    window.server.append("long", "A very long server name " * 8)
    window.server.set_active_id("long")
    pump()
    assert abs(window.sidebar.get_allocated_width() - left_width) <= 1
    window.server.set_active_id("")
    window.resize(initial_client_width, 780)
    try:
        until(lambda: window.get_allocated_width() == initial_width)
    except AssertionError:
        raise AssertionError(f"Window width {window.get_allocated_width()}, expected {initial_width}; "
                             f"main minimum {window.main_paned.get_preferred_width()}, "
                             f"server minimum {window.server.get_preferred_width()}") from None
    screenshot = os.environ.get("CYBERGHOST_TEST_SCREENSHOT")
    if screenshot:
        window.resize(initial_client_width, 1040)
        window.connect_button.emit("clicked")
        until(lambda: window.state.state == "connected" and not window.control_busy)
        capture(window, screenshot)
        window.disconnect_button.emit("clicked")
        until(lambda: window.state.state == "disconnected" and not window.control_busy)

    # Deterministic out-of-order callbacks: the old country's cities must not
    # leak into the currently selected country, even if its request finishes last.
    window._pool.shutdown(wait=True)
    controlled = ControlledExecutor()
    window._pool = controlled
    window._cache.clear()
    choose("FR")
    old = controlled.tasks[-1][1]
    choose("DE")
    new = controlled.tasks[-1][1]
    new.set_result([Choice("Berlin", "Berlin")])
    pump()
    old.set_result([Choice("Paris", "Paris")])
    pump()
    assert [row[1] for row in window.city.get_model()] == ["", "Berlin"]

    window.refresh_status()
    old_status = controlled.tasks[-1][1]
    window._control("connect")
    control = controlled.tasks[-1][1]
    control.set_result("Demo connect finished")
    pump()
    fresh_status = controlled.tasks[-1][1]
    fresh_status.set_result(Status("connected", "VPN connection found."))
    pump()
    old_status.set_result(Status("disconnected", "No VPN connections found."))
    pump()
    assert window.state.state == "connected"

    window._control("disconnect")
    controlled.tasks[-1][1].set_exception(CliError("Authorization was cancelled."))
    pump()
    controlled.tasks[-1][1].set_result(Status("connected", "VPN connection found."))
    pump()
    assert window.error_box.get_visible()
    assert window.state.state == "connected"
    assert not window.control_busy

    # IP responses from before a Connect/Disconnect cannot overwrite the exit
    # address or invent a baseline. All requests here use controlled futures.
    window.client.demo = False  # Still the fake client, but exercise the async path.
    window.installation = SimpleNamespace(needs_setup=False)
    window.before_ip = None
    window._invalidate_public_ip()
    window._apply_status(Status("connected", "VPN connection found."))
    current = controlled.tasks[-1][1]
    current.set_result(PublicIP("198.51.100.8", "DE"))
    pump()
    assert window.before_ip is None
    assert window.before_ip_label.get_text() == "No baseline recorded"
    window._apply_status(Status("disconnected", "No VPN connections found."))
    old_ip = controlled.tasks[-1][1]
    window._invalidate_public_ip()
    window._apply_status(Status("connected", "VPN connection found."))
    new_ip = controlled.tasks[-1][1]
    new_ip.set_result(PublicIP("198.51.100.9", "DE"))
    pump()
    old_ip.set_result(PublicIP("203.0.113.99"))
    pump()
    assert window.before_ip is None
    assert window.current_ip.address == "198.51.100.9"
    window._invalidate_public_ip()
    window._refresh_public_ip()
    controlled.tasks[-1][1].set_exception(TimeoutError("unavailable"))
    pump()
    assert window.state.state == "connected", "IP lookup failure is not a VPN disconnection"
    assert window.current_ip is None
    window.client.demo = True
    # A denied firewall request leaves the verified switch state intact.
    window.guard_toggle.set_active(True)
    controlled.tasks[-1][1].set_exception(CliError("Firewall authorization was cancelled."))
    pump()
    assert window.guard_state.state == "off"
    assert not window.guard_toggle.get_active() and not window.control_busy
    window.startup_toggle.set_active(True)
    controlled.tasks[-1][1].set_exception(CliError("Startup authorization was cancelled."))
    pump()
    assert not window.startup_state.enabled and not window.startup_toggle.get_active()
    assert not window.control_busy
    app.startup.demo = False
    window.refresh_startup()
    controlled.tasks[-1][1].set_exception(CliError("Startup state unavailable"))
    pump()
    window._apply_guard(window.guard_state)
    assert not window._startup_ready and not window.connect_button.get_sensitive()
    assert window.refresh_button.get_sensitive(), "An unknown startup state must remain refreshable"
    app.startup.demo = True
    window.refresh_startup()
    assert window._startup_ready
    buffer = window.log.get_buffer()
    log = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
    assert "198.51.100" not in log and "203.0.113" not in log
    assert all("ip" not in key for key in window.preferences.data)
    print("GTK checks passed: existing actions, resize, public IP, kill switch, startup enable/update/disable, boot-job interlock, failure recovery and authorization cancellation.")
finally:
    if app.window and app.window.alive:
        app.window.destroy()
    app.quit()
    pump()
