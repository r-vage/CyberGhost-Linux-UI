import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cyberghost_desktop import __version__
from cyberghost_desktop.startup import Autostart

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def make_checkout(self, root):
        project = root / "ZIP download" / "cyberghost-desktop"
        project.mkdir(parents=True)
        shutil.copytree(ROOT / "cyberghost_desktop", project / "cyberghost_desktop")
        shutil.copytree(ROOT / "assets", project / "assets")
        shutil.copy2(ROOT / "run.sh", project / "run.sh")
        (project / "run.sh").chmod(0o644)
        return project

    def run_launcher(self, command):
        result = subprocess.run(
            [str(command), "--version"], capture_output=True, text=True,
            timeout=10, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), __version__)

    def test_zip_checkout_without_executable_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_checkout(root)
            paths = installer.install(root / "user home", project)
            self.run_launcher(paths["command"])
            self.assertEqual((project / "run.sh").stat().st_mode & 0o777, 0o644)

    def test_reinstall_updates_launcher_after_moving_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_checkout(root)
            home = root / "user home"
            installer.install(home, project)
            entry = Autostart(config=home / ".config", project=project)
            entry.commit(entry.stage())
            moved = root / "New projects" / "cyberghost-desktop"
            moved.parent.mkdir()
            shutil.move(project, moved)
            paths = installer.install(home, moved)
            self.run_launcher(paths["command"])
            self.assertIn(str(moved), entry.path.read_text())
            self.assertNotIn(str(project), entry.path.read_text())
            installer.uninstall(home)
            self.assertFalse(entry.path.exists())

    def test_idempotent_install_and_scoped_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            paths = installer.install(home, ROOT)
            installer.install(home, ROOT)
            self.assertTrue(paths["command"].stat().st_mode & 0o100)
            self.assertIn("CyberGhost Desktop", paths["desktop"].read_text())
            unrelated = home / ".local/bin/unrelated"
            unrelated.write_text("keep")
            installer.uninstall(home)
            self.assertTrue(all(not p.exists() for p in paths.values()))
            self.assertEqual(unrelated.read_text(), "keep")

    def test_existing_unrelated_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            paths = installer.targets(home)
            paths["desktop"].parent.mkdir(parents=True)
            paths["desktop"].write_text("other app")
            with self.assertRaises(RuntimeError):
                installer.install(home, ROOT)
            self.assertFalse(paths["command"].exists())
            self.assertEqual(paths["desktop"].read_text(), "other app")

    def test_existing_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            paths = installer.targets(home)
            victim = home / "victim"
            victim.write_text("keep")
            paths["command"].parent.mkdir(parents=True)
            paths["command"].symlink_to(victim)
            with self.assertRaises(RuntimeError):
                installer.install(home, ROOT)
            self.assertEqual(victim.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
