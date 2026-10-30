import json
from pathlib import Path
import tempfile
import unittest

from cyberghost_desktop.preferences import Preferences
from cyberghost_desktop.cli import Location


class PreferenceTests(unittest.TestCase):
    def test_only_preferences_are_persisted_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings/preferences.json"
            prefs = Preferences(path)
            prefs.data.update(protocol="openvpn", favorites=["traffic:DE:"], last_location="traffic:DE:")
            prefs.save()
            self.assertEqual(Preferences(path).data, prefs.data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_demo_does_not_read_or_write_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text(json.dumps({"protocol": "openvpn"}))
            before = path.read_bytes()
            prefs = Preferences(path, persistent=False)
            self.assertEqual(prefs.data["protocol"], "wireguard")
            prefs.save()
            self.assertEqual(path.read_bytes(), before)

    def test_offline_locations_survive_restart_and_old_versions_have_a_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            prefs = Preferences(path)
            prefs.data["last_location"] = "traffic:DE:"
            self.assertEqual(prefs.offline_locations("traffic")[0].code, "DE")
            self.assertEqual(prefs.offline_locations("torrent"), [])
            locations = [Location("Germany", "DE"), Location("France", "FR")]
            prefs.remember_locations("traffic", locations)
            prefs.save()
            self.assertEqual(Preferences(path).offline_locations("traffic"), locations)
            data = json.loads(path.read_text())
            data["location_catalogues"]["traffic"] = [{"name": "Bad", "code": "--stop", "service": ""}, None]
            path.write_text(json.dumps(data))
            self.assertEqual(Preferences(path).offline_locations("traffic")[0].code, "DE")

    def test_malformed_preferences_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            for text in ["invalid json", "[]", '{"favorites": [12, null], "protocol": "invalid"}']:
                path.write_text(text)
                self.assertEqual(Preferences(path).data["favorites"], [])
                self.assertEqual(Preferences(path).data["protocol"], "wireguard")


if __name__ == "__main__":
    unittest.main()
