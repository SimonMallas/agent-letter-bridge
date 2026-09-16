"""Helper probe: detect doorbell-outcome v=1 without treating stdout as a ring."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from alb.helper_probe import (
    INCOMPATIBLE, INJECTED, PROBE_ID, PROBE_RECIPIENT, PROBE_TIMEOUT,
    inspect_helper,
)

NLS = (
    "doorbell-outcome v=1 outcome=no_live_surface "
    "reason=unknown_participant target=-"
)
SUBMITTED = (
    "doorbell-outcome v=1 outcome=submitted reason=- target=surface:1"
)
PASTED = (
    "doorbell-outcome v=1 outcome=pasted_not_submitted "
    "reason=enter_failed target=surface:1"
)
DOORBELL = "📬 letterbox doorbell: unacked info in inbox/ — please check"


def _ok(stdout, returncode=0):
    def runner(argv, **kw):
        runner.argv = argv
        runner.kw = kw
        return mock.Mock(returncode=returncode, stdout=stdout, stderr="")
    runner.argv = None
    runner.kw = None
    return runner


class InspectHelper(unittest.TestCase):
    def test_missing_binary(self):
        status, msg = inspect_helper("")
        self.assertEqual(status, "missing")
        self.assertIn(INCOMPATIBLE, msg)

    def test_legacy_prose_is_incompatible(self):
        status, msg = inspect_helper("bus.sh", runner=_ok(
            "bus: doorbell no_live_surface unknown_participant for x"))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INCOMPATIBLE)

    def test_v1_nls_line_is_ok(self):
        status, msg = inspect_helper("bus.sh", runner=_ok(NLS))
        self.assertEqual(status, "ok")
        self.assertIn("emits doorbell-outcome v=1", msg)

    def test_probe_argv_is_the_reserved_recipient(self):
        runner = _ok(NLS)
        inspect_helper("/opt/bus.sh", runner=runner)
        self.assertEqual(
            runner.argv,
            ["/opt/bus.sh", "ring", "alb-doctor-probe", "info", PROBE_ID],
        )
        self.assertEqual(PROBE_RECIPIENT, "alb-doctor-probe")

    def test_probe_timeout_is_three_seconds(self):
        runner = _ok(NLS)
        inspect_helper("bus.sh", runner=runner)
        self.assertEqual(runner.kw.get("timeout"), 3)
        self.assertEqual(PROBE_TIMEOUT, 3)

    def test_a_hung_helper_returns_incompatible_via_timeout(self):
        def runner(argv, **kw):
            self.assertEqual(kw.get("timeout"), 3)
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INCOMPATIBLE)

    def test_a_doorbell_line_anywhere_is_injected(self):
        status, msg = inspect_helper("bus.sh", runner=_ok(DOORBELL))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INJECTED)

    def test_outcome_then_doorbell_is_injected(self):
        status, msg = inspect_helper(
            "bus.sh", runner=_ok(NLS + "\n" + DOORBELL))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INJECTED)

    def test_duplicate_contract_lines_are_incompatible(self):
        status, msg = inspect_helper(
            "bus.sh", runner=_ok(NLS + "\n" + NLS))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INCOMPATIBLE)

    def test_submitted_for_the_probe_recipient_is_unsafe(self):
        status, msg = inspect_helper("bus.sh", runner=_ok(SUBMITTED))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INJECTED)

    def test_pasted_for_the_probe_recipient_is_unsafe(self):
        status, msg = inspect_helper("bus.sh", runner=_ok(PASTED))
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INJECTED)

    def test_not_found(self):
        def runner(argv, **kw):
            raise FileNotFoundError("bus.sh")
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "missing")

    def test_timeout_without_a_contract_line(self):
        def runner(argv, **kw):
            raise subprocess.TimeoutExpired("bus.sh", 3)
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INCOMPATIBLE)
