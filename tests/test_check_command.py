"""`alb --check`: the one command a waking agent runs.

It must be safe to run constantly, say what to do rather than what it saw,
and never touch the platform - an agent checking whether its relay is alive
must not need the relay to be alive.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALB = str(ROOT / ".venv" / "bin" / "alb")


class TheCommandAnAgentRunsOnWaking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, age=0, state="running", reason=None):
        payload = {"heartbeat": time.time() - age, "state": state}
        if reason:
            payload["reason"] = reason
        (self.root / "state" / "health.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def check(self):
        return subprocess.run([ALB, "--check", "--root", str(self.root)],
                              capture_output=True, text=True)

    def test_a_healthy_relay_exits_zero_and_says_so(self):
        self.write(age=3)
        got = self.check()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("running", got.stdout.lower())

    def test_a_dead_relay_exits_nonzero_so_a_script_can_branch(self):
        self.write(age=900)
        got = self.check()
        self.assertEqual(got.returncode, 2)
        self.assertIn("restart", got.stdout.lower())

    def test_a_waiting_relay_exits_zero_because_nothing_should_be_done(self):
        """Exit codes carry the ACTION, not the state. An agent that branches
        on 'is it degraded' would restart a bridge that is behaving."""
        # With its reason: a degraded record that cannot say WHY is now
        # investigated rather than accepted, because a legitimate wait and a
        # stuck process look identical without it.
        self.write(age=200, state="degraded", reason="throttled_429")
        got = self.check()
        self.assertEqual(got.returncode, 0)

    def test_it_needs_no_config_and_no_token(self):
        """Reading your own records must never need a credential - and the
        moment you most need this answer is when the platform is refusing you."""
        self.write(age=3)
        got = self.check()
        self.assertEqual(got.returncode, 0)
        self.assertNotIn("config", got.stderr.lower())

    def test_an_absent_relay_is_distinguished_from_a_dead_one(self):
        got = self.check()
        self.assertEqual(got.returncode, 3)
        self.assertIn("investigate", got.stdout.lower())


class TheAdviceNamesOnlyThingsThatExist(unittest.TestCase):
    """Kimi's block. The restart verdict told an agent to run `alb --stop`,
    which does not exist - so obeying the instruction failed at the exact
    moment of obedience. The same class as a comment describing a safety the
    code does not have: guidance that reads as authoritative and cannot be
    followed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        (self.root / "state" / "health.json").write_text(
            json.dumps({"heartbeat": time.time() - 900, "state": "running"}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_alb_command_it_recommends_is_a_real_one(self):
        import re
        out = subprocess.run([ALB, "--check", "--root", str(self.root)],
                             capture_output=True, text=True).stdout
        helptext = subprocess.run([ALB, "--help"], capture_output=True,
                                  text=True).stdout
        # Only flags advertised as OURS: a recommendation to run a cmux
        # command names cmux's flags, and alb's help says nothing about those.
        for flag in set(re.findall(r"alb [^.\n]*?(--[a-z][a-z-]+)", out)):
            with self.subTest(flag=flag):
                self.assertIn(flag, helptext,
                              f"--check recommends alb {flag}, which does not exist")
