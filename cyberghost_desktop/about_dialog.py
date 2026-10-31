# App information and an explicit, asynchronous update check.

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .i18n import message as msg  # noqa: E402
from .setup_dialog import text_label  # noqa: E402
from .self_update import GIT_ERROR, GIT_REQUIRED, InstallError, checkout_available, install_update  # noqa: E402
from .updates import NETWORK_ERROR, UPDATE_GUIDE_URL, UpdateError, check_for_updates  # noqa: E402

ABOUT_TEXT = (
    "An independent, unofficial interface for the installed CyberGhost Linux CLI.\n\n"
    "Use Setup in the header to install the CLI or configure your account.\n\n"
    "Ctrl+F: search locations   ·   F5: refresh   ·   Ctrl+Q: quit\n\n"
    "The app stores only your choices and favorites. Linux handles administrator authorization. "
    "Quitting leaves your VPN connection as it is."
)


class AboutDialog(Gtk.Dialog):
    def __init__(self, owner):
        super().__init__(transient_for=owner, modal=True, destroy_with_parent=True)
        self.owner, self.ui = owner, owner.ui
        self.alive, self.busy = True, False
        self.available_version = None
        self.can_install = checkout_available()
        self.ui.set(self, "title", "About and updates")
        self.get_style_context().add_class("cyberghost")
        self.set_default_size(620, 620)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.close_button = self.get_widget_for_response(Gtk.ResponseType.CLOSE)
        self.ui.set(self.close_button, "label", "Close")
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_: self.owner.update_busy)
        self.connect("destroy", self._destroyed)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.get_content_area().pack_start(scroll, True, True, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, border_width=22)
        scroll.add(content)
        content.pack_start(self._label("CyberGhost Desktop", "heading"), False, False, 0)
        self.current_version = self._label(msg("Current version: {version}", version=__version__))
        content.pack_start(self.current_version, False, False, 0)

        updates = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        updates.get_style_context().add_class("settings-card")
        content.pack_start(updates, False, False, 0)
        updates.pack_start(self._label("Check for updates, install them, then restart the app. Your VPN connection stays active.", "muted"), False, False, 0)
        self.latest_version = self._label("")
        updates.pack_start(self.latest_version, False, False, 0)
        self.status = self._label("Demo mode: update checks are disabled." if owner.client.demo else "Not checked yet")
        updates.pack_start(self.status, False, False, 0)
        self.check_button = self.ui(Gtk.Button(), label="Check for updates")
        self.check_button.set_halign(Gtk.Align.START)
        self.check_button.connect("clicked", self._check)
        actions = Gtk.Box(spacing=10)
        updates.pack_start(actions, False, False, 0)
        actions.pack_start(self.check_button, False, False, 0)
        self.install_button = self.ui(Gtk.Button(), label="Install update")
        self.install_button.connect("clicked", self._install)
        actions.pack_start(self.install_button, False, False, 0)
        self.restart_button = self.ui(Gtk.Button(), label="Restart app")
        self.restart_button.connect("clicked", lambda *_: self.owner.get_application().request_restart())
        actions.pack_start(self.restart_button, False, False, 0)
        self.install_notice = self._label(GIT_REQUIRED, "muted")
        updates.pack_start(self.install_notice, False, False, 0)
        self.update_link = self.ui(Gtk.LinkButton(uri=UPDATE_GUIDE_URL), label="Update instructions")
        self.update_link.set_halign(Gtk.Align.START)
        updates.pack_start(self.update_link, False, False, 0)
        content.pack_start(self._label(ABOUT_TEXT, "muted"), False, False, 0)
        self.show_all()
        self.latest_version.hide()
        if owner.update_ready:
            self.ui.set(self.status, "label", msg("Update installed: {version}.", version=owner.update_version)
                        if owner.update_version else "Restart the app before using VPN controls.")
        self.sync_actions()

    def _label(self, text, style=None):
        return self.ui(text_label("", style), label=text)

    def _check(self, *_):
        if not self.alive or self.busy or self.owner.client.demo or self.owner.update_ready:
            return
        self.busy = True
        self.available_version = None
        self.sync_actions()
        self.latest_version.hide()
        self.ui.set(self.status, "label", "Checking for updates…")
        self.owner._submit("app-update", check_for_updates, self._checked, self._failed)

    def _checked(self, result):
        if not self.alive:
            return
        self.busy = False
        self.available_version = result.latest_version if result.state == "available" else None
        self.ui.set(self.latest_version, "label", msg("Latest version: {version}", version=result.latest_version))
        self.latest_version.show()
        states = {
            "available": msg("Update available: {version}.", version=result.latest_version),
            "current": "You’re up to date.",
            "ahead": "This version is newer than the published version.",
        }
        self.ui.set(self.status, "label", states[result.state])
        self.sync_actions()

    def _failed(self, error):
        if not self.alive:
            return
        self.busy = False
        self.ui.set(self.status, "label", str(error) if isinstance(error, UpdateError) else NETWORK_ERROR)
        self.sync_actions()

    def sync_actions(self):
        if not self.alive:
            return
        owner = self.owner
        enabled = not self.busy and not owner.client.demo and not owner.update_busy and not owner.update_ready
        self.check_button.set_sensitive(enabled)
        self.install_button.set_visible(self.available_version is not None and not owner.update_ready)
        self.install_button.set_sensitive(enabled and self.can_install and not owner.control_busy
                                          and not owner._setup_running() and owner._startup_ready
                                          and not owner.startup_state.running)
        self.restart_button.set_visible(owner.update_ready)
        self.restart_button.set_sensitive(owner.update_ready and not owner.update_busy)
        self.install_notice.set_visible(not self.can_install and not owner.update_ready)
        self.close_button.set_sensitive(not owner.update_busy)

    def _install(self, *_):
        if not self.alive or self.busy or not self.available_version or self.owner.client.demo or self.owner.update_ready:
            return
        if not self.can_install:
            self.ui.set(self.status, "label", GIT_REQUIRED)
            return
        if not self.owner.begin_update():
            self.ui.set(self.status, "label", "Finish the current VPN or setup operation before installing an update.")
            return
        self.busy = True
        self.sync_actions()
        self.ui.set(self.status, "label", "Installing update… Keep the app open.")
        version = self.available_version
        directory = self.owner.get_application().locale_directory
        self.owner._submit("app-install", lambda: install_update(version, locale_directory=directory),
                           self._installed, self._install_failed)

    def _installed(self, result):
        self.busy = False
        self.owner.finish_update(version=result.version, restart_required=True)
        if self.alive:
            self.ui.set(self.status, "label", msg("Update installed: {version}.", version=result.version))
            self.sync_actions()

    def _install_failed(self, error):
        self.busy = False
        self.owner.finish_update(restart_required=isinstance(error, InstallError) and error.restart_required)
        description = str(error) if isinstance(error, UpdateError) else GIT_ERROR
        self.owner._append_log(description)
        if getattr(error, "details", ""):
            self.owner._append_log(error.details)
        if self.alive:
            self.ui.set(self.status, "label", description)
            self.sync_actions()

    def _response(self, *_):
        if not self.owner.update_busy:
            self.destroy()

    def _destroyed(self, *_):
        self.alive = False
