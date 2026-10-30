import json
import os
import pwd
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cyberghost_desktop.cli import (
    CyberGhostClient, CliError, DemoClient, NetworkUnavailable, Selection,
    parse_choices, parse_locations, parse_status,
)

COUNTRIES = """
+-----+----------------------+--------------+
| No. |     Country Name     | Country Code |
+-----+----------------------+--------------+
|  1  |       Germany        |      DE      |
|  2  |    United States     |      US      |
+-----+----------------------+--------------+
"""
CITIES = """
+-----+------------+----------+------+
| No. |    City    | Instance | Load |
+-----+------------+----------+------+
|  1  |   Berlin   |   110    | 47%  |
|  2  | Frankfurt  |   993    | 48%  |
+-----+------------+----------+------+
"""
SERVERS = """
+-----+-----------+--------------------+------+
| No. |    City   |      Instance      | Load |
+-----+-----------+--------------------+------+
|  1  | Frankfurt | frankfurt-s415-i01 | 51%  |
|  2  | Frankfurt | frankfurt-s415-i02 | 40%  |
+-----+-----------+--------------------+------+
"""
STREAMING = """
| No. | Service                 | Country Code |
| 1   | Netflix US              | US           |
| 2   | Netflix US Firestick    | US           |
| 3   | ZDF                     | DE           |
"""


class ParserTests(unittest.TestCase):
    def test_real_prettytable_country_format(self):
        locations = parse_locations("\x1b[32m" + COUNTRIES + "\x1b[0m", "traffic")
        self.assertEqual([(item.name, item.code) for item in locations], [("Germany", "DE"), ("United States", "US")])

    def test_streaming_services_in_same_country_remain_distinct(self):
        locations = parse_locations(STREAMING, "streaming")
        self.assertEqual(len(locations), 3)
        self.assertEqual(locations[0].service, "Netflix US")
        self.assertNotEqual(locations[0].key, locations[1].key)

    def test_cities_use_name_not_instance_count(self):
        cities = parse_choices(CITIES, "city")
        self.assertEqual(cities[0].value, "Berlin")
        self.assertEqual(cities[0].load, 47)
        self.assertIn("110 servers", cities[0].label)

    def test_servers_use_instance_not_city(self):
        servers = parse_choices(SERVERS, "server")
        self.assertEqual([server.value for server in servers], ["frankfurt-s415-i02", "frankfurt-s415-i01"])

    def test_servers_sort_lowest_load_first_with_unknown_load_last(self):
        source = """| No. | City | Instance | Load |
| 1 | Frankfurt | unknown-a | N/A |
| 2 | Frankfurt | server-b | 10% |
| 3 | Frankfurt | busy | 100% |
| 4 | Frankfurt | server-a | 10% |
| 5 | Frankfurt | idle | 0% |
| 6 | Frankfurt | unknown-b | - |
"""
        choices = parse_choices(source, "server")
        self.assertEqual([choice.value for choice in choices],
                         ["idle", "server-a", "server-b", "busy", "unknown-a", "unknown-b"])
        self.assertEqual([choice.load for choice in choices], [0, 10, 10, 100, None, None])
        # Cities keep their alphabetical order regardless of load.
        cities = parse_choices(CITIES.replace("47%", "90%").replace("48%", "1%"), "city")
        self.assertEqual([choice.value for choice in cities], ["Berlin", "Frankfurt"])

    def test_unknown_output_is_not_connected(self):
        for text in ["", "Connection attempt started", "VPN connection established.", "Traceback: unknown error"]:
            with self.subTest(text=text):
                self.assertEqual(parse_status(text).state, "unknown")

    def test_negative_status_is_not_false_positive(self):
        for text in ["No VPN connections found.", "No Wireguard connections found.", "Wireguard is disconnected.", "VPN is disconnected.", "Not connected"]:
            with self.subTest(text=text):
                self.assertEqual(parse_status(text).state, "disconnected")

    def test_positive_status(self):
        self.assertEqual(parse_status("VPN connection found.").state, "connected")
        self.assertEqual(parse_status("Wireguard connection found.").state, "connected")


class CommandTests(unittest.TestCase):
    def test_wireguard_ignores_openvpn_transport(self):
        self.assertEqual(Selection(country="DE", transport="TCP").connect_args(),
                         ["--traffic", "--country-code", "DE", "--wireguard", "--connect"])

    def test_openvpn_city_and_server(self):
        args = Selection(country="DE", city="Frankfurt", server="frankfurt-s415-i01", protocol="openvpn", transport="TCP").connect_args()
        self.assertEqual(args, ["--traffic", "--country-code", "DE", "--city", "Frankfurt", "--server", "frankfurt-s415-i01", "--openvpn", "--connection", "TCP", "--connect"])

    def test_streaming_service_is_one_argument(self):
        args = Selection(mode="streaming", country="US", service="Netflix US Firestick").connect_args()
        self.assertEqual(args[:2], ["--streaming", "Netflix US Firestick"])

    def test_shell_metacharacters_remain_data(self):
        args = Selection(country="DE", city="City; $(touch /tmp/unwanted)").connect_args()
        self.assertIn("City; $(touch /tmp/unwanted)", args)

    def test_invalid_arguments_rejected(self):
        for changes in [dict(country=""), dict(country="DE --stop"), dict(city="--stop"), dict(city="a\nb"), dict(protocol="bad"), dict(server="orphan")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Selection(**(dict(country="DE") | changes)).connect_args()

    def test_privileged_actions_use_polkit_and_never_shell(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        client = CyberGhostClient(binary="/usr/bin/cyberghostvpn", run=run)
        with patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"):
            client.connect(Selection(country="DE"))
            args, kwargs = run.call_args
            self.assertEqual(args[0][:7], ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/env",
                                          f"SUDO_USER={pwd.getpwuid(os.getuid()).pw_name}",
                                          "LC_ALL=C", "LANG=C", "/usr/bin/cyberghostvpn"])
            self.assertFalse(kwargs.get("shell", False))
            self.assertIsNone(kwargs["timeout"])
            client.disconnect()
            self.assertEqual(run.call_args.args[0][-1], "--stop")

    def test_privileged_account_comes_from_uid_not_environment(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        with (patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"),
              patch("cyberghost_desktop.cli.os.getuid", return_value=1234),
              patch("cyberghost_desktop.cli.pwd.getpwuid", return_value=SimpleNamespace(pw_name="desktop-user")) as lookup,
              patch.dict(os.environ, {"SUDO_USER": "wrong-user", "USER": "root", "LOGNAME": "root"})):
            CyberGhostClient("/usr/bin/cyberghostvpn", run).connect(Selection(country="DE"))
        lookup.assert_called_once_with(1234)
        self.assertIn("SUDO_USER=desktop-user", run.call_args.args[0])
        self.assertNotIn("SUDO_USER=wrong-user", run.call_args.args[0])

    def test_privileged_environment_survives_polkit_sanitization(self):
        # Execute the real env wrapper with an unprivileged probe, simulating
        # pkexec's environment reset without opening authorization or a VPN.
        def sanitized_pkexec(command, **kwargs):
            self.assertEqual(command[:2], ["/usr/bin/pkexec", "--disable-internal-agent"])
            kwargs["env"] = {"USER": "root", "LOGNAME": "root", "HOME": "/root", "PATH": "/usr/bin:/bin"}
            return subprocess.run(command[2:], **kwargs)

        probe = ("import json, os, sys; print(json.dumps({'account': os.environ.get('SUDO_USER'), "
                 "'user': os.environ.get('USER'), 'home': os.environ.get('HOME'), "
                 "'locale': os.environ.get('LC_ALL'), 'language': os.environ.get('LANG'), "
                 "'pythonpath': os.environ.get('PYTHONPATH'), 'args': sys.argv[1:]}))")
        literal = "City; $(printf must-stay-literal)"
        with (patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"),
              patch.dict(os.environ, {"PYTHONPATH": "/must-not-inherit", "SUDO_USER": "wrong-user"})):
            result = CyberGhostClient("/usr/bin/python3", sanitized_pkexec)._call(["-c", probe, literal], privileged=True)
        self.assertEqual(json.loads(result), {
            "account": pwd.getpwuid(os.getuid()).pw_name, "user": "root", "home": "/root",
            "locale": "C", "language": "C", "pythonpath": None, "args": [literal],
        })

    def test_missing_account_stops_before_authorization(self):
        run = Mock()
        with (patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"),
              patch("cyberghost_desktop.cli.pwd.getpwuid", side_effect=KeyError("unknown uid")),
              self.assertRaisesRegex(CliError, "identify your Linux account")):
            CyberGhostClient("/usr/bin/cyberghostvpn", run).disconnect()
        run.assert_not_called()

    def test_read_only_queries_never_prompt_or_connect(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, COUNTRIES, ""))
        client = CyberGhostClient(binary="/usr/bin/cyberghostvpn", run=run)
        client.locations("traffic")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/cyberghostvpn", "--traffic", "--country-code"])
        self.assertEqual(run.call_args.kwargs["timeout"], 40)
        run.return_value.stdout = CITIES
        client.cities(Selection(country="DE"))
        self.assertEqual(run.call_args.args[0][-1], "--city")
        run.return_value.stdout = SERVERS
        client.servers(Selection(country="DE", city="Frankfurt"))
        self.assertEqual(run.call_args.args[0][-1], "--server")

    def test_authorization_cancelled(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 126, "", "dismissed"))
        with patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"), self.assertRaisesRegex(CliError, "cancelled"):
            CyberGhostClient("/usr/bin/cyberghostvpn", run).disconnect()

    def test_read_timeout_reports_retry(self):
        run = Mock(side_effect=subprocess.TimeoutExpired("cyberghostvpn", 40))
        with self.assertRaisesRegex(CliError, "too long"):
            CyberGhostClient("/usr/bin/cyberghostvpn", run).status()

    def test_dns_startup_failure_is_retryable_even_if_cli_exits_zero(self):
        trace = ("Traceback (most recent call last):\n"
                 "socket.gaierror: [Errno -2] Name or service not known\n"
                 "urllib3.exceptions.NameResolutionError: Failed to resolve 'v2-api.cyberghostvpn.com'")
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                run = Mock(return_value=subprocess.CompletedProcess([], exit_code, "", trace))
                with self.assertRaises(NetworkUnavailable) as failure:
                    CyberGhostClient("/usr/bin/cyberghostvpn", run).locations("traffic")
                self.assertIn("server name", str(failure.exception))
                self.assertEqual(failure.exception.details, trace)
                self.assertEqual(run.call_count, 1, "The client itself must not blindly repeat commands")

    def test_network_errors_and_catalog_timeouts_are_retryable(self):
        for error_name in ("ConnectionError", "ConnectTimeout", "ReadTimeout"):
            with self.subTest(error_name=error_name):
                run = Mock(return_value=subprocess.CompletedProcess([], 1, "", f"requests.exceptions.{error_name}: unavailable"))
                with self.assertRaises(NetworkUnavailable):
                    CyberGhostClient("/usr/bin/cyberghostvpn", run).locations("traffic")
        run = Mock(side_effect=subprocess.TimeoutExpired("cyberghostvpn", 40))
        with self.assertRaises(NetworkUnavailable):
            CyberGhostClient("/usr/bin/cyberghostvpn", run).locations("streaming")

    def test_account_and_certificate_errors_are_not_retryable_network_failures(self):
        for message in ("Account setup required", "requests.exceptions.HTTPError: 401 Unauthorized",
                        "requests.exceptions.SSLError: certificate verify failed"):
            with self.subTest(message=message):
                run = Mock(return_value=subprocess.CompletedProcess([], 1, "", message))
                with self.assertRaises(CliError) as failure:
                    CyberGhostClient("/usr/bin/cyberghostvpn", run).locations("traffic")
                self.assertNotIsInstance(failure.exception, NetworkUnavailable)
                self.assertIn(message, failure.exception.details)

    def test_privileged_network_failure_is_not_marked_for_automatic_retry(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 1, "", "socket.gaierror: DNS unavailable"))
        with patch("cyberghost_desktop.cli.shutil.which", return_value="/usr/bin/pkexec"), self.assertRaises(CliError) as failure:
            CyberGhostClient("/usr/bin/cyberghostvpn", run).connect(Selection(country="DE"))
        self.assertNotIsInstance(failure.exception, NetworkUnavailable)
        self.assertEqual(run.call_count, 1)

    def test_unparseable_location_error_is_visible(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, "Account setup required", ""))
        with self.assertRaises(CliError) as failure:
            CyberGhostClient("/usr/bin/cyberghostvpn", run).locations("traffic")
        self.assertIn("Account setup", failure.exception.details)

    def test_demo_never_executes_commands(self):
        client = DemoClient()
        with patch("subprocess.run", side_effect=AssertionError("must not run")):
            client.connect(Selection(country="DE"))
            self.assertEqual(client.status().state, "connected")
            client.disconnect()
            self.assertEqual(client.status().state, "disconnected")


if __name__ == "__main__":
    unittest.main()
