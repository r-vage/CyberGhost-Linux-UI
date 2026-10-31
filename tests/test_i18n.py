import json
from pathlib import Path
import tempfile
import unittest

from cyberghost_desktop.i18n import Translator, message
from cyberghost_desktop.linux_support import dependency_help
from cyberghost_desktop.preferences import Preferences


class TranslationTests(unittest.TestCase):
    def catalog(self, directory, code="fr", name="Français", messages=None):
        path = Path(directory) / f"{code}.json"
        path.write_text(json.dumps({"language": code, "name": name, "messages": messages or {}}, ensure_ascii=False), encoding="utf-8")
        return path

    def test_bundled_catalogs_are_complete_and_preserve_parameters(self):
        translator = Translator()
        self.assertEqual(translator.errors, [])
        english = translator.catalogs["en"].messages
        self.assertGreater(len(english), 200)
        for code, catalog in translator.catalogs.items():
            with self.subTest(language=code):
                translator.set_language(code)
                self.assertEqual(set(catalog.messages), set(english))
                text = translator.render(message("{count} of {total} locations", count=3, total=8))
                self.assertIn("3", text)
                self.assertIn("8", text)
        translator.set_language("de")
        self.assertEqual(translator.text("Connect"), "Verbinden")
        translator.set_language("zh-CN")
        self.assertEqual(translator.text("Connect"), "连接")

    def test_setup_commands_are_preserved_in_every_language(self):
        translator = Translator()
        for source in translator.catalogs["en"].messages:
            commands = [line for line in source.splitlines() if line.startswith(("sudo ", "zypper "))]
            if not commands:
                continue
            for code in translator.catalogs:
                with self.subTest(language=code, source=source):
                    translator.set_language(code)
                    translated = translator.text(source)
                    actual = [line for line in translated.splitlines() if line.startswith(("sudo ", "zypper "))]
                    self.assertEqual(actual, commands)

    def test_new_language_is_discovered_and_missing_entries_use_english(self):
        with tempfile.TemporaryDirectory() as directory:
            self.catalog(directory, messages={"Connect": "Connexion"})
            translator = Translator("fr", directory)
            self.assertEqual(translator.catalogs["fr"].name, "Français")
            self.assertEqual(translator.text("Connect"), "Connexion")
            self.assertEqual(translator.text("Disconnect"), "Disconnect")
            self.assertEqual(translator.text("Server {server}", server="literal-{id}"), "Server literal-{id}")
            translator.set_language("unavailable")
            self.assertEqual(translator.language, "en")
            self.assertEqual(translator.text("Connect"), "Connect")

    def test_bad_entries_fall_back_without_disabling_valid_translations(self):
        with tempfile.TemporaryDirectory() as directory:
            self.catalog(directory, messages={
                "Connect": "Connexion", "Disconnect": None,
                "Server {server}": "Serveur {password}", "City {city}": "Ville {city.__class__}",
                "Load {load}": "Charge {load", "Ready": "",
            })
            translator = Translator("fr", directory)
            self.assertEqual(len(translator.errors), 5)
            self.assertEqual(translator.text("Connect"), "Connexion")
            self.assertEqual(translator.text("Server {server}", server="fr-1"), "Server fr-1")
            self.assertEqual(translator.text("City {city}", city="Paris"), "City Paris")
            self.assertEqual(translator.text("Load {load}", load=20), "Load 20")
            self.assertEqual(translator.text("Disconnect"), "Disconnect")

    def test_malformed_files_and_metadata_do_not_prevent_startup(self):
        invalid = ["not json", "[]", '{"language":"fr","name":"French","messages":{},"messages":{}}',
                   '{"language":"../fr","name":"French","messages":{}}',
                   '{"language":"fr","name":12,"messages":{}}',
                   '{"language":"fr","name":"French","messages":[]}']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fr.json"
            for value in invalid:
                path.write_text(value, encoding="utf-8")
                translator = Translator("fr", directory)
                self.assertEqual(translator.language, "en")
                self.assertTrue(translator.errors)
            path.write_bytes(b"\xff")
            self.assertEqual(Translator("fr", directory).language, "en")

    def test_nested_messages_and_dependency_commands(self):
        translator = Translator("de")
        self.assertEqual(translator.render(message("Demo · {status}", status=message("Connected"))), "Demo · Verbunden")
        english = dependency_help()
        self.assertEqual(Translator().render(dependency_help(message)), english)
        german = translator.render(dependency_help(message))
        commands = [line for line in english.splitlines() if line.startswith("sudo ")]
        self.assertTrue(commands)
        self.assertTrue(all(command in german for command in commands))

    def test_language_preferences_round_trip_and_reject_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            preferences = Preferences(path)
            preferences.data["language"] = "zh-CN"
            preferences.save()
            self.assertEqual(Preferences(path).data["language"], "zh-CN")
            demo = Preferences(path, persistent=False)
            demo.data["language"] = "de"
            demo.save()
            self.assertEqual(Preferences(path).data["language"], "zh-CN")
            for value in (None, [], "../de", "de/../../", 42):
                path.write_text(json.dumps({"language": value}), encoding="utf-8")
                self.assertEqual(Preferences(path).data["language"], "en")


if __name__ == "__main__":
    unittest.main()
