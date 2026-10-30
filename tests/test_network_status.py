import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop.cli import CyberGhostClient


ACTIVE_LINK = {"ifname": "cyberghost", "flags": ["POINTOPOINT", "NOARP", "UP", "LOWER_UP"],
               "operstate": "UNKNOWN", "linkinfo": {"info_kind": "wireguard"}}
ACTIVE_ROUTE = {"dst": "1.1.1.1", "dev": "cyberghost", "table": "51820", "flags": []}


def result(value, *, code=0, error=""):
    return subprocess.CompletedProcess([], code, value if isinstance(value, str) else json.dumps(value), error)


class NetworkStatusTests(unittest.TestCase):
    def check(self, replies):
        run = Mock(side_effect=replies)
        with patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/sbin/ip"):
            status = CyberGhostClient("/usr/bin/cyberghostvpn", run).status()
        return status, run

    def test_normal_user_detects_active_wireguard_despite_cli_false_negative(self):
        status, run = self.check([result("No VPN connections found."), result([ACTIVE_LINK]), result([ACTIVE_ROUTE])])
        self.assertEqual(status.state, "connected")
        self.assertIn("IPv4 internet route", status.detail)
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/sbin/ip", "-j", "-details", "link", "show", "type", "wireguard"])
        self.assertEqual(run.call_args_list[2].args[0], ["/usr/sbin/ip", "-j", "-4", "route", "get", "1.1.1.1"])
        for call in run.call_args_list:
            self.assertNotIn("pkexec", " ".join(call.args[0]))
            self.assertNotIn("sudo", " ".join(call.args[0]))
            self.assertFalse(call.kwargs.get("shell", False))
            self.assertIsNotNone(call.kwargs["timeout"])

    def test_no_interface_preserves_disconnected_cli_status(self):
        status, run = self.check([result("No VPN connections found."), result([])])
        self.assertEqual(status.state, "disconnected")
        self.assertEqual(run.call_count, 2)

    def test_other_wireguard_interface_is_not_a_cyberghost_connection(self):
        status, _ = self.check([result("No VPN connections found."), result([{**ACTIVE_LINK, "ifname": "wg0"}])])
        self.assertEqual(status.state, "disconnected")

    def test_down_interface_does_not_report_connected(self):
        status, run = self.check([result("No VPN connections found."), result([{**ACTIVE_LINK, "flags": ["POINTOPOINT"]}])])
        self.assertEqual(status.state, "disconnected")
        self.assertEqual(run.call_count, 2)

    def test_interface_without_vpn_routing_reports_unknown(self):
        status, _ = self.check([result("No VPN connections found."), result([ACTIVE_LINK]), result([{"dev": "eno1"}])])
        self.assertEqual(status.state, "unknown")
        self.assertIn("route does not use it", status.detail)

    def test_permission_failure_is_not_reported_as_disconnected(self):
        status, _ = self.check([result("No VPN connections found."), result("", code=1, error="Operation not permitted")])
        self.assertEqual(status.state, "unknown")
        self.assertIn("Operation not permitted", status.detail)

    def test_route_lookup_failure_is_not_reported_as_connected(self):
        status, _ = self.check([result("No VPN connections found."), result([ACTIVE_LINK]),
                               result("", code=2, error="Network is unreachable")])
        self.assertEqual(status.state, "unknown")

    def test_malformed_network_output_is_unknown(self):
        for output in ("not JSON", {"ifname": "cyberghost"}, [None], [{"ifname": "cyberghost"}]):
            with self.subTest(output=output):
                status, _ = self.check([result("No VPN connections found."), result(output)])
                self.assertEqual(status.state, "unknown")

    def test_timed_out_network_probe_is_unknown(self):
        status, _ = self.check([result("No VPN connections found."), subprocess.TimeoutExpired("ip", 10)])
        self.assertEqual(status.state, "unknown")
        self.assertIn("timed out", status.detail)

    def test_missing_ip_cannot_claim_disconnected(self):
        run = Mock(return_value=result("No VPN connections found."))
        with patch("cyberghost_desktop.cli.shutil.which", return_value=None):
            status = CyberGhostClient("/usr/bin/cyberghostvpn", run).status()
        self.assertEqual(status.state, "unknown")
        self.assertIn("iproute2", status.detail)

    def test_existing_openvpn_and_wireguard_cli_status_are_preserved(self):
        for message in ("VPN connection found.", "Wireguard connection found."):
            with self.subTest(message=message):
                status, run = self.check([result(message)])
                self.assertEqual(status.state, "connected")
                self.assertEqual(run.call_count, 1)

    def test_interface_removal_does_not_reuse_previous_success(self):
        run = Mock(side_effect=[result("No VPN connections found."), result([ACTIVE_LINK]), result([ACTIVE_ROUTE]),
                                result("No VPN connections found."), result([])])
        client = CyberGhostClient("/usr/bin/cyberghostvpn", run)
        with patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/sbin/ip"):
            self.assertEqual(client.status().state, "connected")
            self.assertEqual(client.status().state, "disconnected")


if __name__ == "__main__":
    unittest.main()
