"""Guided first-run setup; credentials stay in CyberGhost's own terminal UI."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import authorization, setup_tasks  # noqa: E402
from .linux_support import dependency_help, distribution_name  # noqa: E402


def text_label(text="", style=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(75)
    if style:
        widget.get_style_context().add_class(style)
    return widget


class SetupDialog(Gtk.Dialog):
    def __init__(self, owner):
        super().__init__(title="Set up CyberGhost", transient_for=owner, modal=True, destroy_with_parent=True)
        self.owner = owner
        self.busy = False
        self.get_style_context().add_class("cyberghost")
        self.set_default_size(720, 650)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.close_button = self.get_widget_for_response(Gtk.ResponseType.CLOSE)
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_: self.busy)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.get_content_area().pack_start(scroll, True, True, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, border_width=22)
        scroll.add(content)
        content.pack_start(text_label("Get your VPN ready", "heading"), False, False, 0)
        content.pack_start(text_label("You can install the official CyberGhost CLI here after downloading it from "
                                      "your account. An active CyberGhost subscription is required.", "muted"), False, False, 0)

        status_card = self._card(content, "ON THIS COMPUTER")
        status_card.pack_start(text_label(distribution_name(), "muted"), False, False, 0)
        self.cli_status = text_label()
        self.account_status = text_label()
        self.auth_status = text_label()
        for widget in (self.cli_status, self.account_status, self.auth_status):
            status_card.pack_start(widget, False, False, 0)
        self.check_button = Gtk.Button(label="Check again")
        self.check_button.set_halign(Gtk.Align.START)
        self.check_button.connect("clicked", lambda *_: self.recheck())
        status_card.pack_start(self.check_button, False, False, 0)

        download = self._card(content, "1 · DOWNLOAD THE LINUX CLI")
        download.pack_start(text_label("In your CyberGhost account, open Download Hub → Linux → Configure, "
                                       "choose your distribution, and download the app. Extract the ZIP first.", "muted"), False, False, 0)
        links = Gtk.Box(spacing=10)
        links.pack_start(Gtk.LinkButton.new_with_label(setup_tasks.ACCOUNT_URL, "Open CyberGhost account"), False, False, 0)
        links.pack_start(Gtk.LinkButton.new_with_label(setup_tasks.INSTALL_GUIDE_URL, "Official guide"), False, False, 0)
        download.pack_start(links, False, False, 0)

        install = self._card(content, "2 · INSTALL THE DOWNLOADED CLI")
        install.pack_start(text_label("Choose install.sh inside the extracted official download. It runs in a "
                                      "terminal with administrator privileges; follow its prompts there.", "muted"), False, False, 0)
        self.install_button = Gtk.Button(label="Choose installer and run…")
        self.install_button.set_halign(Gtk.Align.START)
        self.install_button.connect("clicked", self._choose_installer)
        install.pack_start(self.install_button, False, False, 0)

        account = self._card(content, "3 · CONFIGURE YOUR ACCOUNT")
        account.pack_start(text_label("The installer normally configures your account too. Use this only if setup "
                                      "is still needed or you want to change the account. Passwords stay in the "
                                      "official CLI's terminal.", "muted"), False, False, 0)
        self.account_button = Gtk.Button(label="Configure account in terminal…")
        self.account_button.set_halign(Gtk.Align.START)
        self.account_button.connect("clicked", lambda *_: self._start("account"))
        account.pack_start(self.account_button, False, False, 0)

        retention = self._card(content, "OPTIONAL · REMEMBER AUTHORIZATION")
        retention.pack_start(text_label("Linux can remember VPN authorization briefly, typically about five minutes. "
                                         "Connect, Disconnect and VPN-only changes then reuse it. Passwords stay in the Linux dialog. "
                                         "Setup installs a fixed, root-owned helper and an app-specific polkit policy.", "muted"), False, False, 0)
        self.retention_status = text_label("", "muted")
        retention.pack_start(self.retention_status, False, False, 0)
        actions = Gtk.Box(spacing=10)
        self.retention_enable = Gtk.Button(label="Enable")
        self.retention_disable = Gtk.Button(label="Remove")
        self.retention_enable.connect("clicked", lambda *_: self._configure_retention(True))
        self.retention_disable.connect("clicked", lambda *_: self._configure_retention(False))
        actions.pack_start(self.retention_enable, False, False, 0)
        actions.pack_start(self.retention_disable, False, False, 0)
        retention.pack_start(actions, False, False, 0)

        manual = Gtk.Expander(label="Manual commands and missing dependencies")
        instructions = text_label(
            "From the extracted CyberGhost download folder:\nsudo bash install.sh\n\n"
            "To configure an already installed CLI:\nsudo cyberghostvpn --setup\n\n"
            "Setup needs Bash, sudo and a terminal (GNOME Terminal, Ptyxis, Konsole, Xfce Terminal or xterm). "
            "Connection buttons need pkexec and your desktop's polkit authentication agent. "
            "Install missing tools through your distribution's package manager.\n\n" + dependency_help(), "command")
        instructions.set_selectable(True)
        manual.add(instructions)
        content.pack_start(manual, False, False, 0)
        self.notice = text_label("", "muted")
        content.pack_start(self.notice, False, False, 0)
        self.recheck()
        self.show_all()

    @staticmethod
    def _card(parent, title):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("settings-card")
        card.pack_start(text_label(title, "eyebrow"), False, False, 0)
        parent.pack_start(card, False, False, 0)
        return card

    def recheck(self):
        if self.busy:
            return
        self.retention = authorization.status()
        self.retention_status.set_text(self.retention.detail)
        self.installation = setup_tasks.inspect_installation()
        state = self.installation
        self.cli_status.set_text(f"CLI installed · {state.cli}" if state.cli else "CLI not installed")
        descriptions = {"present": "Account configuration found", "missing": "Account setup needed",
                        "inaccessible": "Account configuration cannot be checked; check file permissions"}
        self.account_status.set_text(descriptions[state.config_state])
        self.account_status.set_tooltip_text(str(state.config_path))
        self.auth_status.set_text("Connection authorization available" if state.pkexec else "Connection authorization missing · install pkexec / polkit")
        self.owner._accept_installation(state)
        self._sync()
        if not state.needs_setup:
            self.notice.set_text("Setup files are present. Close this screen to choose a location. Account access is checked when connecting.")
        elif not state.terminal or not state.sudo:
            self.notice.set_text("A setup terminal or sudo is missing. See the manual commands below.")
        else:
            self.notice.set_text("After setup finishes, press Enter in the terminal, then check the status here. Nothing connects automatically.")

    def _configure_retention(self, enabled):
        if self.busy or self.owner.control_busy:
            return
        self.busy = True
        self.notice.set_text("Authorize the authorization-helper change in the Linux dialog…")
        self._sync()
        self.owner._sync_controls()
        def finished(_result=None):
            self.busy = False
            self.recheck()
            self.owner._sync_controls()
        def failed(error):
            finished()
            self.notice.set_text(str(error))
            self.owner._append_log(str(error))
        self.owner._submit("authorization-setup", lambda: authorization.configure(enabled), finished, failed)

    def _sync(self):
        state = self.installation
        self.retention_enable.set_label("Update" if self.retention.state in {"outdated", "unavailable"} else "Enable")
        self.retention_enable.set_sensitive(not self.busy and bool(state.pkexec) and self.retention.state != "ready")
        self.retention_disable.set_sensitive(not self.busy and bool(state.pkexec) and self.retention.state != "off")
        usable = bool(state.terminal and state.sudo) and not self.busy
        self.install_button.set_sensitive(usable)
        self.account_button.set_sensitive(usable and bool(state.cli))
        self.check_button.set_sensitive(not self.busy)
        self.close_button.set_sensitive(not self.busy)
        self.owner._sync_controls()

    def _choose_installer(self, *_):
        if self.busy:
            return
        picker = Gtk.FileChooserDialog(title="Choose the official CyberGhost install.sh", transient_for=self,
                                       modal=True, action=Gtk.FileChooserAction.OPEN)
        picker.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Run installer", Gtk.ResponseType.ACCEPT)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("CyberGhost installer (install.sh)")
        file_filter.add_pattern("install.sh")
        picker.add_filter(file_filter)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            picker.set_current_folder(downloads)
        selected = None
        try:
            if picker.run() == Gtk.ResponseType.ACCEPT:
                selected = picker.get_filename()
        finally:
            picker.destroy()
        if selected:
            self._start("install", selected)

    def _start(self, action, installer=None):
        if self.busy:
            return
        try:
            setup_tasks.terminal_command(action, installer)
        except (setup_tasks.SetupError, OSError) as error:
            self.notice.set_text(str(error))
            return
        self.busy = True
        self._sync()
        self.notice.set_text("Finish the prompts in the setup terminal, then press Enter to close it. This screen will check again.")

        def finished(error=None):
            self.busy = False
            self.recheck()
            if error:
                self.notice.set_text(str(error))

        self.owner._submit("setup", lambda: setup_tasks.run_in_terminal(action, installer),
                           lambda _result: finished(), finished)

    def _response(self, *_):
        if not self.busy:
            self.destroy()
