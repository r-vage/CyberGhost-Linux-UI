from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop.__main__ import main


class RestartTests(unittest.TestCase):
    def run_app(self, arguments, *, restart=True, error=None):
        app = Mock(restart_requested=restart)
        app.run.return_value = 0
        app.translator.text.side_effect = lambda source: source
        factory = Mock(return_value=app)
        modules = {"cyberghost_desktop.app": SimpleNamespace(Application=factory),
                   "gi.repository": SimpleNamespace(GLib=Mock())}
        stderr = StringIO()
        with (patch.dict("sys.modules", modules), patch("sys.argv", ["cyberghost-desktop", *arguments]),
              patch("cyberghost_desktop.__main__.os.execv", side_effect=error) as execute, redirect_stderr(stderr)):
            result = main()
        return result, execute, stderr.getvalue()

    def test_restart_replaces_process_with_current_checkout_and_preserves_no_tray(self):
        result, execute, _ = self.run_app(["--no-tray", "--setup"])
        self.assertEqual(result, 0)
        command = execute.call_args.args[1]
        self.assertEqual(command, ["/bin/sh", str(Path(__file__).resolve().parents[1] / "run.sh"), "--no-tray"])
        self.assertEqual(execute.call_args.args[0], "/bin/sh")
        self.assertNotIn("--setup", command)

    def test_restart_preserves_default_tray_behavior(self):
        _, execute, _ = self.run_app([])
        self.assertEqual(len(execute.call_args.args[1]), 2)

    def test_normal_quit_does_not_restart(self):
        result, execute, _ = self.run_app([], restart=False)
        self.assertEqual(result, 0)
        execute.assert_not_called()

    def test_failed_restart_reports_how_to_reopen(self):
        result, _, error = self.run_app([], error=OSError("exec failed"))
        self.assertEqual(result, 1)
        self.assertIn("Close it and open it again", error)


if __name__ == "__main__":
    unittest.main()
