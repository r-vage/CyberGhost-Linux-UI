#!/usr/bin/env python3
"""Capture real GTK documentation screens with isolated, in-memory demo data.

Run with the system Python in a graphical session, or with GDK_BACKEND=broadway
and BROADWAY_DISPLAY pointing at a separate broadwayd. No ComfyUI is required.
"""

import argparse
from contextlib import ExitStack
import math
from pathlib import Path
import sys
from unittest.mock import patch

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cyberghost_desktop.app import Application  # noqa: E402
from cyberghost_desktop.authorization import AuthorizationStatus  # noqa: E402
from cyberghost_desktop.cli import Choice, DemoClient  # noqa: E402
from cyberghost_desktop.i18n import Translator  # noqa: E402
from cyberghost_desktop.preferences import Preferences  # noqa: E402
from cyberghost_desktop.setup_dialog import SetupDialog  # noqa: E402
from cyberghost_desktop.setup_tasks import Installation  # noqa: E402


class TourClient(DemoClient):
    """A larger example catalog makes the real scrolling server picker visible."""

    def cities(self, selection):
        return [Choice("Berlin", "Berlin · 110 servers · 47% load", 47),
                Choice("Frankfurt", "Frankfurt · 16 servers · 52% load", 52)]

    def servers(self, selection):
        return [Choice(f"frankfurt-demo-{index:02d}", f"frankfurt-demo-{index:02d} · {load}% load", load)
                for index, load in enumerate((18, 22, 25, 29, 34, 38, 43, 48, 53, 60, 64, 69, 72, 78, 84, 91), 1)]


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
    deadline = GLib.timeout_add_seconds(10, timeout)
    loop.run()
    if failed:
        GLib.source_remove(tick)
        raise RuntimeError("GTK capture did not reach the expected state")
    GLib.source_remove(deadline)


def paint(window):
    complete = []
    previous = None
    stable = 0

    def geometry(widget):
        area = widget.get_allocation()
        result = [(area.x, area.y, area.width, area.height, widget.get_mapped())]
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                result.extend(geometry(child))
        return result

    def after_paint(_clock):
        nonlocal previous, stable
        current = geometry(window)
        stable = stable + 1 if current == previous else 0
        previous = current
        if stable >= 3:
            complete.append(True)
        else:
            window.queue_draw()

    until(lambda: window.get_mapped())
    clock = window.get_frame_clock()
    signal = clock.connect("after-paint", after_paint)
    window.queue_draw()
    try:
        until(lambda: bool(complete))
    finally:
        clock.disconnect(signal)


def capture(window, path):
    paint(window)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, window.get_allocated_width(), window.get_allocated_height())
    window.draw(cairo.Context(surface))
    surface.write_to_png(str(path))


def color(context, value):
    value = value.lstrip("#")
    context.set_source_rgb(*(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)))


def rounded(context, x, y, width, height, radius=12):
    context.new_sub_path()
    for cx, cy, start in ((x + width - radius, y + radius, -90),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, 90),
                          (x + radius, y + radius, 180)):
        context.arc(cx, cy, radius, math.radians(start), math.radians(start + 90))
    context.close_path()


def text(context, value, x, y, width, size=17, *, bold=False, shade="#c7cbd3"):
    layout = PangoCairo.create_layout(context)
    layout.set_text(value, -1)
    layout.set_width(int(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    font = Pango.FontDescription("Sans Bold" if bold else "Sans")
    font.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_spacing(4 * Pango.SCALE)
    color(context, shade)
    context.move_to(x, y)
    PangoCairo.show_layout(context, layout)
    return layout.get_pixel_size()[1]


def bounds(window, widgets):
    rectangles = []
    for widget in widgets:
        assert widget.get_mapped(), "Tour target is not visible"
        position = widget.translate_coordinates(window, 0, 0)
        assert position is not None
        x, y = position
        rectangles.append((x, y, x + widget.get_allocated_width(), y + widget.get_allocated_height()))
    left = min(rect[0] for rect in rectangles)
    top = min(rect[1] for rect in rectangles)
    right = max(rect[2] for rect in rectangles)
    bottom = max(rect[3] for rect in rectangles)
    assert 0 <= left < right <= window.get_allocated_width()
    assert 0 <= top < bottom <= window.get_allocated_height(), (left, top, right, bottom)
    return left, top, right - left, bottom - top


def tour(window, destination, chapter, heading, subtitle, callouts):
    """Render the live GTK widget tree and vector callouts on one documentation page."""
    paint(window)
    ui_width, ui_height = window.get_allocated_width(), window.get_allocated_height()
    width, height = 1920, ui_height + 252
    ui_x, ui_y = (width - ui_width) / 2, 184
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    color(context, "#10141b")
    context.paint()
    rounded(context, 40, 32, width - 80, 124)
    color(context, "#181d25")
    context.fill_preserve()
    color(context, "#514536")
    context.set_line_width(1)
    context.stroke()
    color(context, "#e8b978")
    context.rectangle(40, 47, 3, 92)
    context.fill()
    text(context, f"CYBERGHOST LINUX UI  /  VISUAL TOUR  /  {chapter:02d} — 05", 65, 49, 1750, 13,
         bold=True, shade="#e8b978")
    text(context, heading, 65, 74, 1750, 32, bold=True, shade="#f6f2eb")
    text(context, subtitle, 65, 119, 1750, 17)
    context.save()
    context.translate(ui_x, ui_y)
    window.draw(context)
    context.restore()
    boxes = []
    for number, entry in enumerate(callouts, 1):
        side, y, title, detail, widgets = entry
        target_x, target_y, target_w, target_h = bounds(window, widgets)
        target_y += ui_y
        target_x += ui_x
        box_width = min(330, ui_x - 75)
        x = 40 if side == "left" else width - 40 - box_width
        # Measure on a throwaway context so the finished text is drawn just once.
        measure = cairo.Context(cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None))
        title_height = text(measure, title, 0, 0, box_width - 82, 20, bold=True)
        body_height = text(measure, detail, 0, 0, box_width - 40, 17)
        box_height = max(36, title_height) + body_height + 54
        assert y + box_height < height - 30, (heading, title, y, box_height, height)
        for previous in boxes:
            if previous[0] == side:
                assert y >= previous[2] + 18 or y + box_height + 18 <= previous[1], title
        boxes.append((side, y, y + box_height))
        # Keep connectors outside the app. A matching border identifies the control.
        end_x = ui_x + 22 if side == "left" else ui_x + ui_width - 22
        lane = ui_x - 8 if side == "left" else ui_x + ui_width + 8
        start_x = x + box_width if side == "left" else x
        destination_y = target_y + target_h / 2
        context.move_to(start_x, y + box_height / 2)
        context.line_to(lane, y + box_height / 2)
        context.line_to(lane, destination_y)
        context.line_to(end_x, destination_y)
        color(context, "#e8b978")
        context.set_line_width(2)
        context.stroke()
        direction = 1 if side == "left" else -1
        context.move_to(end_x, destination_y)
        context.line_to(end_x - 10 * direction, destination_y - 5)
        context.line_to(end_x - 10 * direction, destination_y + 5)
        context.close_path()
        context.fill()
        rounded(context, target_x - 4, target_y - 4, target_w + 8, target_h + 8, 6)
        context.set_line_width(1.3)
        context.stroke()
        rounded(context, x, y, box_width, box_height)
        color(context, "#181d25")
        context.fill_preserve()
        color(context, "#514536")
        context.set_line_width(1)
        context.stroke()
        rounded(context, x + 18, y + 18, 34, 34, 8)
        color(context, "#e8b978")
        context.fill()
        text(context, str(number), x + 29, y + 22, 25, 19, bold=True, shade="#171a20")
        text(context, title, x + 64, y + 18, box_width - 82, 20, bold=True, shade="#f6f2eb")
        text(context, detail, x + 20, y + 32 + max(36, title_height), box_width - 40, 17)
    text(context, "Real GTK interface · Demo data · No VPN or system settings changed for these captures",
         40, height - 42, width - 80, 14, shade="#9ba8b9")
    surface.write_to_png(str(destination))
    print(destination.name, f"{width} × {height}", flush=True)


def external_action(*_args, **_kwargs):
    raise RuntimeError("Documentation capture attempted a real system action")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs", help="Screenshot directory (default: docs)")
    parser.add_argument("--language", choices=tuple(Translator().catalogs), default="en", help="Interface language")
    parser.add_argument("--readme-only", action="store_true", help="Capture the three README images without the annotated English tour")
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    gallery = output / "tour"
    if not options.readme_only:
        gallery.mkdir(exist_ok=True)

    def render_tour(*args, **kwargs):
        if not options.readme_only:
            tour(*args, **kwargs)

    preferences = Preferences(persistent=False)
    preferences.data["language"] = options.language
    app = Application(demo=True, tray=False, client=TourClient(), preferences=preferences)
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
        app.activate()
        window = app.window
        window.resize(1080, 1040)
        until(lambda: len(window.locations) == 8 and window.state.state == "disconnected" and window._startup_ready)
        germany = next(row for row in window.location_list.get_children() if row.location.code == "DE")
        window.location_list.select_row(germany)
        until(lambda: len(window.city.get_model()) == 3)
        window.favorite_button.set_active(True)
        window.city.set_active_id("Frankfurt")
        until(lambda: len(window.server.get_model()) == 17)
        window.server.button.clicked()
        until(lambda: window.server.popover.get_mapped())
        render_tour(window, gallery / "locations.png", 1, "Choose a location and a server",
             "Country flags, saved favorites and a scrolling server list keep destination choices together.", [
                 ("left", 230, "Find a location", "Search by country or code. All locations, Streaming and P2P share the same sidebar.",
                  [window.mode, window.search]),
                 ("left", 635, "Keep favorites close", "The star saves this location. Favorites filters the list; flags are bundled for offline use.",
                  [window.favorites_only]),
                 ("right", 680, "Choose the protocol", "WireGuard or OpenVPN. OpenVPN also lets you choose UDP or TCP transport.",
                  [window.protocol]),
                 ("right", 1020, "Compare server load", "Automatic stays first, then the lowest reported loads. Scroll for more. Load is a snapshot, not a speed test.",
                  [window.server]),
             ])
        window.server.popover.popdown()
        window.server.set_active_id("frankfurt-demo-01")
        window.connect_button.clicked()
        until(lambda: window.state.state == "connected" and not window.control_busy)
        window.guard_toggle.set_active(True)
        until(lambda: window.guard_state.state == "on" and not window.control_busy)
        capture(window, output / "desktop.png")
        window.server.set_active_id("frankfurt-demo-02")
        assert window.connect_button.get_sensitive(), "Changed destination should offer Connect"
        render_tour(window, gallery / "connection.png", 2, "Check the connection and protect other traffic",
             "The connection panel and tray share the same green, red and amber status icons.", [
                 ("left", 250, "Read the state", "Green arrow: connected. Red cross: disconnected. Amber question mark: changing or unknown status.",
                  [window.status_chip, window.status_icon, window.status_title]),
                 ("right", 280, "Compare public IPv4", "See the address checked before connecting and the current VPN exit. Addresses stay in memory.",
                  [window.before_ip_label, window.current_ip_label]),
                 ("right", 625, "Keep VPN only on", "Enable after a WireGuard connection. Direct internet and LAN traffic remain blocked after disconnecting.",
                  [window.guard_toggle, window.guard_label]),
                 ("left", 940, "Switch deliberately", "Choose a different destination, then click Connect. The app disconnects the old VPN before connecting to the new one.",
                  [window.connect_button, window.disconnect_button]),
             ])
        window.startup_expander.set_expanded(True)
        window.resize(1080, 1280)
        window.startup_guard.set_active(True)
        window.startup_toggle.set_active(True)
        until(lambda: window.startup_state.enabled and not window.control_busy)
        capture(window, output / "startup.png")
        render_tour(window, gallery / "startup.png", 3, "Save a connection for the next boot",
             "Startup is optional and uses a saved destination, separate from the current connection.", [
                 ("left", 380, "Choose what to save", "Select the country, city, server and protocol to use at startup. The current VPN keeps running.",
                  [window.city, window.server]),
                 ("right", 820, "Authorize once", "Enable Connect VPN during boot. A systemd service connects after networking is available; the app opens at desktop login.",
                  [window.startup_toggle]),
                 ("left", 1160, "Protect after connect", "Optional VPN only is applied after a successful boot connection. It does not block early boot traffic.",
                  [window.startup_guard]),
                 ("right", 1205, "Update the saved choice", "Changing the main selection does not change startup. Use Save current location for startup to apply new choices.",
                  [window.startup_save, window.startup_detail]),
             ])
        window.startup_expander.set_expanded(False)
        window.resize(1080, 1160)
        command_expander = window.command_expander
        command_expander.set_expanded(True)
        window.log_expander.set_expanded(True)
        paint(window)
        adjustment = window.content_scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        render_tour(window, gallery / "activity.png", 4, "Inspect the command and its result",
             "The app keeps the requested command and the CLI's response available for troubleshooting.", [
                 ("left", 800, "Preview before running", "Command preview reflects the current selection. Copy command gives you the corresponding terminal command.",
                  [window.command_label, window.copy_button]),
                 ("right", 1030, "Read the actual output", "Expand Activity & CLI output for connection progress and errors. Refresh checks status and location lists again.",
                  [window.log]),
                 ("left", 1220, "Close without disconnecting", "With tray support, closing hides the window. Quit leaves the VPN running. Use Disconnect to stop the tunnel.",
                  [window.log_expander.get_parent().get_children()[-1]]),
             ])
        installation = Installation(None, Path("/home/example/.cyberghost/config.ini"), "missing",
                                    "/usr/bin/pkexec", "/usr/bin/sudo", ("/usr/bin/x-terminal-emulator", "-e"))
        with (patch("cyberghost_desktop.setup_dialog.setup_tasks.inspect_installation", return_value=installation),
              patch("cyberghost_desktop.setup_dialog.authorization.status",
                    return_value=AuthorizationStatus("off", "Linux asks for authorization for each VPN change.")),
              patch("cyberghost_desktop.setup_dialog.distribution_name", return_value="Linux Mint (example setup)")):
            dialog = SetupDialog(window)
            window.setup_dialog = dialog
            dialog.resize(740, 1240)
            dialog.present()
            capture(dialog, output / "setup.png")
            render_tour(dialog, gallery / "setup.png", 5, "Install the CLI and manage authorization",
                 "Setup guides the official installer and account configuration through the terminal.", [
                     ("left", 280, "Check this computer", "See whether the CLI, account configuration and connection authorization are available. Recheck after installing.",
                      [dialog.cli_status, dialog.account_status, dialog.check_button]),
                     ("right", 420, "Use the official download", "Get the Linux CLI from your CyberGhost account, extract it, then select its install.sh. A subscription is required.",
                      [dialog.install_button]),
                     ("left", 770, "Configure the account", "Use the official CLI terminal when account setup is needed. The desktop app never asks for or stores your password.",
                      [dialog.account_button]),
                     ("right", 1030, "Remember authorization", "Optionally enable brief Linux authorization caching for VPN controls. Update or remove its helper here later.",
                      [dialog.retention_status, dialog.retention_enable, dialog.retention_disable]),
                 ])
            dialog.destroy()
            window.setup_dialog = None
        print("Captured three README screenshots" + ("." if options.readme_only else " and five annotated tour screens.") +
              " Demo data only; language: " + options.language, flush=True)
    finally:
        if app.window and app.window.alive:
            app.window.destroy()
        app.quit()


if __name__ == "__main__":
    with ExitStack() as safety:
        for target in ("subprocess.run", "subprocess.Popen", "cyberghost_desktop.setup_tasks.run_in_terminal",
                       "cyberghost_desktop.authorization.configure"):
            safety.enter_context(patch(target, side_effect=external_action))
        main()
