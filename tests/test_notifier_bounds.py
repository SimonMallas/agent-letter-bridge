"""No ring call may block forever.

Pi's allowance analysis: while any notifier call can hang unbounded, no finite
running allowance is PROVABLE - the number would be a claim about a path with
no ceiling. All three ring paths lacked a timeout, so the bridge could sit in
a subprocess indefinitely with its heartbeat frozen, looking exactly like the
death a supervisor is meant to detect.

A bounded ring can fail. An unbounded one can stop the bridge.
"""
import math
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
            self.assertTrue(math.isfinite(timeout),
                            "infinity is positive and is not a bound")

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
    """Pi drove real sleeping children rather than inspecting keywords - and
    then caught my attempt to preserve that from calling production at all.

    My first version ran subprocess.run directly with a timeout, which proves
    Python enforces timeouts and says nothing about our adapters. He proved it
    by attribution: patched _run to raise if invoked, ran this test, and it
    passed with the adapter call count at ZERO. A test named for our
    enforcement that never reaches our enforcement.

    These go through the real _run and the real _bus_ring."""

    def _sleeper(self):
        return [sys.executable, "-c", "import time; time.sleep(5)"]

    def test_a_hanging_cmux_call_is_cut_off(self):
        with mock.patch.object(cmux_transport, "RING_TIMEOUT", 0.1):
            with self.assertRaises(subprocess.TimeoutExpired):
                cmux_transport._run(self._sleeper())

    def test_a_hanging_tmux_call_is_cut_off(self):
        with mock.patch.object(tmux_transport, "RING_TIMEOUT", 0.1):
            with self.assertRaises(subprocess.TimeoutExpired):
                tmux_transport._run(self._sleeper())

    def test_a_hanging_integrated_ring_is_cut_off(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            # Python rather than a shell, and it prints the success marker
            # AFTER sleeping. Pi's polish, and the reason is attribution: with
            # a shell that never reports success, removing the timeout makes
            # this test fail at the helper-output check rather than at the
            # missing TimeoutExpired - red for a downstream reason instead of
            # the one the test is named for. A sleeper that eventually
            # succeeds fails only where it should. It also avoids leaving a
            # shell's orphaned child behind when the shell itself is killed.
            sleeper = pathlib.Path(tmp) / "slow-helper"
            sleeper.write_text(
                f"#!{sys.executable}\n"
                "import time\n"
                "time.sleep(5)\n"
                "print('doorbell submitted')\n",
                encoding="utf-8")
            sleeper.chmod(0o700)
            with mock.patch.object(run, "RING_TIMEOUT", 0.1):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run._bus_ring("codex", "info", "an-id", binary=str(sleeper))

    def test_no_second_cmux_step_follows_a_timed_out_first(self):
        """A half-fired ring is worse than one that did not fire."""
        calls = []

        def timing_out(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

        with mock.patch.object(subprocess, "run", timing_out):
            with self.assertRaises(subprocess.TimeoutExpired):
                cmux_transport.Cmux().deliver("surface:1", "a line")
        self.assertEqual(len(calls), 1)

    def test_no_second_tmux_step_follows_a_timed_out_first(self):
        calls = []

        def timing_out(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

        with mock.patch.object(subprocess, "run", timing_out):
            with self.assertRaises(subprocess.TimeoutExpired):
                tmux_transport.Tmux().deliver("%1", "a line")
        self.assertEqual(len(calls), 1)
