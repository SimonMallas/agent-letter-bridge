"""Helper probe: detect doorbell-outcome v=1 without treating stdout as a ring."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from alb.helper_probe import INCOMPATIBLE, inspect_helper


class InspectHelper(unittest.TestCase):
    def test_missing_binary(self):
        status, msg = inspect_helper("")
        self.assertEqual(status, "missing")
        self.assertIn(INCOMPATIBLE, msg)

    def test_legacy_prose_is_incompatible(self):
        def runner(argv, **kw):
            return mock.Mock(
                returncode=0,
                stdout="bus: doorbell no_live_surface unknown_participant for x",
                stderr="")
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "incompatible")
        self.assertEqual(msg, INCOMPATIBLE)

    def test_v1_line_is_ok(self):
        def runner(argv, **kw):
            return mock.Mock(
                returncode=0,
                stdout="doorbell-outcome v=1 outcome=no_live_surface reason=unknown_participant target=-",
                stderr="")
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "ok")
        self.assertIn("emits doorbell-outcome v=1", msg)

    def test_a_doorbell_line_on_stdout_is_not_ok(self):
        def runner(argv, **kw):
            return mock.Mock(
                returncode=0,
                stdout="📬 letterbox doorbell: unacked info in inbox/ — please check",
                stderr="")
        status, msg = inspect_helper("bus.sh", runner=runner)
        self.assertEqual(status, "incompatible")

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
