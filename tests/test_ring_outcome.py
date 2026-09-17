"""v=1 ring-outcome consumer: vendored fixtures plus phase/exit edges."""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from alb import ring_outcome
from alb.bridge import run


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "vendor" / "doorbell-outcome-v1"


def _rows(name: str):
    path = FIXTURES / name
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        yield line.split("\t")


def _pinned_sha256(name: str) -> str:
    source = json.loads(
        (ROOT / "vendor" / "doorbell-outcome-source.json").read_text(encoding="utf-8"))
    return source["files"][name]


class FixtureAccepted(unittest.TestCase):
    def test_every_accepted_line_parses_to_its_columns(self):
        count = 0
        for line, outcome, reason, target in _rows("accepted.tsv"):
            count += 1
            got = ring_outcome.parse_line(line)
            self.assertIsInstance(got, ring_outcome.Classification, line)
            self.assertEqual((got.outcome, got.reason, got.target),
                             (outcome, reason, target), line)
        self.assertGreaterEqual(count, 21)


class FixtureRejected(unittest.TestCase):
    # Canonical expectation, tied to the vendored pin in
    # vendor/doorbell-outcome-source.json (cmux 47b818a6,
    # conformance/doorbell-outcome-v1): rejected.tsv carries exactly 42 data
    # rows (one private-stack row lives only in the private workspace, by
    # design). Equality is the non-vacuous check — every pinned row must be
    # exercised, and a truncated or swapped fixture fails here.
    EXPECTED_ROWS = 42

    def test_vendored_rejected_bytes_match_the_pin(self):
        digest = hashlib.sha256(
            (FIXTURES / "rejected.tsv").read_bytes()).hexdigest()
        self.assertEqual(digest, _pinned_sha256("rejected.tsv"))

    def test_every_rejected_line_fails_parse(self):
        count = 0
        for line, _why in _rows("rejected.tsv"):
            count += 1
            got = ring_outcome.parse_line(line)
            self.assertIsInstance(got, str, line)
        self.assertEqual(count, self.EXPECTED_ROWS)


class FixtureParseCases(unittest.TestCase):
    def test_consumer_cases_match_independent_classify(self):
        count = 0
        for stdout, stderr, exit_, expected, retry, _note in _rows("parse-cases.tsv"):
            count += 1
            timed_out = exit_ == "timeout"
            code = 0 if timed_out or exit_ == "" else int(exit_)
            got = ring_outcome.classify(
                stdout.replace("\\n", "\n"), stderr.replace("\\n", "\n"),
                exit_code=code, timed_out=timed_out)
            self.assertTrue(
                expected in {got.status, got.outcome, got.reason},
                f"{_note}: expected {expected} got {got}")
            self.assertEqual(got.retry, retry == "yes", _note)
        self.assertGreaterEqual(count, 13)


class PhaseAndExitEdges(unittest.TestCase):
    def test_partial_contract_plus_timeout_is_unconfirmed(self):
        got = ring_outcome.classify(
            "doorbell-outcome v=1 outc", timed_out=True)
        self.assertEqual(got.status, "unconfirmed")
        self.assertFalse(got.retry)

    def test_missing_executable_is_adapter_unavailable_without_retry(self):
        got = ring_outcome.classify("", missing_executable=True)
        self.assertEqual(got.reason, "adapter_unavailable")
        self.assertFalse(got.retry)

    def test_stderr_submitted_cannot_succeed(self):
        got = ring_outcome.classify(
            "",
            "doorbell-outcome v=1 outcome=submitted reason=- target=surface:1",
            exit_code=0)
        self.assertEqual(got.status, "unparseable")
        self.assertEqual(got.health, "failing")

    def test_exit_zero_without_a_line_is_unparseable(self):
        got = ring_outcome.classify("log only\n", exit_code=0)
        self.assertEqual(got.status, "unparseable")
        self.assertFalse(got.retry)

    def test_helper_timeout_is_the_only_retry(self):
        yes = ring_outcome.classify(
            "doorbell-outcome v=1 outcome=no_live_surface reason=helper_timeout target=-")
        no = ring_outcome.classify(
            "doorbell-outcome v=1 outcome=no_live_surface reason=send_failed target=-")
        self.assertTrue(yes.retry)
        self.assertFalse(no.retry)


class BusRingConsumer(unittest.TestCase):
    def _run(self, stdout="", stderr="", returncode=0, side_effect=None):
        if side_effect is None:
            completed = mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)
            runner = mock.Mock(return_value=completed)
        else:
            runner = mock.Mock(side_effect=side_effect)
        with mock.patch.object(run.subprocess, "run", runner), \
             mock.patch.object(run.time, "sleep"):
            try:
                run._bus_ring("agent", "info", "an-id", binary="helper")
                ok = True
            except run.RingNotDelivered as exc:
                ok = False
                return exc, runner
        return None, runner if ok else (None, runner)

    def test_contract_submitted_returns(self):
        exc, _ = self._run(
            "doorbell-outcome v=1 outcome=submitted reason=- target=surface:1")
        self.assertIsNone(exc)

    def test_legacy_prose_is_not_submitted(self):
        exc, runner = self._run("bus: doorbell submitted to agent on surface:1")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.classification.status, "unparseable")
        self.assertEqual(runner.call_count, 1)

    def test_helper_timeout_retries(self):
        timeout = mock.Mock(
            returncode=0,
            stdout="doorbell-outcome v=1 outcome=no_live_surface reason=helper_timeout target=-",
            stderr="")
        ok = mock.Mock(
            returncode=0,
            stdout="doorbell-outcome v=1 outcome=submitted reason=- target=surface:1",
            stderr="")
        exc, runner = self._run(side_effect=[timeout, ok])
        self.assertIsNone(exc)
        self.assertEqual(runner.call_count, 2)

    def test_send_failed_does_not_retry(self):
        exc, runner = self._run(
            "doorbell-outcome v=1 outcome=no_live_surface reason=send_failed target=-")
        self.assertEqual(exc.classification.reason, "send_failed")
        self.assertEqual(runner.call_count, 1)

    def test_caller_timeout_empty_stdout_does_not_retry(self):
        exc, runner = self._run(
            side_effect=subprocess.TimeoutExpired("helper", 10))
        self.assertEqual(exc.classification.reason, "unconfirmed")
        self.assertEqual(runner.call_count, 1)

    def test_missing_executable(self):
        with mock.patch.object(run.subprocess, "run",
                               side_effect=FileNotFoundError("helper")):
            with self.assertRaises(run.RingNotDelivered) as ctx:
                run._bus_ring("agent", "info", "an-id", binary="missing")
        self.assertEqual(ctx.exception.classification.reason, "adapter_unavailable")


if __name__ == "__main__":
    unittest.main()
