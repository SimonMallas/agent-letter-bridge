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
    def _timeouts_passed(self, call_fn):
        seen = []

        def fake_run(argv, **kw):
            seen.append(kw.get("timeout"))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(subprocess, "run", fake_run):
            try:
                call_fn()
            except Exception:  # noqa: BLE001 - we are inspecting the call, not the result
                pass
        return seen

    def test_the_cmux_ring_cannot_hang(self):
        seen = self._timeouts_passed(
            lambda: cmux_transport.Cmux().deliver("surface:1", "a line"))
        self.assertTrue(seen, "no subprocess call observed")
        for timeout in seen:
            self.assertIsNotNone(timeout, "a ring with no timeout can stop the bridge")

    def test_the_tmux_ring_cannot_hang(self):
        seen = self._timeouts_passed(
            lambda: tmux_transport.Tmux().deliver("%1", "a line"))
        self.assertTrue(seen)
        for timeout in seen:
            self.assertIsNotNone(timeout)

    def test_the_integrated_ring_cannot_hang(self):
        seen = self._timeouts_passed(
            lambda: run._bus_ring("codex", "info", "some-id", binary="/bin/true"))
        self.assertTrue(seen)
        for timeout in seen:
            self.assertIsNotNone(timeout)
