# Exercise normal personal-file startup in a temporary folder with fake VPN backends.

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberghost_desktop.app import Application, Gio  # noqa: E402
from cyberghost_desktop.cli import DemoClient  # noqa: E402
from cyberghost_desktop.locale_store import atomic_write, json_bytes  # noqa: E402
from cyberghost_desktop.preferences import Preferences  # noqa: E402
from cyberghost_desktop.startup import DemoStartup  # noqa: E402
from cyberghost_desktop.vpn_guard import DemoGuard  # noqa: E402


def external_action(*_args, **_kwargs):
    raise AssertionError("Personal-language tests must not invoke real system commands")


with (tempfile.TemporaryDirectory() as directory,
      patch("subprocess.run", side_effect=external_action),
      patch("subprocess.Popen", side_effect=external_action)):
    path = Path(directory) / "preferences.json"
    personal = path.parent / "locales"
    atomic_write(personal / "nl.json", json_bytes({"language": "nl", "name": "Nederlands", "messages": {"Connect": "Verbinden"}}))
    (personal / "de.json").write_text("unfinished edit")
    saved = Preferences(path)
    saved.data["language"] = "nl"
    saved.save()
    app = Application(tray=False, client=DemoClient(), preferences=Preferences(path), guard=DemoGuard(), startup=DemoStartup())
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None)
    try:
        app.activate()
        assert app.window.language.get_active_id() == "nl"
        assert app.window.connect_button.get_label() == "Verbinden"
        assert (personal / ".manifest.json").exists()
        assert "Disconnect" in (personal / "nl.json").read_text(), "New keys reach personal-only languages at startup"
        assert (personal / "de.json").read_text() == "unfinished edit"
        buffer = app.window.log.get_buffer()
        assert "de.json" in buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
    finally:
        app.window.destroy()
        app.quit()

print("GTK personal-language startup passed: discovery, saved choice, new keys, malformed-file preservation and diagnostics.")
