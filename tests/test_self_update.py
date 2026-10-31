from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cyberghost_desktop.i18n import Translator
from cyberghost_desktop.locale_store import LocaleStore, default_manifest, digest, json_bytes
from cyberghost_desktop.self_update import (
    DOWNLOAD_ERROR, GIT_BUSY, GIT_REQUIRED, LANGUAGE_BACKUP_ERROR, LOCAL_CHANGES, LOCAL_COMMITS, SOURCE_ERROR, VERSION_CHANGED,
    CheckoutUpdater, InstallError, checkout_available,
)


@unittest.skipUnless(shutil.which("git"), "Git is required for temporary-checkout tests")
class CheckoutUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.checkout = self.root / "checkout"
        self.source.mkdir()
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.environment.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        self.git(self.source, "init", "--initial-branch=main")
        (self.source / "cyberghost_desktop").mkdir()
        (self.source / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.source / ".gitignore").write_text("ignored.txt\n__pycache__/\n")
        self.catalog = {"language": "de", "name": "Deutsch", "messages": {"Connect": "Verbinden", "Disconnect": "Trennen"}}
        self.publish_catalog(self.catalog)
        self.publish("0.1.3")
        self.git(self.root, "clone", str(self.source), str(self.checkout))
        self.initial = self.git(self.checkout, "rev-parse", "HEAD").stdout.strip()
        self.publish("0.1.5")
        self.personal = self.root / "personal-locales"
        self.updater = CheckoutUpdater(self.checkout, locale_directory=self.personal)
        self.updater.environment.update(self.environment)
        remote = patch("cyberghost_desktop.self_update.GIT_URL", str(self.source))
        remote.start()
        self.addCleanup(remote.stop)

    def git(self, directory, *arguments):
        return subprocess.run(["git", "-c", "user.name=Update Test", "-c", "user.email=update@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *arguments],
                              cwd=directory, env=self.environment, capture_output=True, text=True, check=True, timeout=10)

    def commit(self, directory):
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-m", "test version")

    def publish(self, version):
        (self.source / "cyberghost_desktop/__init__.py").write_text(f'__version__ = "{version}"\n')
        (self.source / "app.py").write_text(f'# App {version}\n')
        self.commit(self.source)

    def publish_catalog(self, catalog):
        directory = self.source / "cyberghost_desktop/locales"
        directory.mkdir(exist_ok=True)
        (directory / "de.json").write_bytes(json_bytes(catalog))
        english = {"language": "en", "name": "English", "messages": {key: key for key in catalog["messages"]}}
        (directory / "en.json").write_bytes(json_bytes(english))
        (self.source / "cyberghost_desktop/locale_manifest.json").write_bytes(json_bytes(default_manifest(directory)))

    def edit_catalog(self):
        personal = deepcopy(self.catalog)
        personal["messages"]["Connect"] = "Meine Übersetzung"
        body = (json.dumps(personal, indent=3, ensure_ascii=False) + "\n").encode("utf-8")
        path = self.checkout / "cyberghost_desktop/locales/de.json"
        path.write_bytes(body)
        return path, body

    def assert_unchanged(self):
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.initial)
        self.assertEqual(self.git(self.checkout, "stash", "list").stdout, "")

    def test_updates_clean_main_checkout_and_retains_ignored_files(self):
        (self.checkout / "ignored.txt").write_text("personal data")
        result = self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(result.version, "0.1.5")
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout,
                         self.git(self.source, "rev-parse", "HEAD").stdout)
        self.assertEqual((self.checkout / "app.py").read_text(), "# App 0.1.5\n")
        self.assertEqual((self.checkout / "ignored.txt").read_text(), "personal data")
        self.assertEqual(self.git(self.checkout, "status", "--porcelain").stdout, "")
        self.assertTrue(checkout_available(self.checkout))

    def test_already_updated_files_can_be_restarted_by_an_older_running_app(self):
        self.git(self.checkout, "pull", "--ff-only")
        result = self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(result.version, "0.1.5")

    def test_migrates_edited_language_then_merges_new_feature_keys_on_restart(self):
        path, body = self.edit_catalog()
        upstream = deepcopy(self.catalog)
        upstream["messages"].update({"Connect": "Neue Vorgabe", "Disconnect": "Verbindung trennen", "New feature": "Neue Funktion"})
        self.publish_catalog(upstream)
        self.commit(self.source)
        self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(json.loads(path.read_bytes()), upstream)
        self.assertEqual((self.personal / ".backups/de" / (digest(body) + ".json")).read_bytes(), body)
        store = LocaleStore(self.personal)
        self.assertEqual(store.sync(self.checkout / "cyberghost_desktop/locales",
                                    self.checkout / "cyberghost_desktop/locale_manifest.json"), [])
        messages = json.loads((self.personal / "de.json").read_bytes())["messages"]
        self.assertEqual(messages, {"Connect": "Meine Übersetzung", "Disconnect": "Verbindung trennen", "New feature": "Neue Funktion"})
        self.assertEqual(store.state["catalogs"]["de"]["conflicts"], ["Connect"])
        self.assertEqual(self.git(self.checkout, "status", "--porcelain").stdout, "")
        self.assertEqual(self.git(self.checkout, "stash", "list").stdout, "")

    def test_moves_new_untracked_language_to_personal_directory(self):
        path = self.checkout / "cyberghost_desktop/locales/nl.json"
        body = json_bytes({"language": "nl", "name": "Nederlands", "messages": {"Connect": "Verbinden"}})
        path.write_bytes(body)
        self.updater.install("0.1.5", "0.1.3")
        self.assertFalse(path.exists())
        self.assertEqual((self.personal / "nl.json").read_bytes(), body)
        translator = Translator("nl", self.checkout / "cyberghost_desktop/locales", user_directory=self.personal)
        self.assertEqual(translator.text("Connect"), "Verbinden")
        self.assertEqual(self.git(self.checkout, "status", "--porcelain").stdout, "")

    def test_existing_personal_edits_win_but_both_versions_are_saved(self):
        store = LocaleStore(self.personal)
        store.sync(self.checkout / "cyberghost_desktop/locales", self.checkout / "cyberghost_desktop/locale_manifest.json")
        personal = deepcopy(self.catalog)
        personal["messages"]["Connect"] = "Persönliche Datei"
        (self.personal / "de.json").write_bytes(json_bytes(personal))
        _path, body = self.edit_catalog()
        self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(json.loads((self.personal / "de.json").read_bytes())["messages"]["Connect"], "Persönliche Datei")
        self.assertEqual((self.personal / ".backups/de" / (digest(body) + ".json")).read_bytes(), body)

    def test_failed_language_backup_preserves_checkout_and_edits(self):
        path, body = self.edit_catalog()
        with patch.object(LocaleStore, "_backup", side_effect=OSError("disk full")), self.assertRaisesRegex(InstallError, LANGUAGE_BACKUP_ERROR):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(path.read_bytes(), body)
        self.assert_unchanged()

    def test_staged_language_changes_remain_for_manual_update(self):
        path, body = self.edit_catalog()
        self.git(self.checkout, "add", "cyberghost_desktop/locales/de.json")
        with self.assertRaisesRegex(InstallError, LOCAL_CHANGES):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(self.git(self.checkout, "show", ":cyberghost_desktop/locales/de.json").stdout.encode("utf-8"), body)
        self.assertFalse(self.personal.exists())
        self.assert_unchanged()

    def test_language_edit_during_download_is_not_discarded(self):
        path, _body = self.edit_catalog()
        changed = json_bytes(dict(self.catalog, name="Neue Bearbeitung"))
        run = self.updater._git

        def edited(*arguments, **kwargs):
            result = run(*arguments, **kwargs)
            if arguments[0] == "fetch":
                path.write_bytes(changed)
            return result

        with patch.object(self.updater, "_git", side_effect=edited), self.assertRaisesRegex(InstallError, LOCAL_CHANGES):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(path.read_bytes(), changed)
        self.assert_unchanged()

    def test_language_edit_during_backup_is_not_restored_over(self):
        path, body = self.edit_catalog()
        changed = json_bytes(dict(self.catalog, name="Bearbeitung während Sicherung"))
        adopt = LocaleStore.adopt

        def edited(store, *arguments):
            adopt(store, *arguments)
            path.write_bytes(changed)

        with patch.object(LocaleStore, "adopt", edited), self.assertRaisesRegex(InstallError, LOCAL_CHANGES):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual((self.personal / ".backups/de" / (digest(body) + ".json")).read_bytes(), body)
        self.assert_unchanged()

    def test_partial_checkout_failure_requires_restart(self):
        run = self.updater._git

        def partial(*arguments, **kwargs):
            if arguments[0] == "merge":
                (self.checkout / "app.py").write_text("# partially updated\n")
                raise InstallError("Git failed")
            return run(*arguments, **kwargs)

        with patch.object(self.updater, "_git", side_effect=partial), self.assertRaises(InstallError) as failure:
            self.updater.install("0.1.5", "0.1.3")
        self.assertTrue(failure.exception.restart_required)
        self.assertEqual((self.checkout / "app.py").read_text(), "# partially updated\n")
        self.assert_unchanged()

    def test_preserves_modified_staged_and_untracked_files_without_fetching(self):
        for kind in ("modified", "staged", "untracked"):
            path = self.checkout / ("translation.json" if kind == "untracked" else "app.py")
            path.write_text("my changes")
            if kind == "staged":
                self.git(self.checkout, "add", "app.py")
            with (self.subTest(kind=kind), patch.object(self.updater, "_git", wraps=self.updater._git) as run,
                  self.assertRaisesRegex(InstallError, LOCAL_CHANGES)):
                self.updater.install("0.1.5", "0.1.3")
            self.assertFalse(any(call.args[0] == "fetch" for call in run.call_args_list))
            self.assertEqual(path.read_text(), "my changes")
            self.assert_unchanged()
            # Restore only disposable test fixtures for the next subtest.
            if kind == "untracked":
                path.unlink()
            else:
                self.git(self.checkout, "restore", "--source=HEAD", "--staged", "--worktree", "app.py")

    def test_refuses_local_commits_without_merging_or_stashing(self):
        (self.checkout / "app.py").write_text("# Local feature\n")
        self.commit(self.checkout)
        self.initial = self.git(self.checkout, "rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(InstallError, LOCAL_COMMITS):
            self.updater.install("0.1.5", "0.1.3")
        self.assert_unchanged()
        self.assertEqual((self.checkout / "app.py").read_text(), "# Local feature\n")

    def test_refuses_other_branches_and_detached_head(self):
        for arguments in (("switch", "-c", "translation"), ("checkout", "--detach")):
            self.git(self.checkout, *arguments)
            with self.subTest(arguments=arguments), self.assertRaisesRegex(InstallError, SOURCE_ERROR):
                self.updater.install("0.1.5", "0.1.3")
            self.assert_unchanged()

    def test_refuses_another_remote_without_contacting_it(self):
        self.git(self.checkout, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(InstallError, SOURCE_ERROR):
            self.updater.install("0.1.5", "0.1.3")
        self.assert_unchanged()

    def test_refuses_in_progress_git_operations(self):
        for name in ("MERGE_HEAD", "index.lock", "rebase-apply"):
            path = self.checkout / ".git" / name
            path.touch()
            with self.subTest(name=name), self.assertRaisesRegex(InstallError, GIT_BUSY):
                self.updater.install("0.1.5", "0.1.3")
            path.unlink()
            self.assert_unchanged()

    def test_rechecks_changes_made_during_download(self):
        run = self.updater._git

        def edited(*arguments, **kwargs):
            result = run(*arguments, **kwargs)
            if arguments[0] == "fetch":
                (self.checkout / "app.py").write_text("edited while downloading")
            return result

        with patch.object(self.updater, "_git", side_effect=edited), self.assertRaisesRegex(InstallError, LOCAL_CHANGES):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual((self.checkout / "app.py").read_text(), "edited while downloading")
        self.assert_unchanged()

    def test_refuses_to_overwrite_an_ignored_file_added_by_the_update(self):
        (self.checkout / "ignored.txt").write_text("my private data")
        (self.source / "ignored.txt").write_text("upstream content")
        self.git(self.source, "add", "--force", "ignored.txt")
        self.commit(self.source)
        with self.assertRaises(InstallError):
            self.updater.install("0.1.5", "0.1.3")
        self.assertEqual((self.checkout / "ignored.txt").read_text(), "my private data")
        self.assert_unchanged()

    def test_invalid_or_older_published_versions_never_change_working_files(self):
        for version in ("0.1.2", "invalid", "0.1.5rc1"):
            self.publish(version)
            with self.subTest(version=version), self.assertRaisesRegex(InstallError, VERSION_CHANGED):
                self.updater.install("0.1.5", "0.1.3")
            self.assert_unchanged()

    def test_missing_restart_script_is_rejected_before_installation(self):
        (self.source / "run.sh").unlink()
        self.commit(self.source)
        with self.assertRaisesRegex(InstallError, VERSION_CHANGED):
            self.updater.install("0.1.5", "0.1.3")
        self.assert_unchanged()

    def test_download_timeout_leaves_working_files_untouched(self):
        real_run = subprocess.run

        def timeout(command, **kwargs):
            if "fetch" in command:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return real_run(command, **kwargs)

        with patch("cyberghost_desktop.self_update.subprocess.run", side_effect=timeout), self.assertRaisesRegex(InstallError, DOWNLOAD_ERROR):
            self.updater.install("0.1.5", "0.1.3")
        self.assert_unchanged()

    def test_post_merge_hooks_are_not_run(self):
        hook = self.checkout / ".git/hooks/post-merge"
        marker = self.root / "hook-ran"
        hook.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n")
        hook.chmod(0o755)
        self.updater.install("0.1.5", "0.1.3")
        self.assertFalse(marker.exists())

    def test_zip_install_and_missing_git_are_reported(self):
        with self.assertRaisesRegex(InstallError, GIT_REQUIRED):
            CheckoutUpdater(self.root)
        with patch("cyberghost_desktop.self_update.shutil.which", return_value=None):
            self.assertFalse(checkout_available(self.checkout))
            with self.assertRaisesRegex(InstallError, GIT_REQUIRED):
                CheckoutUpdater(self.checkout)


if __name__ == "__main__":
    unittest.main()
