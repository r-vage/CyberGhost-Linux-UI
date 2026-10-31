from http.client import IncompleteRead
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from cyberghost_desktop import __version__
from cyberghost_desktop.updates import (
    CHECK_TIMEOUT, MAX_VERSION_BYTES, NETWORK_ERROR, REFERENCE_URL, VERSION_ERROR, VERSION_URL,
    NoUpdateRedirects, UpdateError, check_for_updates, version_numbers,
)

COMMIT = "a" * 40


def reference(commit=COMMIT):
    return json.dumps({"ref": "refs/heads/main", "object": {"type": "commit", "sha": commit}}).encode()


class UpdateTests(unittest.TestCase):
    def check(self, body, current="0.1.3"):
        responses = [BytesIO(reference()), BytesIO(body)]
        opener = Mock()
        opener.open.side_effect = responses
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener) as build:
            result = check_for_updates(current)
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(opener.open.call_count, 2)
        lookup, version = [call.args[0] for call in opener.open.call_args_list]
        self.assertTrue(lookup.full_url.startswith(REFERENCE_URL + "?check="))
        self.assertEqual(lookup.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(lookup.get_header("X-github-api-version"), "2026-03-10")
        self.assertEqual(version.full_url, VERSION_URL.format(commit=COMMIT))
        self.assertEqual(version.get_header("Accept"), "text/plain")
        for call in opener.open.call_args_list:
            self.assertEqual(call.args[0].get_header("User-agent"), f"CyberGhostDesktop/{current}")
            self.assertEqual(call.args[0].get_header("Cache-control"), "no-cache")
            self.assertEqual(call.args[0].get_header("Pragma"), "no-cache")
            self.assertEqual(call.kwargs["timeout"], CHECK_TIMEOUT)
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

    def test_stale_raw_branch_content_is_not_used(self):
        opener = Mock()

        def download(request, **_kwargs):
            if request.full_url.startswith(REFERENCE_URL + "?check="):
                return BytesIO(reference())
            if request.full_url == VERSION_URL.format(commit=COMMIT):
                return BytesIO(b'__version__ = "0.1.4"')
            if request.full_url == VERSION_URL.format(commit="main"):
                return BytesIO(b'__version__ = "0.1.3"')  # The stale CDN response.
            self.fail(f"Unexpected URL: {request.full_url}")

        opener.open.side_effect = download
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener):
            result = check_for_updates("0.1.3")
        self.assertEqual((result.latest_version, result.state), ("0.1.4", "available"))
        self.assertEqual(opener.open.call_count, 2)

    def test_each_check_resolves_main_afresh_and_uses_the_returned_commit(self):
        second_commit = "b" * 64
        opener = Mock()
        opener.open.side_effect = [BytesIO(reference()), BytesIO(b'__version__ = "0.1.4"'),
                                   BytesIO(reference(second_commit)), BytesIO(b'__version__ = "0.1.5"')]
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener):
            first = check_for_updates("0.1.3")
            second = check_for_updates("0.1.3")
        self.assertEqual((first.latest_version, second.latest_version), ("0.1.4", "0.1.5"))
        requests = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertNotEqual(requests[0], requests[2])
        self.assertTrue(all(url.startswith(REFERENCE_URL + "?check=") for url in requests[::2]))
        self.assertEqual(requests[1::2], [VERSION_URL.format(commit=COMMIT), VERSION_URL.format(commit=second_commit)])

    def test_invalid_branch_metadata_stops_before_downloading_any_file(self):
        metadata = [b"", b"<html>error</html>", b"\xff", b"null", b"[]", b"{}",
                    b'{"ref":"refs/heads/main","object":null}']
        for branch, kind, commit in [("refs/heads/other", "commit", COMMIT), ("refs/heads/main", "tag", COMMIT),
                                     *[("refs/heads/main", "commit", sha) for sha in
                                       (None, 123, True, "", "main", "../main", "x" * 40, "a" * 39, "a" * 65)]]:
            metadata.append(json.dumps({"ref": branch, "object": {"type": kind, "sha": commit}}).encode())
        for body in metadata:
            response, opener = BytesIO(body), Mock()
            opener.open.return_value = response
            with (self.subTest(body=body), patch("cyberghost_desktop.updates.build_opener", return_value=opener),
                  self.assertRaisesRegex(UpdateError, VERSION_ERROR)):
                check_for_updates()
            self.assertEqual(opener.open.call_count, 1)
            self.assertTrue(response.closed)

    def test_response_supplied_urls_cannot_redirect_version_download(self):
        data = json.loads(reference())
        data["url"] = "https://example.invalid/branch"
        data["object"]["url"] = "https://example.invalid/version"
        opener = Mock()
        opener.open.side_effect = [BytesIO(json.dumps(data).encode()), BytesIO(b'__version__ = "0.1.4"')]
        with patch("cyberghost_desktop.updates.build_opener", return_value=opener):
            check_for_updates("0.1.3")
        self.assertEqual(opener.open.call_args.args[0].full_url, VERSION_URL.format(commit=COMMIT))

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
        for stage in (0, 1):
            opener, response = Mock(), Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b"x" * (MAX_VERSION_BYTES + 1)
            opener.open.side_effect = ([BytesIO(reference())] if stage else []) + [response]
            with (self.subTest(stage=stage), patch("cyberghost_desktop.updates.build_opener", return_value=opener),
                  self.assertRaisesRegex(UpdateError, VERSION_ERROR)):
                check_for_updates()
            self.assertEqual(opener.open.call_count, stage + 1)
            response.read.assert_called_once_with(MAX_VERSION_BYTES + 1)
            response.__exit__.assert_called_once()

    def test_timeout_dns_http_and_partial_response_failures_are_reported(self):
        for stage in (0, 1):
            for error in [TimeoutError(), URLError("DNS unavailable"),
                          HTTPError(REFERENCE_URL, 403, "Rate limit", {}, None),
                          HTTPError(VERSION_URL.format(commit=COMMIT), 404, "Not Found", {}, None), IncompleteRead(b"partial")]:
                opener = Mock()
                opener.open.side_effect = ([BytesIO(reference())] if stage else []) + [error]
                with (self.subTest(stage=stage, error=type(error).__name__),
                      patch("cyberghost_desktop.updates.build_opener", return_value=opener),
                      self.assertRaisesRegex(UpdateError, NETWORK_ERROR)):
                    check_for_updates()
                self.assertEqual(opener.open.call_count, stage + 1)

    def test_invalid_installed_version_does_not_make_a_network_request(self):
        with patch("cyberghost_desktop.updates.build_opener") as build, self.assertRaises(ValueError):
            check_for_updates("development")
        build.assert_not_called()

    def test_refuses_redirects(self):
        for url in (REFERENCE_URL, VERSION_URL.format(commit=COMMIT)):
            self.assertIsNone(NoUpdateRedirects().redirect_request(Request(url), None, 302, "Moved", {},
                                                                 "https://example.org/version.py"))

    def test_version_parser_rejects_non_strings(self):
        for value in (None, 123, [0, 1, 4]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_numbers(value)


if __name__ == "__main__":
    unittest.main()
