"""GTK desktop window; only explicit button actions change the VPN connection."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .cli import CliError, CyberGhostClient, DemoClient, NetworkUnavailable, Selection, Status  # noqa: E402
from .preferences import Preferences  # noqa: E402
from .public_ip import PublicIP, fetch_public_ip  # noqa: E402
from .setup_dialog import SetupDialog  # noqa: E402
from .setup_tasks import inspect_installation  # noqa: E402
from .startup import DemoStartup, StartupManager, StartupState  # noqa: E402
from .vpn_guard import DemoGuard, GuardStatus, VPNGuard  # noqa: E402


def styled(widget, *classes):
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def label(text, *classes, wrap=False):
    widget = styled(Gtk.Label(label=text, xalign=0), *classes)
    if wrap:
        widget.set_line_wrap(True)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_max_width_chars(65)
    return widget


def box(vertical=True, spacing=10, margin=0):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if margin:
        widget.set_border_width(margin)
    return widget


def combo(options):
    widget = Gtk.ComboBoxText()
    for key, text in options:
        widget.append(key, text)
    widget.set_active(0)
    widget.set_hexpand(True)
    for cell in widget.get_cells():
        if isinstance(cell, Gtk.CellRendererText):
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            cell.set_property("max-width-chars", 24)
    return widget


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, client, preferences):
        super().__init__(application=application, title="CyberGhost Desktop")
        self.client, self.preferences = client, preferences
        self.installation = None if client.demo else inspect_installation()
        self.setup_dialog = None
        self._setup_idle = None
        self.alive = True
        self.control_busy = False
        self._connection_action = None
        self.status_pending = False
        self.before_ip = None
        self.current_ip = None
        self._ip_state = None
        self._ip_pending = False
        self.guard_state = GuardStatus("unknown", "Checking VPN-only protection…")
        self._guard_pending = False
        self._setting_guard = False
        self.startup_state = StartupState()
        self._startup_pending = False
        self._startup_ready = False
        self._setting_startup = False
        self.state = Status("unknown", "Checking CyberGhost…")
        self.locations = []
        self.location = None
        self._locations_retry = None
        self._epoch = 0
        self._requests = {}
        self._futures = {}
        self._cache = {}
        self._rendering = False
        self._changing_choices = False
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cyberghost-ui")
        self.set_default_size(1080, 780)
        self.set_size_request(900, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("network-vpn")
        styled(self, "cyberghost")
        self.connect("delete-event", self._delete)
        self.connect("destroy", self._destroy)
        self.connect("key-press-event", self._key_press)
        self._build()
        self.show_all()
        self.error_box.hide()
        self.demo_note.set_visible(client.demo)
        self.spinner.hide()
        self._sync_controls()
        self.refresh_startup()
        self.refresh_guard()
        self.refresh_locations()
        self.refresh_status()
        self._timer = GLib.timeout_add_seconds(15, self._poll)
        if not client.demo and (self.installation.needs_setup or application.setup_on_start):
            self._setup_idle = GLib.idle_add(self._initial_setup)

    def _build(self):
        header = Gtk.HeaderBar(title="CyberGhost Desktop", subtitle="A simple home for your Linux VPN", show_close_button=True)
        self.set_titlebar(header)
        help_button = Gtk.Button.new_from_icon_name("help-about-symbolic", Gtk.IconSize.BUTTON)
        help_button.set_tooltip_text("About and keyboard shortcuts")
        help_button.connect("clicked", self._about)
        header.pack_end(help_button)
        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_button.set_tooltip_text("Refresh locations and connection status (F5)")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_button)
        self.setup_button = Gtk.Button(label="Setup")
        self.setup_button.set_tooltip_text("Install CyberGhost's CLI or configure your account")
        self.setup_button.connect("clicked", self._show_setup)
        header.pack_end(self.setup_button)

        main = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned = main
        self.add(main)
        sidebar = styled(box(), "sidebar")
        self.sidebar = sidebar
        sidebar.set_size_request(305, -1)
        sidebar.set_hexpand(False)
        main.pack1(sidebar, resize=False, shrink=False)
        sidebar_top = box(margin=18)
        sidebar.pack_start(sidebar_top, False, False, 0)
        sidebar_top.pack_start(label("LOCATIONS", "eyebrow"), False, False, 0)
        self.mode = combo([("traffic", "All locations"), ("streaming", "Streaming"), ("torrent", "P2P / Torrent")])
        self.mode.set_active_id(self.preferences.data["mode"])
        self.mode.connect("changed", self._mode_changed)
        sidebar_top.pack_start(self.mode, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Search country or service")
        self.search.connect("search-changed", lambda *_: self._render_locations())
        sidebar_top.pack_start(self.search, False, False, 0)
        self.favorites_only = Gtk.ToggleButton(label="★  Favorites")
        self.favorites_only.connect("toggled", lambda *_: self._render_locations())
        sidebar_top.pack_start(self.favorites_only, False, False, 0)
        self.location_list = styled(Gtk.ListBox(), "locations")
        self.location_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.location_list.connect("row-selected", self._location_selected)
        self.empty_list = label("Loading locations…", "muted", wrap=True)
        self.empty_list.set_margin_start(20)
        self.empty_list.set_margin_end(20)
        self.location_list.set_placeholder(self.empty_list)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.location_list)
        sidebar.pack_start(scroll, True, True, 0)
        self.location_count = label("Contacting CyberGhost…", "caption")
        self.location_count.set_margin_start(20)
        self.location_count.set_margin_bottom(16)
        sidebar.pack_start(self.location_count, False, False, 0)

        content_scroll = Gtk.ScrolledWindow()
        self.content_scroll = content_scroll
        content_scroll.set_hexpand(True)
        content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        main.pack2(content_scroll, resize=True, shrink=False)
        main.set_position(305)
        content = box(spacing=18, margin=25)
        content_scroll.add(content)
        self.demo_note = label("DEMO MODE · Preview only — your VPN connection is unchanged.", "demo", wrap=True)
        content.pack_start(self.demo_note, False, False, 0)
        self.error_box = styled(box(False, 12), "error")
        self.error_label = label("", wrap=True)
        self.error_box.pack_start(self.error_label, True, True, 0)
        dismiss = styled(Gtk.Button(label="×"), "flat")
        dismiss.set_tooltip_text("Dismiss message; details remain in Activity")
        dismiss.connect("clicked", lambda *_: self.error_box.hide())
        self.error_box.pack_end(dismiss, False, False, 0)
        content.pack_start(self.error_box, False, False, 0)

        card = styled(box(spacing=14), "status-card")
        content.pack_start(card, False, False, 0)
        status_header = box(False)
        self.status_chip = label("●  CHECKING STATUS", "status-chip")
        status_header.pack_start(self.status_chip, True, True, 0)
        shield = styled(Gtk.Image.new_from_icon_name("network-vpn-symbolic", Gtk.IconSize.DIALOG), "status-symbol")
        status_header.pack_end(shield, False, False, 0)
        card.pack_start(status_header, False, False, 0)
        self.status_title = label("Checking connection", "status-title")
        card.pack_start(self.status_title, False, False, 0)
        self.status_detail = label("Choose a location to get started.", "muted", wrap=True)
        card.pack_start(self.status_detail, False, False, 0)
        ip_row = Gtk.Grid(column_spacing=22, row_spacing=5, column_homogeneous=True)
        self.before_ip_label = label("Not checked yet", "ip-value")
        self.current_ip_label = label("Checking…", "ip-value")
        for column, title, value in ((0, "BEFORE VPN", self.before_ip_label), (1, "CURRENT PUBLIC IPv4", self.current_ip_label)):
            ip_row.attach(label(title, "eyebrow"), column, 0, 1, 1)
            value.set_selectable(True)
            value.set_hexpand(True)
            value.set_ellipsize(Pango.EllipsizeMode.END)
            value.set_max_width_chars(26)
            ip_row.attach(value, column, 1, 1, 1)
        ip_row.set_tooltip_text("Checks via Cloudflare when connection state changes or you refresh. Addresses stay in memory. "
                                "An address from before the VPN is shown only if checked while disconnected.")
        card.pack_start(ip_row, False, False, 0)
        actions = box(False, 10)
        self.connect_button = styled(Gtk.Button(label="Connect"), "primary")
        self.connect_button.set_tooltip_text("Connect using the selected location. Linux will request administrator authorization.")
        self.connect_button.connect("clicked", lambda *_: self._control("connect"))
        self.disconnect_button = styled(Gtk.Button(label="Disconnect"), "danger")
        self.disconnect_button.set_tooltip_text("Disconnect the CyberGhost VPN using its official CLI.")
        self.disconnect_button.connect("clicked", lambda *_: self._control("disconnect"))
        actions.pack_start(self.connect_button, False, False, 0)
        actions.pack_start(self.disconnect_button, False, False, 0)
        self.spinner = Gtk.Spinner()
        actions.pack_end(self.spinner, False, False, 0)
        card.pack_start(actions, False, False, 0)
        self.operation_note = label("Authorization is handled by Linux. No password is stored here.", "caption", wrap=True)
        card.pack_start(self.operation_note, False, False, 0)
        self.guard_toggle = Gtk.CheckButton(label="VPN only (WireGuard)")
        self.guard_toggle.set_tooltip_text(
            "Optional kill switch. Connect with WireGuard, then enable. Blocks direct IPv4, IPv6 and LAN traffic "
            "if the VPN drops. Stays on after Disconnect or Quit, until disabled or reboot. "
            "Turn it off before changing servers. Requires nftables and Linux authorization.")
        self.guard_toggle.connect("toggled", self._guard_toggled)
        card.pack_start(self.guard_toggle, False, False, 0)
        self.guard_label = label(self.guard_state.detail, "caption", wrap=True)
        card.pack_start(self.guard_label, False, False, 0)

        selected = box(False)
        selected_text = box(spacing=4)
        selected_text.pack_start(label("SELECTED LOCATION", "eyebrow"), False, False, 0)
        self.selected_title = label("Choose a location", "heading")
        self.selected_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.selected_title.set_max_width_chars(36)
        selected_text.pack_start(self.selected_title, False, False, 0)
        selected.pack_start(selected_text, True, True, 0)
        self.favorite_button = Gtk.ToggleButton(label="☆")
        self.favorite_button.set_tooltip_text("Save or remove this location from Favorites")
        self.favorite_button.connect("toggled", self._favorite_changed)
        selected.pack_end(self.favorite_button, False, False, 0)
        content.pack_start(selected, False, False, 0)

        settings = styled(box(spacing=14), "settings-card")
        content.pack_start(settings, False, False, 0)
        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        self.protocol = combo([("wireguard", "WireGuard"), ("openvpn", "OpenVPN")])
        self.protocol.set_active_id(self.preferences.data["protocol"])
        self.protocol.set_tooltip_text("WireGuard is lightweight. OpenVPN offers UDP or TCP transport if you need an alternative.")
        self.transport = combo([("UDP", "UDP"), ("TCP", "TCP")])
        self.transport.set_active_id(self.preferences.data["transport"])
        self.transport.set_tooltip_text("OpenVPN only. UDP favors speed; TCP may work better on restrictive networks.")
        self.city = combo([("", "Automatic city")])
        self.city.set_tooltip_text("Let CyberGhost choose a city, or select one. Server loads are reported by the CLI.")
        self.server = combo([("", "Automatic server")])
        self.server.set_tooltip_text("Choose a city first to list individual servers. Automatic lets the CLI select one.")
        for column, title, widget in [(0, "PROTOCOL", self.protocol), (1, "OPENVPN TRANSPORT", self.transport)]:
            grid.attach(label(title, "eyebrow"), column, 0, 1, 1)
            grid.attach(widget, column, 1, 1, 1)
        for column, title, widget in [(0, "CITY", self.city), (1, "SERVER", self.server)]:
            grid.attach(label(title, "eyebrow"), column, 2, 1, 1)
            grid.attach(widget, column, 3, 1, 1)
        self.city.connect("changed", self._city_changed)
        self.server.connect("changed", lambda *_: self._sync_controls())
        self.protocol.connect("changed", self._protocol_changed)
        self.transport.connect("changed", self._protocol_changed)
        settings.pack_start(grid, False, False, 0)
        self.location_hint = label("Select a country on the left. Nothing connects automatically.", "caption", wrap=True)
        settings.pack_start(self.location_hint, False, False, 0)

        self.startup_expander = Gtk.Expander(label="Startup")
        startup_box = styled(box(spacing=10, margin=14), "settings-card")
        self.startup_toggle = Gtk.CheckButton(label="Connect VPN during boot")
        self.startup_toggle.set_tooltip_text(
            "Authorize once to save this location for future boots. The VPN connects after networking becomes "
            "available, and this app opens when you sign in. The current connection stays unchanged.")
        self.startup_toggle.connect("toggled", self._startup_toggled)
        startup_box.pack_start(self.startup_toggle, False, False, 0)
        self.startup_guard = Gtk.CheckButton(label="Enable VPN only after connecting (WireGuard)")
        self.startup_guard.set_tooltip_text("Optional protection for the startup connection. Early boot traffic can use the normal network until the VPN connects.")
        startup_box.pack_start(self.startup_guard, False, False, 0)
        self.startup_save = Gtk.Button(label="Save current location for startup")
        self.startup_save.set_tooltip_text("Save the selected country/service, city, server and protocol. Changes require Linux authorization.")
        self.startup_save.connect("clicked", lambda *_: self._change_startup(True))
        startup_box.pack_start(self.startup_save, False, False, 0)
        self.startup_detail = label("Checking startup settings…", "caption", wrap=True)
        startup_box.pack_start(self.startup_detail, False, False, 0)
        startup_box.pack_start(label("One-time authorization. Needs systemd and a CyberGhost account file available during boot.",
                                     "caption", wrap=True), False, False, 0)
        self.startup_expander.add(startup_box)
        content.pack_start(self.startup_expander, False, False, 0)

        command_box = box(spacing=6)
        self.command_label = label("Choose a location to see the command.", "command", wrap=True)
        self.command_label.set_selectable(True)
        command_box.pack_start(self.command_label, False, False, 0)
        self.copy_button = Gtk.Button(label="Copy command")
        self.copy_button.set_halign(Gtk.Align.START)
        self.copy_button.connect("clicked", self._copy_command)
        command_box.pack_start(self.copy_button, False, False, 0)
        command_expander = Gtk.Expander(label="Command preview")
        command_expander.add(command_box)
        content.pack_start(command_expander, False, False, 0)

        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log.set_left_margin(10)
        self.log.set_right_margin(10)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(140)
        log_scroll.set_max_content_height(200)
        log_scroll.add(self.log)
        self.log_expander = Gtk.Expander(label="Activity & CLI output")
        self.log_expander.add(log_scroll)
        content.pack_start(self.log_expander, False, False, 0)
        content.pack_start(label("Closing this window does not disconnect your VPN.", "caption", wrap=True), False, False, 0)

    def _submit(self, key, work, on_success, on_error=None):
        if not self.alive:
            return
        token = self._requests.get(key, 0) + 1
        self._requests[key] = token
        previous = self._futures.get(key)
        if previous is not None:
            previous.cancel()
        future = self._pool.submit(work)
        self._futures[key] = future

        def completed(task):
            if not self.alive or self._requests.get(key) != token:
                return False
            try:
                result = task.result()
            except Exception as error:
                (on_error or self._show_error)(error)
            else:
                on_success(result)
            return False

        future.add_done_callback(lambda task: GLib.idle_add(completed, task))

    def _save(self):
        try:
            self.preferences.save()
        except OSError as error:
            self._append_log(f"Could not save preferences: {error}")

    def _append_log(self, text):
        buffer = self.log.get_buffer()
        previous = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        buffer.set_text((previous + f"[{datetime.now():%H:%M:%S}] {text}\n")[-40000:])
        self.log.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 1)

    def _show_error(self, error):
        self.error_label.set_text(str(error))
        self.error_box.show_all()
        details = getattr(error, "details", "")
        self._append_log(str(error) + ("\n" + details if details else ""))

    def _mode_changed(self, *_):
        self.preferences.data["mode"] = self.mode.get_active_id()
        self._save()
        self.refresh_locations()

    def refresh(self):
        if self.control_busy or self._setup_running():
            return
        if not self.client.demo:
            self._accept_installation(inspect_installation())
        self._cache.clear()
        self.error_box.hide()
        self._invalidate_public_ip()
        self.refresh_startup()
        self.refresh_guard()
        self.refresh_locations()
        self.refresh_status()

    def refresh_locations(self):
        mode = self.mode.get_active_id()
        self._epoch += 1
        epoch = self._epoch
        if self._locations_retry is not None:
            GLib.source_remove(self._locations_retry)
            self._locations_retry = None
        # Invalidate an older request even if the replacement has to wait for
        # the startup service first. It must not repopulate a different mode.
        self._requests["locations"] = self._requests.get("locations", 0) + 1
        previous = self._futures.get("locations")
        if previous is not None:
            previous.cancel()
        self.location = None
        self.locations = []
        self.selected_title.set_text("Choose a location")
        self._reset_choices()
        self._render_locations()
        self.empty_list.set_text("Loading locations…")
        self.location_count.set_text("Contacting CyberGhost…")
        self._sync_controls()

        if not self.client.binary:
            self.empty_list.set_text("Open Setup to install the CyberGhost CLI.")
            self.location_count.set_text("CLI setup required")
            return

        deadline = None
        network_wait_logged = False

        def current():
            return self.alive and epoch == self._epoch

        def wait(message):
            self.empty_list.set_text(message)
            self.location_count.set_text("Waiting to load locations…")
            self._locations_retry = GLib.timeout_add_seconds(3, attempt)

        def loaded(locations):
            if not current():
                return
            self.locations = locations
            self._render_locations()
            self._append_log(f"Loaded {len(locations)} {mode} locations.")

        def failed(error):
            nonlocal network_wait_logged
            if not current():
                return
            if isinstance(error, NetworkUnavailable) and time.monotonic() < deadline:
                message = "Waiting for the network/DNS. Locations will load automatically."
                wait(message)
                if not network_wait_logged:
                    self._append_log(message)
                    network_wait_logged = True
                return
            self.empty_list.set_text("Locations unavailable. Use Refresh to try again.")
            self.location_count.set_text("Could not load locations")
            self._show_error(error)

        def attempt():
            nonlocal deadline
            self._locations_retry = None
            if not current():
                return False
            if not self._startup_ready or self._startup_pending or self.startup_state.running:
                wait("Waiting for the startup VPN check to finish…")
                return False
            if self.control_busy or self._setup_running():
                wait("Locations will load when the current operation finishes…")
                return False
            if deadline is None:
                deadline = time.monotonic() + 180
            self.empty_list.set_text("Loading locations…")
            self.location_count.set_text("Contacting CyberGhost…")
            self._submit("locations", lambda: self.client.locations(mode), loaded, failed)
            return False

        attempt()

    def _favorite_key(self, location):
        return self.mode.get_active_id() + ":" + location.key

    def _render_locations(self):
        if not hasattr(self, "location_list"):
            return
        self._rendering = True
        try:
            for child in self.location_list.get_children():
                self.location_list.remove(child)
            query = self.search.get_text().strip().casefold()
            favorites = self.preferences.data["favorites"]
            chosen = self.location.key if self.location else None
            saved = self.preferences.data["last_location"]
            selected_row = None
            for location in self.locations:
                favorite = self._favorite_key(location) in favorites
                if query not in (location.name + " " + location.code).casefold():
                    continue
                if self.favorites_only.get_active() and not favorite:
                    continue
                row = Gtk.ListBoxRow()
                row.location = location
                row_box = box(False, 12)
                row.add(row_box)
                row_box.pack_start(label(location.code, "country-code"), False, False, 0)
                title = label(location.name, "location-name")
                title.set_ellipsize(Pango.EllipsizeMode.END)
                title.set_max_width_chars(23)
                title.set_tooltip_text(location.name)
                row_box.pack_start(title, True, True, 0)
                if favorite:
                    row_box.pack_end(label("★", "status-symbol"), False, False, 0)
                self.location_list.add(row)
                if location.key == chosen or (chosen is None and self._favorite_key(location) == saved):
                    selected_row = row
            count = len(self.location_list.get_children())
            self.location_count.set_text(f"{count} of {len(self.locations)} locations")
            self.empty_list.set_text("No matching locations." if self.locations else "No locations loaded.")
            self.location_list.show_all()
            if selected_row is not None:
                self.location_list.select_row(selected_row)
        finally:
            self._rendering = False
        if selected_row is not None and self.location is None:
            self._location_selected(self.location_list, selected_row)

    def _location_selected(self, _list, row):
        if self._rendering or row is None or self.control_busy:
            return
        self._epoch += 1
        self.location = row.location
        self.preferences.data["last_location"] = self._favorite_key(self.location)
        self._save()
        self.selected_title.set_text(self.location.name)
        self.selected_title.set_tooltip_text(self.location.name)
        self._reset_choices()
        self._update_favorite()
        self._sync_controls()
        if self.mode.get_active_id() == "streaming":
            self.location_hint.set_text("CyberGhost selects the server optimized for this streaming service.")
        else:
            self._load_cities()

    def _update_favorite(self):
        self._updating_favorite = True
        favorite = self.location is not None and self._favorite_key(self.location) in self.preferences.data["favorites"]
        self.favorite_button.set_active(favorite)
        self.favorite_button.set_label("★" if favorite else "☆")
        self._updating_favorite = False

    def _favorite_changed(self, *_):
        if not self.location or getattr(self, "_updating_favorite", False):
            return
        key = self._favorite_key(self.location)
        favorites = self.preferences.data["favorites"]
        if self.favorite_button.get_active() and key not in favorites:
            favorites.append(key)
        elif not self.favorite_button.get_active() and key in favorites:
            favorites.remove(key)
        self._save()
        self._update_favorite()
        self._render_locations()

    def _reset_choices(self):
        self._changing_choices = True
        for widget, title in [(self.city, "Automatic city"), (self.server, "Automatic server")]:
            widget.remove_all()
            widget.append("", title)
            widget.set_active(0)
        self._changing_choices = False

    def _selection(self):
        return Selection(
            mode=self.mode.get_active_id(), country=self.location.code if self.location else "",
            service=self.location.service if self.location else "",
            city=self.city.get_active_id() or "", server=self.server.get_active_id() or "",
            protocol=self.protocol.get_active_id(), transport=self.transport.get_active_id(),
        )

    def _load_cities(self):
        epoch = self._epoch
        selection = self._selection()
        key = (selection.mode, selection.country)
        self.location_hint.set_text("Loading available cities…")

        def loaded(choices):
            if epoch != self._epoch:
                return
            self._cache[key] = choices
            for choice in choices:
                self.city.append(choice.value, choice.label)
            self.location_hint.set_text(f"{len(choices)} cities available. Automatic lets CyberGhost choose." if choices else "No city list returned; automatic selection is still available.")
            self._sync_controls()

        def failed(error):
            if epoch == self._epoch:
                self.location_hint.set_text("City list unavailable. You can still use automatic selection.")
                self._show_error(error)

        if key in self._cache:
            loaded(self._cache[key])
        else:
            self._submit("cities", lambda: self.client.cities(selection), loaded, failed)

    def _city_changed(self, *_):
        if self._changing_choices:
            return
        self._epoch += 1
        epoch = self._epoch
        self._changing_choices = True
        self.server.remove_all()
        self.server.append("", "Automatic server")
        self.server.set_active(0)
        self._changing_choices = False
        self._sync_controls()
        if not self.city.get_active_id():
            self.location_hint.set_text("Automatic city and server selection.")
            return
        selection = self._selection()
        self.location_hint.set_text("Loading servers for " + selection.city + "…")

        def loaded(choices):
            if epoch != self._epoch:
                return
            for choice in choices:
                self.server.append(choice.value, choice.label)
            self.location_hint.set_text(f"{len(choices)} servers available in {selection.city}. Load is a snapshot, not a speed test.")
            self._sync_controls()

        def failed(error):
            if epoch == self._epoch:
                self.location_hint.set_text("Server list unavailable. Automatic selection is still available.")
                self._show_error(error)

        self._submit("servers", lambda: self.client.servers(selection), loaded, failed)

    def _protocol_changed(self, *_):
        self.preferences.data.update(protocol=self.protocol.get_active_id(), transport=self.transport.get_active_id())
        self._save()
        self._sync_controls()

    def _sync_controls(self):
        busy = self.control_busy or self._setup_running() or not self._startup_ready or self.startup_state.running
        available = bool(self.client.binary)
        ready = self.client.demo or not self.installation.needs_setup
        self.connect_button.set_sensitive(not busy and available and ready and self.guard_state.state == "off"
                                          and self.location is not None and self.state.state == "disconnected")
        self.connect_button.set_tooltip_text(
            "Turn VPN only off before connecting or changing servers."
            if self.guard_state.state != "off" else
            "Wait for a confirmed disconnected status before connecting. Use Refresh to check again."
            if self.state.state == "unknown" else
            "Connect using the selected location. Linux will request administrator authorization.")
        self.disconnect_button.set_sensitive(not busy and available and self.state.state != "disconnected")
        self.setup_button.set_sensitive(not busy and not self.client.demo)
        self.mode.set_sensitive(not busy)
        self.location_list.set_sensitive(not busy)
        self.refresh_button.set_sensitive(not self.control_busy and not self._setup_running())
        self.protocol.set_sensitive(not busy)
        self.transport.set_sensitive(not busy and self.protocol.get_active_id() == "openvpn")
        custom = not busy and self.location is not None and self.mode.get_active_id() != "streaming"
        self.city.set_sensitive(custom)
        self.server.set_sensitive(custom and bool(self.city.get_active_id()))
        self.favorite_button.set_sensitive(self.location is not None)
        self.guard_toggle.set_sensitive(not busy and not self._guard_pending and
                                        (self.guard_state.state != "off" or
                                         (self.state.state == "connected" and self.protocol.get_active_id() == "wireguard")))
        startup_ready = not busy and self.startup_state.available and available and ready and self.location is not None
        self.startup_toggle.set_sensitive(not busy and self.startup_state.available and (self.startup_state.enabled or startup_ready))
        self.startup_save.set_sensitive(startup_ready and self.startup_state.enabled)
        self.startup_guard.set_sensitive(not busy and self.startup_state.available and self.protocol.get_active_id() == "wireguard")
        try:
            command = self._selection().command_preview()
        except ValueError:
            command = "Choose a location to see the command."
            self.copy_button.set_sensitive(False)
        else:
            self.copy_button.set_sensitive(True)
        self.command_label.set_text(command)
        app = self.get_application()
        if hasattr(app, "sync_tray"):
            app.sync_tray()

    def _copy_command(self, *_):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("sudo " + self._selection().command_preview(), -1)

    def _poll(self):
        if self.alive:
            self.refresh_startup()
            self.refresh_guard()
            self.refresh_status()
        return self.alive

    def refresh_status(self):
        if self.control_busy or self._setup_running() or not self.alive:
            return
        if not self.client.binary:
            self._requests["status"] = self._requests.get("status", 0) + 1
            self.status_pending = False
            self._apply_status(Status("unknown", "Open Setup to install the CyberGhost CLI and configure your account."))
            return
        if self.status_pending:
            return
        self.status_pending = True

        def loaded(status):
            self.status_pending = False
            if self.control_busy:
                return
            self._apply_status(status)

        def failed(error):
            self.status_pending = False
            if not self.control_busy:
                self._apply_status(Status("unknown", str(error)))

        self._submit("status", self.client.status, loaded, failed)

    def _apply_status(self, status):
        changed = status != self.state
        self.state = status
        titles = {"connected": "Connected", "disconnected": "Not connected", "unknown": "Status unavailable"}
        self.status_title.set_text(("Demo · " if self.client.demo else "") + titles[status.state])
        if not self.client.binary:
            self.status_title.set_text("Setup required")
        self.status_chip.set_text("●  " + ("VPN ACTIVE" if status.state == "connected" else "VPN DISCONNECTED" if status.state == "disconnected" else "STATUS UNKNOWN"))
        context = self.status_chip.get_style_context()
        for name in ["connected", "disconnected"]:
            context.remove_class(name)
        context.add_class(status.state)
        if status.state == "connected":
            message = status.detail + "\nDisconnect first to change the connected location."
        elif status.state == "disconnected":
            message = "Choose a location, then connect when you’re ready."
        else:
            message = status.detail[:300]
        self.status_detail.set_text(message)
        if changed:
            self._append_log(status.detail)
        self._sync_controls()
        self._refresh_public_ip()

    def _invalidate_public_ip(self):
        self._requests["public-ip"] = self._requests.get("public-ip", 0) + 1
        self._ip_pending = False
        self._ip_state = None
        self.current_ip = None
        self.current_ip_label.set_text("Waiting for connection…")
        self.current_ip_label.set_tooltip_text(None)

    def _refresh_public_ip(self):
        state = self.state.state
        if self.status_pending:
            return
        if self.control_busy or state == "unknown" or not self.client.binary:
            if self._ip_state is not None or self._ip_pending:
                self._invalidate_public_ip()
            if state == "unknown":
                self.current_ip_label.set_text("Waiting for status…")
            return
        if state == "disconnected" and self.guard_state.state != "off":
            self._invalidate_public_ip()
            self.current_ip_label.set_text("Blocked by VPN only" if self.guard_state.state == "on" else "Waiting for firewall…")
            return
        if self._ip_state == state:
            return
        self._ip_state = state
        self._ip_pending = True
        self.current_ip_label.set_text("Checking…")
        if state == "connected" and self.before_ip is None:
            self.before_ip_label.set_text("No baseline recorded")

        def loaded(value):
            self._ip_pending = False
            if self.control_busy or self.state.state != state:
                return
            self.current_ip = value
            self.current_ip_label.set_text(value.display)
            self.current_ip_label.set_tooltip_text(value.display)
            if state == "disconnected":
                self.before_ip = value
                self.before_ip_label.set_text(value.display)
                self.before_ip_label.set_tooltip_text(value.display)

        def failed(_error):
            self._ip_pending = False
            if not self.control_busy and self.state.state == state:
                self.current_ip = None
                self.current_ip_label.set_text("Unavailable · refresh to retry")
                self.current_ip_label.set_tooltip_text(None)

        if self.client.demo:
            loaded(PublicIP("198.51.100.24", "DE") if state == "connected" else PublicIP("203.0.113.42"))
        else:
            self._submit("public-ip", self.get_application().ip_lookup, loaded, failed)

    def refresh_guard(self):
        if self.control_busy or self._guard_pending or not self.alive:
            return
        guard = self.get_application().guard
        if guard.demo:
            self._apply_guard(guard.status())
            return
        self._guard_pending = True

        def loaded(value):
            self._guard_pending = False
            self._apply_guard(value)

        def failed(_error):
            loaded(GuardStatus("unknown", "Could not verify firewall protection. Refresh or turn VPN only off to recover."))

        self._submit("guard-status", guard.status, loaded, failed)

    def _apply_guard(self, status):
        self.guard_state = status
        self._setting_guard = True
        try:
            self.guard_toggle.set_active(status.state != "off")
            self.guard_toggle.set_inconsistent(status.state == "unknown")
        finally:
            self._setting_guard = False
        self.guard_label.set_text(status.detail)
        self._sync_controls()
        self._refresh_public_ip()

    def _guard_toggled(self, *_):
        if self._setting_guard:
            return
        enabled = self.guard_toggle.get_active()
        if self.control_busy or self._setup_running() or self.startup_state.running or not self._startup_ready or (enabled and
                (self.state.state != "connected" or self.protocol.get_active_id() != "wireguard")):
            self._apply_guard(self.guard_state)
            return
        self.control_busy = True
        self._requests["guard-status"] = self._requests.get("guard-status", 0) + 1
        self._requests["status"] = self._requests.get("status", 0) + 1
        self.status_pending = self._guard_pending = False
        self._invalidate_public_ip()
        self._apply_guard(self.guard_state)  # Keep the verified state until authorization finishes.
        self.spinner.show()
        self.spinner.start()
        self.error_box.hide()
        self.operation_note.set_text("Authorize the firewall change in the Linux dialog…")
        self._sync_controls()

        def finish(status=None):
            self.control_busy = False
            self.spinner.stop()
            self.spinner.hide()
            self.operation_note.set_text("Authorization is handled by Linux. No password is stored here.")
            if status is not None:
                self._apply_guard(status)
                self._append_log(status.detail)
            self.refresh_guard()
            self.refresh_status()

        def failed(error):
            self._show_error(error)
            finish()

        self._submit("guard-change", lambda: self.get_application().guard.change(enabled), finish, failed)

    def refresh_startup(self):
        if self.control_busy or self._startup_pending or not self.alive:
            return
        manager = self.get_application().startup
        if manager.demo:
            self._apply_startup(manager.inspect())
            return
        self._startup_pending = True

        def loaded(value):
            self._startup_pending = False
            self._apply_startup(value)

        def failed(error):
            self._startup_pending = False
            # Unverified boot state must not invite a second concurrent connect.
            self._startup_ready = False
            self.startup_detail.set_text(str(error))
            self._sync_controls()
            self.refresh_button.set_sensitive(not self.control_busy)

        self._submit("startup-status", manager.inspect, loaded, failed)

    def _apply_startup(self, state, *, reset_form=False):
        first = not self._startup_ready
        self._startup_ready = True
        self.startup_state = state
        self._setting_startup = True
        try:
            self.startup_toggle.set_active(state.enabled)
            if first or reset_form:
                self.startup_guard.set_active(state.vpn_only)
        finally:
            self._setting_startup = False
        self.startup_detail.set_text(state.detail)
        self._sync_controls()

    def _startup_toggled(self, *_):
        if not self._setting_startup:
            self._change_startup(self.startup_toggle.get_active())

    def _change_startup(self, enabled):
        if self.control_busy or self._setup_running() or self.startup_state.running or not self._startup_ready or not self.startup_state.available:
            self._apply_startup(self.startup_state)
            return
        try:
            selection = self._selection() if enabled else None
            if selection is not None:
                selection.connect_args()
        except ValueError as error:
            self._apply_startup(self.startup_state)
            self._show_error(error)
            return
        vpn_only = self.startup_guard.get_active() and self.protocol.get_active_id() == "wireguard"
        self.control_busy = True
        for key in ("startup-status", "status", "guard-status"):
            self._requests[key] = self._requests.get(key, 0) + 1
        self._startup_pending = self.status_pending = self._guard_pending = False
        self._apply_startup(self.startup_state)
        self.spinner.show()
        self.spinner.start()
        self.error_box.hide()
        self.operation_note.set_text("Authorize the startup settings in the Linux dialog…")

        def finish(state=None):
            self.control_busy = False
            self.spinner.stop()
            self.spinner.hide()
            self.operation_note.set_text("Authorization is handled by Linux. No password is stored here.")
            if state is not None:
                self._apply_startup(state, reset_form=True)
                self._append_log("Startup connection saved for the next boot." if state.enabled else "Startup connection disabled. Current VPN unchanged.")
            self.refresh_startup()
            self.refresh_guard()
            self.refresh_status()

        def failed(error):
            self._show_error(error)
            finish()

        manager = self.get_application().startup
        work = (lambda: manager.configure(selection, vpn_only)) if enabled else manager.disable
        self._submit("startup-change", work, finish, failed)

    def _control(self, action):
        if self.control_busy or self._setup_running() or self.startup_state.running or not self._startup_ready:
            return
        if action == "connect" and self.guard_state.state != "off":
            self._show_error(CliError("Turn VPN only off before connecting or changing servers."))
            return
        if not self.client.demo and (not self.client.binary or (action == "connect" and self.installation.needs_setup)):
            self._show_setup()
            return
        try:
            selection = self._selection()
            if action == "connect":
                selection.connect_args()
        except ValueError as error:
            self._show_error(error)
            return
        self.control_busy = True
        self._invalidate_public_ip()
        # A status request begun before this action must not overwrite its result.
        self._requests["status"] = self._requests.get("status", 0) + 1
        self.status_pending = False
        self.error_box.hide()
        self.spinner.show()
        self.spinner.start()
        self.status_title.set_text("Connecting…" if action == "connect" else "Disconnecting…")
        self._connection_action = action
        self.operation_note.set_text("Complete the Linux authorization dialog. Waiting for CyberGhost…")
        self._sync_controls()
        self._append_log(selection.command_preview() if action == "connect" else "cyberghostvpn --stop")

        def finish():
            self.control_busy = False
            self._connection_action = None
            self.spinner.stop()
            self.spinner.hide()
            self.operation_note.set_text("Authorization is handled by Linux. No password is stored here.")
            self._apply_status(Status("unknown", "Checking the result with CyberGhost…"))
            self.refresh_status()

        def completed(output):
            self._append_log(output or "Command finished; checking connection status.")
            finish()

        def failed(error):
            self._show_error(error)
            finish()

        work = (lambda: self.client.connect(selection)) if action == "connect" else self.client.disconnect
        self._submit("control", work, completed, failed)

    def _setup_running(self):
        return self.setup_dialog is not None and self.setup_dialog.busy

    def _accept_installation(self, installation):
        self.installation = installation
        self.client.binary = installation.cli
        self._sync_controls()

    def _initial_setup(self):
        self._setup_idle = None
        if self.alive and (not self._startup_ready or self.startup_state.running):
            self._setup_idle = GLib.timeout_add(250, self._initial_setup)
            return False
        if self.alive:
            self._show_setup()
        return False

    def _show_setup(self, *_):
        if self.client.demo or self.control_busy or self.startup_state.running or not self._startup_ready or not self.alive:
            return
        if self.setup_dialog is None:
            self.setup_dialog = SetupDialog(self)
            self.setup_dialog.connect("destroy", self._setup_closed)
        self.setup_dialog.present()

    def _setup_closed(self, *_):
        self.setup_dialog = None
        if self.alive:
            self.refresh()

    def _about(self, *_):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.CLOSE, text="CyberGhost Desktop · 0.1.0")
        dialog.format_secondary_text(
            "An independent, unofficial interface for the installed CyberGhost Linux CLI.\n\n"
            "Use Setup in the header to install the CLI or configure your account.\n\n"
            "Ctrl+F: search locations   ·   F5: refresh   ·   Ctrl+Q: quit\n\n"
            "The app stores only your choices and favorites. Linux handles administrator authorization. "
            "Quitting leaves your VPN connection as it is."
        )
        dialog.run()
        dialog.destroy()

    def _key_press(self, _widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and event.keyval in [Gdk.KEY_f, Gdk.KEY_F]:
            self.search.grab_focus()
            return True
        if ctrl and event.keyval in [Gdk.KEY_q, Gdk.KEY_Q]:
            self.get_application().request_quit()
            return True
        if event.keyval == Gdk.KEY_F5:
            self.refresh()
            return True
        return False

    def _delete(self, *_):
        if self._setup_running():
            self.setup_dialog.present()
            return True
        app = self.get_application()
        if app.tray is not None:
            self.hide()
            return True
        if self.control_busy:
            self._show_error(CliError("A VPN or firewall command is still running. Finish or cancel its authorization dialog before closing."))
            return True
        return False

    def _destroy(self, *_):
        self.alive = False
        if self._locations_retry is not None:
            GLib.source_remove(self._locations_retry)
            self._locations_retry = None
        if self._setup_idle is not None:
            GLib.source_remove(self._setup_idle)
        if self.setup_dialog is not None:
            self.setup_dialog.destroy()
        if getattr(self, "_timer", None):
            GLib.source_remove(self._timer)
        self._pool.shutdown(wait=False, cancel_futures=True)


class Application(Gtk.Application):
    def __init__(self, *, demo=False, tray=True, client=None, preferences=None, setup=False, ip_lookup=None, guard=None, startup=None):
        super().__init__(application_id="io.github.rvage.CyberGhostDesktop" + (".Demo" if demo else ""),
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.client = client or (DemoClient() if demo else CyberGhostClient())
        self.preferences = preferences or Preferences(persistent=not demo)
        self.want_tray = tray
        self.setup_on_start = setup
        self.ip_lookup = ip_lookup or fetch_public_ip
        self.guard = guard or (DemoGuard() if demo else VPNGuard())
        self.startup = startup or (DemoStartup() if demo else StartupManager())
        self.tray = None
        self._tray_appearance = None
        self.window = None

    def do_activate(self):
        if self.window is None or not self.window.alive:
            Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_name("style.css")))
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.window = MainWindow(self, self.client, self.preferences)
            if self.want_tray:
                self._create_tray()
        self.window.show()
        self.window.present()

    def _create_tray(self):
        for namespace in ["AyatanaAppIndicator3", "AppIndicator3"]:
            try:
                gi.require_version(namespace, "0.1")
                from importlib import import_module
                indicator = import_module("gi.repository." + namespace)
            except (ValueError, ImportError):
                continue
            menu = Gtk.Menu()
            self.tray_status = Gtk.MenuItem(label="CyberGhost Desktop")
            self.tray_status.set_sensitive(False)
            menu.append(self.tray_status)
            show = Gtk.MenuItem(label="Open CyberGhost Desktop")
            show.connect("activate", lambda *_: self.activate())
            menu.append(show)
            self.tray_disconnect = Gtk.MenuItem(label="Disconnect VPN")
            self.tray_disconnect.connect("activate", lambda *_: self.window._control("disconnect"))
            menu.append(self.tray_disconnect)
            menu.append(Gtk.SeparatorMenuItem())
            quit_item = Gtk.MenuItem(label="Quit (keep VPN connection)")
            quit_item.connect("activate", lambda *_: self.request_quit())
            menu.append(quit_item)
            menu.show_all()
            tray_id = "cyberghost-desktop" + ("-demo" if self.client.demo else "")
            self.tray = indicator.Indicator.new(tray_id, "cyberghost-desktop-unknown", indicator.IndicatorCategory.APPLICATION_STATUS)
            self.tray.set_icon_theme_path(str(Path(__file__).resolve().with_name("icons")))
            self._tray_appearance = None
            self.tray.set_title("CyberGhost Desktop")
            self.tray.set_menu(menu)
            self.tray.set_status(indicator.IndicatorStatus.ACTIVE)
            self.hold()
            self.sync_tray()
            break

    def sync_tray(self):
        if self.tray is not None and self.window:
            state = self.window.state.state
            description = {"connected": "Connected", "disconnected": "Disconnected", "unknown": "Status unknown"}[state]
            if self.window._connection_action is not None:
                state = "unknown"
                description = "Connecting…" if self.window._connection_action == "connect" else "Disconnecting…"
            elif self.window.startup_state.running:
                state, description = "unknown", "Starting VPN…"
            description = ("Demo · " if self.client.demo else "") + "CyberGhost Desktop · " + description
            appearance = (state, description)
            if self._tray_appearance != appearance:
                self.tray.set_icon_full("cyberghost-desktop-" + state, description)
                self.tray.set_title(description)
                self._tray_appearance = appearance
            self.tray_status.set_label(description)
            self.tray_disconnect.set_sensitive(self.window.disconnect_button.get_sensitive())

    def request_quit(self):
        if self.window and self.window._setup_running():
            self.window.setup_dialog.present()
            return
        if self.window and self.window.control_busy:
            self.activate()
            self.window._show_error(CliError("Finish or cancel the active authorization/connection request before quitting."))
            return
        if self.window:
            self.window.destroy()
        self.quit()
