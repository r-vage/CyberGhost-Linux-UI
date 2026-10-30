"""A bounded server dropdown for catalogs too large for a GTK menu."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402


class ServerPicker(Gtk.Box):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.button = Gtk.MenuButton()
        self.button.get_style_context().add_class("server-picker")
        self.pack_start(self.button, True, True, 0)
        self.set_hexpand(True)
        self._model = Gtk.ListStore(str, str)  # Display text, CLI value.
        self._active_id = None
        self._label = Gtk.Label(label="Automatic server", xalign=0)
        self._label.set_ellipsize(Pango.EllipsizeMode.END)
        self._label.set_max_width_chars(24)
        contents = Gtk.Box(spacing=8)
        contents.pack_start(self._label, True, True, 0)
        contents.pack_end(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU), False, False, 0)
        self.button.add(contents)

        self.popover = Gtk.Popover(relative_to=self.button)
        self.popover.get_style_context().add_class("server-menu")
        self.popover.set_constrain_to(Gtk.PopoverConstraint.WINDOW)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_overlay_scrolling(False)
        self.scroll.set_max_content_height(280)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_border_width(6)
        self.tree = Gtk.TreeView(model=self._model)
        self.tree.set_headers_visible(False)
        self.tree.set_search_column(0)
        self.tree.set_activate_on_single_click(True)
        renderer = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END, max_width_chars=36, ypad=7)
        column = Gtk.TreeViewColumn("Server", renderer, text=0)
        column.set_expand(True)
        self.tree.append_column(column)
        self.tree.set_tooltip_column(0)
        self.tree.connect("row-activated", self._selected)
        self.scroll.add(self.tree)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.pack_start(self.scroll, True, True, 0)
        self.status_box = Gtk.Box(spacing=8)
        self.status_box.set_border_width(8)
        self.status_box.set_no_show_all(True)
        self.status_spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_max_width_chars(36)
        self.status_label.get_style_context().add_class("caption")
        self.status_box.pack_start(self.status_spinner, False, False, 0)
        self.status_box.pack_start(self.status_label, True, True, 0)
        panel.pack_start(self.status_box, False, False, 0)
        self.popover.add(panel)
        panel.show_all()
        self.status_label.show()
        self.popover.connect("show", self._show_choices)
        self.button.set_popover(self.popover)

    def append(self, key, text):
        self._model.append([text, key])

    def set_status(self, text="", *, loading=False):
        self.status_label.set_text(text)
        self.status_label.set_tooltip_text(text or None)
        self.status_box.set_visible(bool(text))
        self.status_spinner.set_visible(loading)
        if loading:
            self.status_spinner.start()
        else:
            self.status_spinner.stop()

    def replace(self, choices):
        """Refresh rows without resetting a still-valid user selection."""
        previous = self._active_id
        self._model.clear()
        for key, text in choices:
            self.append(key, text)
        if not self.set_active_id(previous):
            self.set_active(0)

    def remove_all(self):
        previous = self._active_id
        self._active_id = None
        self._model.clear()
        self._label.set_text("Automatic server")
        if previous is not None:
            self.emit("changed")

    def get_model(self):
        return self._model

    def get_active_id(self):
        return self._active_id

    def set_active_id(self, key):
        for index, row in enumerate(self._model):
            if row[1] == key:
                self.set_active(index)
                return True
        return False

    def set_active(self, index):
        # Selection is separate from the menu button's open/closed state.
        if 0 <= index < len(self._model):
            row = self._model[index]
            key, text = row[1], row[0]
        else:
            key, text = None, "Automatic server"
        changed = key != self._active_id
        self._active_id = key
        self._label.set_text(text)
        if changed:
            self.emit("changed")

    def _show_choices(self, _popover):
        self.popover.set_size_request(max(240, self.get_allocated_width()), -1)
        for index, row in enumerate(self._model):
            if row[1] == self._active_id:
                path = Gtk.TreePath.new_from_indices([index])
                self.tree.set_cursor(path)
                self.tree.scroll_to_cell(path, None, False, 0, 0)
                break
        self.tree.grab_focus()

    def _selected(self, _tree, path, _column):
        self.set_active(path.get_indices()[0])
        self.popover.popdown()
        self.button.grab_focus()
