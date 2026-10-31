# Bind display text to its English source so a language change needs no restart.

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class UIStrings:
    def __init__(self, translator):
        self.translator = translator
        self._properties = {}
        self._refreshers = {}

    def _track(self, widget):
        if widget not in self._properties:
            self._properties[widget] = {}
            widget.connect("destroy", self._forget)

    def _forget(self, widget):
        self._properties.pop(widget, None)
        self._refreshers.pop(widget, None)

    def __call__(self, widget, **properties):
        for name, value in properties.items():
            self.set(widget, name.replace("_", "-"), value)
        return widget

    def set(self, widget, name, value):
        self._track(widget)
        self._properties[widget][name] = value
        widget.set_property(name, self.translator.render(value))

    def watch(self, widget, refresh):
        self._track(widget)
        self._refreshers[widget] = refresh

    def refresh(self):
        for refresh in list(self._refreshers.values()):
            refresh()
        for widget, properties in list(self._properties.items()):
            for name, value in properties.items():
                widget.set_property(name, self.translator.render(value))


class LocalizedCombo(Gtk.ComboBoxText):
    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.sources = {}
        ui.watch(self, self.retranslate)

    def append(self, key, text):
        self.sources[key] = text
        super().append(key, self.ui.translator.render(text))

    def remove_all(self):
        self.sources.clear()
        super().remove_all()

    def retranslate(self):
        for row in self.get_model():
            row[0] = self.ui.translator.render(self.sources[row[1]])
