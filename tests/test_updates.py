from http.client import IncompleteRead
from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from cyberghost_desktop import __version__
from cyberghost_desktop.updates import (
    CHECK_TIMEOUT, MAX_VERSION_BYTES, NETWORK_ERROR, VERSION_ERROR, VERSION_URL,
    NoUpdateRedirects, UpdateError, check_for_updates, version_numbers,
)


class UpdateTests(unittest.TestCase):
    def check(self, body, current="0.1.3"):
        response = BytesIO(body)
        opener = Mock()
        opener.open.return_value = response
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener) as build:
            result = check_for_updates(current)
        self.assertTrue(response.closed)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, VERSION_URL)
        self.assertEqual(request.get_header("Accept"), "text/plain")
        self.assertEqual(request.get_header("User-agent"), f"CyberGhostDesktop/{current}")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], CHECK_TIMEOUT)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], NoUpdateRedirects)
        return result

    def test_numeric_comparison_handles_patch_minor_major_and_equal_versions(self):
        for current, latest, state in [
            ("0.1.9", "0.1.10", "available"), ("0.9.12", "0.10.0", "available"),
            ("1.99.99", "2.0.0", "available"), ("0.1.4", "0.1.4", "current"),
            ("0.1.4", "0.1.3", "ahead"), ("1.0.0", "0.99.99", "ahead"),
        ]:
            with self.subTest(current=current, latest=latest):
                result = self.check(f'__version__ = "{latest}"\n'.encode(), current)
                self.assertEqual(result.state, state)
                self.assertEqual(result.latest_version, latest)

    def test_reads_literal_version_without_executing_downloaded_python(self):
        result = self.check(b"raise RuntimeError('remote code must not execute')\r\n__version__\t= '0.1.4'\r\n")
        self.assertEqual(result.latest_version, "0.1.4")

    def test_current_package_version_is_supported(self):
        self.assertEqual(self.check(f'__version__ = "{__version__}"'.encode(), __version__).state, "current")

    def test_rejects_missing_ambiguous_or_nonliteral_remote_versions(self):
        for body in [b"", b"<html>Not Found</html>", b"\xff", b'__version__ = "0.1.4\'\n',
                     b'__version__ = "0.1.4"\n__version__ = "9.0.0"',
                     b'__version__ = str(14)', b'__version__ = "0.1.4" + ".5"']:
            with self.subTest(body=body), self.assertRaisesRegex(UpdateError, VERSION_ERROR):
                self.check(body)

    def test_unsupported_versions_are_not_reported_as_updates(self):
        for version in ["v0.1.4", "0.1", "0.1.4.1", "0.1.4rc1", "0.1.4-dev", "0.1.4+local",
                        "00.1.4", "-1.2.3", "1.٢.3", "1.2.3 ", "1." + "9" * 70 + ".0"]:
            with self.subTest(version=version), self.assertRaises(UpdateError):
                self.check(f'__version__ = "{version}"'.encode())

    def test_response_size_is_bounded_and_oversized_data_is_rejected(self):
        opener, response = Mock(), Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"x" * (MAX_VERSION_BYTES + 1)
        opener.open.return_value = response
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener), self.assertRaises(UpdateError):
            check_for_updates()
        response.read.assert_called_once_with(MAX_VERSION_BYTES + 1)
        response.__exit__.assert_called_once()

    def test_timeout_dns_http_and_partial_response_failures_are_reported(self):
        for error in [TimeoutError(), URLError("DNS unavailable"),
                      HTTPError(VERSION_URL, 404, "Not Found", {}, None), IncompleteRead(b"partial")]:
            opener = Mock()
            opener.open.side_effect = error
            with (self.subTest(error=type(error).__name__),
                  patch("cyberghost_desktop.updates.build_opener", return_value=opener),
                  self.assertRaisesRegex(UpdateError, NETWORK_ERROR)):
                check_for_updates()

    def test_invalid_installed_version_does_not_make_a_network_request(self):
        with patch("cyberghost_desktop.updates.build_opener") as build, self.assertRaises(ValueError):
            check_for_updates("development")
        build.assert_not_called()

    def test_refuses_redirects(self):
        self.assertIsNone(NoUpdateRedirects().redirect_request(Request(VERSION_URL), None, 302, "Moved", {},
                                                             "https://example.org/version.py"))

    def test_version_parser_rejects_non_strings(self):
        for value in (None, 123, [0, 1, 4]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_numbers(value)


if __name__ == "__main__":
    unittest.main()
