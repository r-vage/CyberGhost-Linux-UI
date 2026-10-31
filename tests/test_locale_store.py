from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cyberghost_desktop import __version__
from cyberghost_desktop.i18n import Translator
from cyberghost_desktop.locale_store import (
    SOURCE_MANIFEST, LocaleStore, atomic_write, default_manifest, digest, json_bytes, merge_catalog,
)


class LocaleStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.defaults = self.root / "bundled"
        self.defaults.mkdir()
        self.personal = self.root / "personal"
        self.source_manifest = self.root / "source-manifest.json"
        self.original = {"language": "de", "name": "Deutsch", "messages": {
            "Connect": "Verbinden", "Disconnect": "Trennen", "Old untouched": "Alt",
            "Old custom": "Alte Anpassung", "Locations": "Orte",
        }}
        self.publish(self.original)

    def publish(self, data):
        atomic_write(self.defaults / "de.json", json_bytes(data))
        english = {"language": "en", "name": "English", "messages": {key: key for key in data["messages"]}}
        atomic_write(self.defaults / "en.json", json_bytes(english))
        atomic_write(self.source_manifest, json_bytes(default_manifest(self.defaults)))

    def sync(self):
        store = LocaleStore(self.personal)
        self.assertEqual(store.sync(self.defaults, self.source_manifest), [])
        return store

    def read(self, code="de"):
        return json.loads((self.personal / f"{code}.json").read_bytes())

    def backup(self, code, body):
        return self.personal / ".backups" / code / (digest(body) + ".json")

    def test_manifest_matches_packaged_files_and_version(self):
        self.assertEqual(SOURCE_MANIFEST.read_bytes(), json_bytes(default_manifest()))
        self.assertEqual(json.loads(SOURCE_MANIFEST.read_bytes())["version"], __version__)

    def test_first_launch_seeds_editable_files_and_unchanged_launch_does_not_rewrite(self):
        store = self.sync()
        self.assertEqual(self.read(), self.original)
        self.assertEqual(json.loads((self.personal / ".upstream/de.json").read_bytes()), self.original)
        self.assertEqual(store.state["catalogs"]["de"]["defaults"], self.original)
        snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.personal.rglob("*.json")}
        self.sync()
        for path, snapshot in snapshots.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)
        self.assertFalse((self.personal / ".backups").exists())

    def test_three_way_update_adds_keys_improves_defaults_and_retains_customizations(self):
        self.sync()
        personal = self.read()
        personal["messages"].update({"Connect": "Mein Verbinden", "Old custom": "Behalten"})
        body = json_bytes(personal)
        (self.personal / "de.json").write_bytes(body)
        upstream = deepcopy(self.original)
        upstream["messages"].update({"Connect": "Neu verbinden", "Disconnect": "Verbindung trennen", "New feature": "Neue Funktion"})
        del upstream["messages"]["Old custom"]
        del upstream["messages"]["Old untouched"]
        self.publish(upstream)
        store = self.sync()
        expected = {**upstream["messages"], "Connect": "Mein Verbinden", "Old custom": "Behalten"}
        self.assertEqual(self.read()["messages"], expected)
        self.assertEqual(self.backup("de", body).read_bytes(), body)
        self.assertEqual(store.state["catalogs"]["de"]["conflicts"], ["Connect"])
        self.assertEqual(json.loads((self.personal / ".upstream/de.json").read_bytes()), upstream)
        self.assertEqual(self.sync().state["catalogs"]["de"]["conflicts"], ["Connect"])
        self.assertEqual(self.read()["messages"], expected)
        personal = self.read()
        personal["messages"]["Connect"] = upstream["messages"]["Connect"]
        (self.personal / "de.json").write_bytes(json_bytes(personal))
        self.assertEqual(self.sync().state["catalogs"]["de"]["conflicts"], [])

    def test_custom_name_is_preserved_while_unchanged_name_can_update(self):
        upstream = dict(self.original, name="Deutsch (Deutschland)")
        merged, conflicts = merge_catalog(self.original, self.original, upstream)
        self.assertEqual(merged["name"], upstream["name"])
        self.assertEqual(conflicts, [])
        merged, conflicts = merge_catalog(self.original, dict(self.original, name="Mein Deutsch"), upstream)
        self.assertEqual(merged["name"], "Mein Deutsch")
        self.assertEqual(conflicts, ["name"])

    def test_new_personal_language_is_discovered_and_receives_future_english_keys(self):
        self.sync()
        custom = {"language": "nl", "name": "Nederlands", "messages": {"Connect": "Verbinden"}}
        atomic_write(self.personal / "nl.json", json_bytes(custom))
        self.sync()
        self.assertEqual(self.read("nl")["messages"]["Disconnect"], "Disconnect")
        upstream = deepcopy(self.original)
        upstream["messages"]["New feature"] = "Neue Funktion"
        self.publish(upstream)
        self.sync()
        translator = Translator("nl", directory=self.defaults, user_directory=self.personal)
        self.assertEqual(translator.text("Connect"), "Verbinden")
        self.assertEqual(translator.text("New feature"), "New feature")
        self.assertEqual(translator.catalogs["nl"].name, "Nederlands")
        self.assertEqual(self.read("nl")["messages"]["New feature"], "New feature")
        self.assertNotIn(".manifest", translator.catalogs)
        self.assertEqual(translator.errors, [])

    def test_unknown_baseline_preserves_existing_values_and_still_adds_keys(self):
        self.sync()
        personal = self.read()
        personal["messages"]["Connect"] = "Mein Text"
        (self.personal / "de.json").write_bytes(json_bytes(personal))
        upstream = deepcopy(self.original)
        upstream["messages"].update({"Disconnect": "Neue Vorgabe", "New feature": "Neu"})
        self.publish(upstream)
        for state in (b"broken json", b"[]", json_bytes({"schema": 1, "catalogs": {"de": []}})):
            (self.personal / ".manifest.json").write_bytes(state)
            store = self.sync()
            self.assertEqual(self.read()["messages"]["Connect"], "Mein Text")
            self.assertEqual(self.read()["messages"]["Disconnect"], "Trennen")
            self.assertEqual(self.read()["messages"]["New feature"], "Neu")
            self.assertIn("Disconnect", store.state["catalogs"]["de"]["conflicts"])

    def test_changed_bundled_catalog_does_not_become_trusted_default(self):
        self.sync()
        custom = deepcopy(self.original)
        custom["messages"]["Connect"] = "Eigene Übersetzung"
        body = json_bytes(custom)
        (self.defaults / "de.json").write_bytes(body)
        store = self.sync()
        self.assertEqual(self.read()["messages"]["Connect"], "Eigene Übersetzung")
        self.assertEqual(store.state["catalogs"]["de"]["defaults"], self.original)
        self.assertEqual(self.backup("de", body).read_bytes(), body)
        upstream = deepcopy(self.original)
        upstream["messages"].update({"Connect": "Neue Vorgabe", "New feature": "Neu"})
        self.publish(upstream)
        self.sync()
        self.assertEqual(self.read()["messages"]["Connect"], "Eigene Übersetzung")
        self.assertEqual(self.read()["messages"]["New feature"], "Neu")

    def test_missing_source_manifest_is_conservative(self):
        self.sync()
        upstream = deepcopy(self.original)
        upstream["messages"]["Connect"] = "Neue Vorgabe"
        self.publish(upstream)
        self.source_manifest.unlink()
        store = self.sync()
        self.assertEqual(store.state["catalogs"]["de"]["defaults"], self.original)
        self.assertEqual(self.read()["messages"]["Connect"], "Neue Vorgabe")
        self.assertTrue(self.backup("de", json_bytes(upstream)).exists())

    def test_invalid_user_file_is_untouched_and_runtime_uses_bundled_translations(self):
        self.sync()
        body = b'{"language": "de", "messages": {}'  # An unfinished edit.
        (self.personal / "de.json").write_bytes(body)
        store = LocaleStore(self.personal)
        self.assertTrue(store.sync(self.defaults, self.source_manifest))
        self.assertEqual((self.personal / "de.json").read_bytes(), body)
        translator = Translator("de", directory=self.defaults, user_directory=self.personal)
        self.assertEqual(translator.text("Connect"), "Verbinden")
        self.assertTrue(translator.errors)

    def test_invalid_source_file_does_not_replace_existing_personal_file(self):
        self.sync()
        before = (self.personal / "de.json").read_bytes()
        (self.defaults / "de.json").write_bytes(b"unfinished")
        self.assertTrue(LocaleStore(self.personal).sync(self.defaults, self.source_manifest))
        self.assertEqual((self.personal / "de.json").read_bytes(), before)

    def test_invalid_personal_entries_fall_back_to_same_language(self):
        self.sync()
        personal = self.read()
        personal["messages"].update({"Connect": "{wrong}", "Disconnect": "Mein Text"})
        (self.personal / "de.json").write_bytes(json_bytes(personal))
        self.sync()
        translator = Translator("de", directory=self.defaults, user_directory=self.personal)
        self.assertEqual(translator.text("Connect"), "Verbinden")
        self.assertEqual(translator.text("Disconnect"), "Mein Text")
        self.assertEqual(self.read()["messages"]["Connect"], "{wrong}")

    def test_failed_backup_does_not_replace_user_edits(self):
        self.sync()
        personal = self.read()
        personal["messages"]["Connect"] = "Mein Text"
        body = json_bytes(personal)
        (self.personal / "de.json").write_bytes(body)
        upstream = deepcopy(self.original)
        upstream["messages"]["New feature"] = "Neu"
        self.publish(upstream)
        with patch.object(LocaleStore, "_backup", side_effect=OSError("disk full")):
            self.assertTrue(LocaleStore(self.personal).sync(self.defaults, self.source_manifest))
        self.assertEqual((self.personal / "de.json").read_bytes(), body)

    def test_atomic_writes_refuse_symlinks_and_preserve_direction_escapes(self):
        target = self.root / "target.json"
        target.write_bytes(b"personal contents")
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            atomic_write(link, b"replacement")
        self.assertEqual(target.read_bytes(), b"personal contents")
        self.assertIn(b"\\u2067", json_bytes({"label": "\u2067text\u2069"}))


if __name__ == "__main__":
    unittest.main()
