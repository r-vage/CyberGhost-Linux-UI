from concurrent.futures import CancelledError
import subprocess
from threading import Event
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop.ping import PING_FAILED, PING_INVALID, PING_TARGET, PING_UNAVAILABLE, PingError, measure_ping


REPLY = "rtt min/avg/max/mdev = 24.567/24.567/24.567/0.000 ms\n"


class PingTests(unittest.TestCase):
    def setUp(self):
        self.cancel = Event()
        self.process = Mock(returncode=0)
        self.process.poll.return_value = 0
        self.process.communicate.return_value = (REPLY, "")
        self.which = patch("cyberghost_desktop.ping.shutil.which", return_value="/usr/bin/ping").start()
        self.popen = patch("cyberghost_desktop.ping.subprocess.Popen", return_value=self.process).start()
        self.addCleanup(patch.stopall)

    def test_measures_rtt_with_bounded_unprivileged_ipv4_command(self):
        self.assertEqual(measure_ping(self.cancel), 24.567)
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ["/usr/bin/ping", "-n", "-4", "-q", "-c", "1", "-W", "2", "-w", "3", PING_TARGET])
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("shell", kwargs)
        self.process.kill.assert_not_called()

    def test_preserves_submillisecond_precision(self):
        self.process.communicate.return_value = ("rtt min/avg/max/mdev = 0.034/0.034/0.034/0.000 ms", "")
        self.assertEqual(measure_ping(self.cancel), 0.034)

    def test_no_reply_is_not_zero_latency(self):
        self.process.returncode = 1
        self.process.communicate.return_value = ("1 packets transmitted, 0 received, 100% packet loss", "")
        self.assertIsNone(measure_ping(self.cancel))

    def test_reports_permission_errors(self):
        self.process.returncode = 2
        self.process.communicate.return_value = ("", "ping: socket: Operation not permitted\n")
        with self.assertRaises(PingError) as raised:
            measure_ping(self.cancel)
        self.assertEqual(str(raised.exception), PING_FAILED)
        self.assertIn("Operation not permitted", raised.exception.details)

    def test_rejects_missing_or_nonfinite_timings(self):
        for output in ("unexpected success", "rtt min/avg/max/mdev = 0/nan/0/0 ms",
                       "rtt min/avg/max/mdev = 0/" + "9" * 400 + "/0/0 ms"):
            with self.subTest(output=output[:50]):
                self.process.communicate.return_value = (output, "")
                with self.assertRaisesRegex(PingError, PING_INVALID):
                    measure_ping(self.cancel)

    def test_missing_ping_is_actionable(self):
        self.which.return_value = None
        with self.assertRaisesRegex(PingError, PING_UNAVAILABLE):
            measure_ping(self.cancel)
        self.popen.assert_not_called()

    def test_reports_launch_failures(self):
        self.popen.side_effect = OSError("Permission denied")
        with self.assertRaises(PingError) as raised:
            measure_ping(self.cancel)
        self.assertEqual(str(raised.exception), PING_FAILED)
        self.assertEqual(raised.exception.details, "Permission denied")

    def test_cancelled_queued_probe_never_starts(self):
        self.cancel.set()
        with self.assertRaises(CancelledError):
            measure_ping(self.cancel)
        self.popen.assert_not_called()

    def test_stop_kills_and_reaps_a_probe_waiting_for_a_reply(self):
        self.process.poll.return_value = None

        def communicate(**kwargs):
            if kwargs:
                self.cancel.set()
                raise subprocess.TimeoutExpired("ping", 0.1)
            return "", ""

        self.process.communicate.side_effect = communicate
        with self.assertRaises(CancelledError):
            measure_ping(self.cancel)
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.communicate.call_count, 2)

    def test_discards_a_reply_that_arrives_after_stop(self):
        def communicate(**_kwargs):
            self.cancel.set()
            return REPLY, ""

        self.process.communicate.side_effect = communicate
        with self.assertRaises(CancelledError):
            measure_ping(self.cancel)

    def test_hard_deadline_kills_and_reaps_hung_ping(self):
        self.process.poll.return_value = None
        with patch("cyberghost_desktop.ping.time.monotonic", side_effect=[10.0, 14.01]):
            self.assertIsNone(measure_ping(self.cancel))
        self.process.kill.assert_called_once()
        self.process.communicate.assert_called_once_with()
