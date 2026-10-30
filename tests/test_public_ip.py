from io import BytesIO
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from cyberghost_desktop.public_ip import NoRedirect, TRACE_URL, fetch_public_ip


class PublicIPTests(unittest.TestCase):
    def lookup(self, body):
        opener = Mock()
        opener.open.return_value = BytesIO(body)
        with patch("cyberghost_desktop.public_ip.build_opener", return_value=opener) as build:
            result = fetch_public_ip()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, TRACE_URL)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 6)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], NoRedirect)
        return result

    def test_extracts_ipv4_and_country_only(self):
        result = self.lookup(b"h=example\nip=8.8.8.8\nloc=DE\nother=ignored\n")
        self.assertEqual(result.display, "8.8.8.8 · DE")

    def test_country_is_optional_and_validated(self):
        self.assertEqual(self.lookup(b"ip=8.8.8.8\nloc=<script>\n").display, "8.8.8.8")

    def test_rejects_private_ipv6_invalid_and_oversized_responses(self):
        for body in [b"ip=192.168.0.1", b"ip=224.0.0.1", b"ip=::1", b"ip=2001:4860:4860::8888", b"ip=not-an-ip",
                     b"<html>error</html>", b"ip=8.8.8.8\n" + b"x" * 16384]:
            with self.subTest(body=body[:30]), self.assertRaises(ValueError):
                self.lookup(body)

    def test_network_failure_propagates_without_caching(self):
        opener = Mock()
        opener.open.side_effect = TimeoutError("timeout")
        with patch("cyberghost_desktop.public_ip.build_opener", return_value=opener), self.assertRaises(TimeoutError):
            fetch_public_ip()

    def test_rejects_redirects_to_other_destinations(self):
        with self.assertRaises(HTTPError):
            NoRedirect().redirect_request(Request(TRACE_URL), None, 302, "redirect", {}, "http://example.org")
