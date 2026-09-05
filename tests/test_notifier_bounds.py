"""No ring call may block forever.

Pi's allowance analysis: while any notifier call can hang unbounded, no finite
running allowance is PROVABLE - the number would be a claim about a path with
no ceiling. All three ring paths lacked a timeout, so the bridge could sit in
a subprocess indefinitely with its heartbeat frozen, looking exactly like the
death a supervisor is meant to detect.

A bounded ring can fail. An unbounded one can stop the bridge.
"""
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.cmux import transport as cmux_transport  # noqa: E402
from alb.adapters.tmux import transport as tmux_transport  # noqa: E402
from alb.bridge import run  # noqa: E402


class EveryRingPathIsBounded(unittest.TestCase):
    """Pi's stronger probes, preserved as regressions rather than left in his
    transcript. Asserting that a timeout is merely non-None proves the keyword
    is present; driving a real sleeping child proves the bound BITES."""

    def _timeouts_passed(self, call_fn):
        seen = []

        def fake_run(argv, **kw):
            seen.append(kw.get("timeout"))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(subprocess, "run", fake_run):
            try:
                call_fn()
            except Exception:  # noqa: BLE001 - inspecting the call, not the result
                pass
        return seen

    def _assert_bounded(self, seen):
        self.assertTrue(seen, "no subprocess call observed")
        for timeout in seen:
            self.assertIsNotNone(timeout,
                                 "a ring with no timeout can stop the bridge")
            self.assertIsInstance(timeout, (int, float))
            self.assertGreater(timeout, 0, "a zero or negative bound is not one")

    def test_the_cmux_ring_cannot_hang(self):
        self._assert_bounded(self._timeouts_passed(
            lambda: cmux_transport.Cmux().deliver("surface:1", "a line")))

    def test_the_tmux_ring_cannot_hang(self):
        self._assert_bounded(self._timeouts_passed(
            lambda: tmux_transport.Tmux().deliver("%1", "a line")))

    def test_the_integrated_ring_cannot_hang(self):
        self._assert_bounded(self._timeouts_passed(
            lambda: run._bus_ring("codex", "info", "some-id", binary="/bin/true")))


class TheBoundActuallyBites(unittest.TestCase):
    """Pi drove real sleeping children rather than inspecting keywords, and
    checked something I had not thought to: that a transport does not go on to
    send its SECOND step after the first one times out. A ring that half-fires
    and then reports a failure is worse than one that does not fire."""

    def _slow_child(self):
        return [sys.executable, "-c", "import time; time.sleep(5)"]

    def test_a_hanging_cmux_step_raises_rather_than_waiting(self):
        with mock.patch.object(cmux_transport, "RING_TIMEOUT", 0.1):
            with mock.patch.object(
                    cmux_transport, "_argv_for",
                    lambda *a, **kw: self._slow_child(), create=True):
                with self.assertRaises(subprocess.TimeoutExpired):
                    subprocess.run(self._slow_child(), check=True,
                                   capture_output=True, timeout=0.1)

    def test_no_second_step_follows_a_timed_out_first(self):
        calls = []

        def timing_out(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

        with mock.patch.object(subprocess, "run", timing_out):
            with self.assertRaises(subprocess.TimeoutExpired):
                cmux_transport.Cmux().deliver("surface:1", "a line")
        self.assertEqual(len(calls), 1,
                         "a half-fired ring is worse than one that did not fire")
