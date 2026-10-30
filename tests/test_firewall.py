from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from cyberghost_desktop.cli import CliError
from cyberghost_desktop.firewall import Firewall, GuardStatus, OWNER, TABLE, fingerprint, ruleset, authorized_action, serve
from cyberghost_desktop.vpn_guard import DemoGuard, VPNGuard


def document(handle=1, policy="drop", owner=OWNER):
    return {"nftables": [{"metainfo": {"version": "test"}},
                         {"table": {"family": "inet", "name": TABLE, "handle": handle, "comment": owner}},
                         {"chain": {"family": "inet", "table": TABLE, "name": "output", "policy": policy}}]}


class FirewallTests(unittest.TestCase):
    def test_mark_is_strict_and_rules_cover_both_ip_versions_and_forwarding(self):
        for invalid in (0, -1, 2**32, "1; flush ruleset", True):
            with self.subTest(mark=invalid), self.assertRaises(ValueError):
                ruleset(invalid)
        source = ruleset(51820)
        self.assertIn("table inet", source)
        self.assertEqual(source.count("policy drop"), 2)
        self.assertIn('oifname "cyberghost"', source)
        self.assertIn("meta l4proto udp meta mark 51820 accept", source)
        self.assertNotIn("flush", source)
        self.assertNotIn("ct state established", source.split("chain forward")[0])

    def test_fingerprint_ignores_handles_but_detects_rule_changes(self):
        self.assertEqual(fingerprint(document(1)), fingerprint(document(99)))
        self.assertNotEqual(fingerprint(document()), fingerprint(document(policy="accept")))

    def test_actual_rules_are_checked_not_a_saved_boolean(self):
        firewall = Firewall()
        firewall.expected = fingerprint(document())
        with patch.object(firewall, "document", return_value=document()):
            self.assertEqual(firewall.status().state, "on")
        with patch.object(firewall, "document", return_value=document(policy="accept")):
            self.assertEqual(firewall.status().state, "unknown")
        with patch.object(firewall, "document", return_value=None):
            self.assertEqual(firewall.status().state, "off")

    def test_foreign_table_is_not_deleted(self):
        firewall = Firewall()
        listings = [{"nftables": [{"table": {"family": "inet", "name": TABLE}}]}, document(owner="Someone else")]
        with patch.object(firewall, "command", side_effect=[json.dumps(item) for item in listings]) as command:
            with self.assertRaisesRegex(RuntimeError, "left unchanged"):
                firewall.disable()
        self.assertTrue(all("delete" not in call.args for call in command.call_args_list))

    def test_replacement_is_one_atomic_transaction_and_only_owns_one_table(self):
        firewall = Firewall()
        with patch.object(firewall, "document", return_value=document()), patch.object(firewall, "command") as command:
            self.assertEqual(firewall.enable(51820).state, "on")
        command.assert_called_once_with("nft", "--file", "-", source=f"delete table inet {TABLE}\n" + ruleset(51820))

    def test_activation_requires_real_routed_wireguard_and_mark(self):
        firewall = Firewall()
        link = {"ifname": "cyberghost", "flags": ["UP"], "linkinfo": {"info_kind": "wireguard"}}
        routes = [{"dev": "cyberghost"}]
        with patch.object(firewall, "command", side_effect=[json.dumps([link]), json.dumps(routes), "0xca6c\n"]):
            self.assertEqual(firewall.wireguard_mark(), 51820)
        for links, route, mark in [([], routes, "0xca6c"), ([link], [{"dev": "eth0"}], "0xca6c"),
                                   ([link], routes, "off"), ([link], routes, "0")]:
            with patch.object(firewall, "command", side_effect=[json.dumps(links), json.dumps(route), mark]), self.assertRaises(RuntimeError):
                firewall.wireguard_mark()

    def test_system_tools_environment_and_bounded_queries(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "{}", ""))
        firewall = Firewall(run)
        with patch("cyberghost_desktop.firewall.shutil.which", return_value="/usr/sbin/nft"):
            firewall.command("nft", "list", "tables")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/sbin/nft", "list", "tables"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 8)
        self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_invalid_actions_never_change_firewall(self):
        with patch("cyberghost_desktop.firewall.os.geteuid", return_value=1000), self.assertRaises(RuntimeError):
            authorized_action("enable")
        with patch("cyberghost_desktop.firewall.os.geteuid", return_value=0), self.assertRaises(RuntimeError):
            authorized_action("shell")

    def test_disable_after_disconnect_does_not_require_wireguard(self):
        firewall = Firewall()
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "active").touch()
            with (patch("cyberghost_desktop.firewall.os.geteuid", return_value=0),
                  patch("cyberghost_desktop.firewall.secure_runtime"),
                  patch("cyberghost_desktop.firewall.request", side_effect=FileNotFoundError),
                  patch.object(firewall, "wireguard_mark", side_effect=AssertionError("VPN disconnected")),
                  patch.object(firewall, "document", return_value=document()),
                  patch.object(firewall, "command") as command):
                self.assertEqual(authorized_action("disable", runtime, firewall).state, "off")
            command.assert_called_once_with("nft", "delete", "table", "inet", TABLE)
            self.assertFalse((runtime / "active").exists())

    def test_socket_allows_unprivileged_status_but_never_mutation(self):
        for action in ("status", "enable", "disable", "run command"):
            with self.subTest(action=action):
                server = MagicMock()
                server.makefile.return_value = BytesIO(action.encode() + b"\n")
                listener = Mock()
                listener.accept.side_effect = [(server, None), StopIteration]
                firewall = Mock(spec=Firewall)
                firewall.status.return_value = GuardStatus("off", "Verified off")
                with patch("cyberghost_desktop.firewall.peer_uid", return_value=1000), self.assertRaises(StopIteration):
                    serve(listener, firewall, Path("/unused"))
                result = json.loads(server.sendall.call_args.args[0])
                if action == "status":
                    self.assertEqual(result["state"], "off")
                else:
                    self.assertIn("authorization", result["error"])
                firewall.enable.assert_not_called()
                firewall.disable.assert_not_called()


class GuardClientTests(unittest.TestCase):
    def test_monitor_missing_is_off_only_if_no_activation_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("cyberghost_desktop.vpn_guard.RUNTIME", root), patch("cyberghost_desktop.vpn_guard.request", side_effect=FileNotFoundError):
                guard = VPNGuard()
                self.assertEqual(guard.status().state, "off")
                (root / "active").touch()
                self.assertEqual(guard.status().state, "unknown")

    def test_unverifiable_status_is_never_protected_or_disconnected(self):
        with patch("cyberghost_desktop.vpn_guard.request", side_effect=TimeoutError):
            self.assertEqual(VPNGuard().status().state, "unknown")

    def test_polkit_fixed_action_with_isolated_python_and_no_root_environment(self):
        output = json.dumps(asdict(GuardStatus("on", "Verified")))
        with patch("cyberghost_desktop.vpn_guard.shutil.which", return_value="/usr/bin/pkexec"), \
                patch("cyberghost_desktop.vpn_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
            self.assertEqual(VPNGuard().change(True).state, "on")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:4], ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/python3", "-I"])
        self.assertEqual(args[0][-1], "enable")
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("timeout", kwargs)  # Do not terminate an authorized mutation halfway through.

    def test_cancelled_authorization_is_explicit(self):
        with patch("cyberghost_desktop.vpn_guard.shutil.which", return_value="/usr/bin/pkexec"), \
                patch("cyberghost_desktop.vpn_guard.subprocess.run", return_value=subprocess.CompletedProcess([], 126, "", "")):
            with self.assertRaisesRegex(CliError, "cancelled"):
                VPNGuard().change(True)

    def test_moved_app_reports_missing_helper_before_authorization(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch("cyberghost_desktop.vpn_guard.__file__", str(Path(directory) / "old-folder" / "vpn_guard.py")),
              patch("cyberghost_desktop.vpn_guard.shutil.which", return_value="/usr/bin/pkexec"),
              patch("cyberghost_desktop.vpn_guard.subprocess.run") as run):
            with self.assertRaisesRegex(CliError, "quit the app from the tray and reopen") as error:
                VPNGuard().change(False)
            self.assertIn("old-folder/firewall.py", error.exception.details)
            run.assert_not_called()

    def test_helper_failure_preserves_diagnostic(self):
        result = subprocess.CompletedProcess([], 2, "", "python: cannot open firewall.py")
        with (patch("cyberghost_desktop.vpn_guard.shutil.which", return_value="/usr/bin/pkexec"),
              patch("cyberghost_desktop.vpn_guard.subprocess.run", return_value=result)):
            with self.assertRaisesRegex(CliError, "exit 2") as error:
                VPNGuard().change(False)
            self.assertEqual(error.exception.details, result.stderr)

    def test_invalid_helper_response_is_not_success(self):
        for output in ('[]', '{"state":"off"}', '{"state":"bad","detail":"Verified"}'):
            result = subprocess.CompletedProcess([], 0, output, "")
            with (self.subTest(output=output),
                  patch("cyberghost_desktop.vpn_guard.shutil.which", return_value="/usr/bin/pkexec"),
                  patch("cyberghost_desktop.vpn_guard.subprocess.run", return_value=result),
                  self.assertRaisesRegex(CliError, "invalid")):
                VPNGuard().change(False)

    def test_demo_never_reads_runtime_or_invokes_commands(self):
        with patch("cyberghost_desktop.vpn_guard.request", side_effect=AssertionError("real status")), \
                patch("cyberghost_desktop.vpn_guard.subprocess.run", side_effect=AssertionError("real command")):
            guard = DemoGuard()
            self.assertEqual(guard.status().state, "off")
            self.assertEqual(guard.change(True).state, "on")
            self.assertEqual(guard.change(False).state, "off")
