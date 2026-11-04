import json
from pathlib import Path
import tempfile
import unittest

from cyberghost_desktop.preferences import Preferences
from cyberghost_desktop.cli import Choice, Location


class PreferenceTests(unittest.TestCase):
    def test_internet_blocking_defaults_on_and_explicit_opt_out_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            prefs = Preferences(path)
            self.assertTrue(prefs.data["vpn_only"])
            prefs.data["vpn_only"] = False
            prefs.save()
            self.assertFalse(Preferences(path).data["vpn_only"])
            path.write_text('{"vpn_only": "false"}')
            self.assertTrue(Preferences(path).data["vpn_only"])

    def test_saved_city_and_server_lists_survive_restart_including_empty_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            prefs = Preferences(path)
            cities = [Choice("Berlin", "Berlin · 20 servers")]
            servers = [Choice("berlin-1", "berlin-1 · 40% load", 40)]
            prefs.remember_choices(("traffic", "DE"), cities)
            prefs.remember_choices(("servers", "traffic", "DE", "Berlin"), servers)
            prefs.remember_choices(("torrent", "FR"), [])
            prefs.save()
            self.assertEqual(Preferences(path).choice_cache(), {
                ("traffic", "DE"): cities, ("servers", "traffic", "DE", "Berlin"): servers, ("torrent", "FR"): []})
            prefs.data["choice_catalogues"]['["traffic","--stop"]'] = []
            prefs.data["choice_catalogues"]['["traffic","FR"]'] = [{"value": "--stop", "label": "Bad", "load": None}]
            prefs.data["choice_catalogues"]['["traffic",null]'] = []
            prefs.save()
            cache = Preferences(path).choice_cache()
            self.assertEqual(len(cache), 3)
            prefs.data["last_location"] = "traffic:DE:"
            self.assertFalse(prefs.has_locations("traffic"), "A single saved country is not a complete catalog")

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

    def test_malformed_option_types_do_not_prevent_opening_the_app(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            for key in ("mode", "protocol", "transport"):
                for value in ([], {}, None, 123):
                    with self.subTest(key=key, value=value):
                        path.write_text(json.dumps({key: value, "vpn_only": False, "favorites": ["traffic:DE:"]}))
                        prefs = Preferences(path)
                        self.assertEqual(prefs.data[key], {"mode": "traffic", "protocol": "wireguard", "transport": "UDP"}[key])
                        self.assertFalse(prefs.data["vpn_only"], "Keep valid explicit preferences")
                        self.assertEqual(prefs.data["favorites"], ["traffic:DE:"])


if __name__ == "__main__":
    unittest.main()
