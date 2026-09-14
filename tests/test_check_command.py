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
# Invoked as a MODULE, not as an installed console script. The mutation
# harness runs these in an isolated tree that has no .venv, so a test keyed to
# the installed binary fails there before anything is mutated - and the gate
# would then report those pre-existing failures as a mutant's killer. Found by
# the clean-baseline preflight the moment it existed.
ALB = [sys.executable, "-m", "alb"]
ROOT_DIR = ROOT


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
        return subprocess.run([*ALB, "--check", "--root", str(self.root)],
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

    def test_stale_ring_health_is_visible_without_an_inbound(self):
        """Ring outcome lives next to health.json. An agent checking a quiet
        bridge must still see that the last ring failed three days ago."""
        self.write(age=3)
        (self.root / "state" / "ring-health.json").write_text(
            json.dumps({
                "state": "failed",
                "reason": "no_live_surface",
                "at": time.time() - 3 * 86400,
            }), encoding="utf-8")
        got = self.check()
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn("no_live_surface", got.stdout)
        self.assertRegex(got.stdout, r"3d|72h|[2-4]d")
        status = subprocess.run(
            [*ALB, "--status", "--root", str(self.root)],
            capture_output=True, text=True)
        self.assertIn("no_live_surface", status.stdout)
        self.assertRegex(status.stdout, r"3d|72h|[2-4]d")


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
        out = subprocess.run([*ALB, "--check", "--root", str(self.root)],
                             capture_output=True, text=True).stdout
        helptext = subprocess.run([*ALB, "--help"], capture_output=True,
                                  text=True).stdout
        # Only flags advertised as OURS: a recommendation to run a cmux
        # command names cmux's flags, and alb's help says nothing about those.
        for flag in set(re.findall(r"alb [^.\n]*?(--[a-z][a-z-]+)", out)):
            with self.subTest(flag=flag):
                self.assertIn(flag, helptext,
                              f"--check recommends alb {flag}, which does not exist")


class TheDocumentedRitualMatchesTheBinary(unittest.TestCase):
    """docs/agent-setup.md tells an agent what to run and what each exit code
    means. It is the one place where a wrong word costs a relay, because the
    agent following it is alone at the time - so the doc is tested, not
    trusted."""

    def setUp(self):
        self.doc = (ROOT_DIR / "docs" / "agent-setup.md").read_text(
            encoding="utf-8")

    def test_every_alb_flag_the_ritual_names_exists(self):
        import re
        helptext = subprocess.run([*ALB, "--help"], capture_output=True,
                                  text=True).stdout
        for flag in set(re.findall(r"alb (--[a-z][a-z-]+)", self.doc)):
            with self.subTest(flag=flag):
                self.assertIn(flag, helptext,
                              f"agent-setup.md tells an agent to run alb "
                              f"{flag}, which does not exist")

    def test_the_exit_codes_it_documents_are_the_ones_we_return(self):
        for code, meaning in (("0", "nothing"), ("2", "dead"), ("3", "not fix")):
            with self.subTest(code=code):
                self.assertIn(f"exit {code}", self.doc.lower().replace("  ", " ")
                              .replace("exit  ", "exit ") or self.doc)

    def test_the_honest_limit_is_stated_where_an_agent_will_read_it(self):
        """A sleeping agent supervises nothing. If that sentence is missing,
        the page promises self-healing it cannot deliver."""
        self.assertIn("sleeping agent is not a supervisor", self.doc)


class TheTwoRitualsAgree(unittest.TestCase):
    """Codex F2. Two authoritative places tell an agent how to restart: the
    --check verdict and docs/agent-setup.md. They named different mechanisms -
    a cmux key in one, alb --stop in the other - so an agent on tmux following
    the binary would reach for a tool it does not have, while the doc named
    the adapter-independent command we built for exactly that reason.

    The earlier advice test was vacuous for this: it proved every alb flag
    named exists, and passed happily while the verdict named no alb flag at
    all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        (self.root / "state" / "health.json").write_text(
            json.dumps({"heartbeat": time.time() - 900, "state": "running"}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_restart_verdict_names_the_stop_we_built(self):
        out = subprocess.run([*ALB, "--check", "--root", str(self.root)],
                             capture_output=True, text=True).stdout
        self.assertIn("--stop", out,
                      "the verdict must name the adapter-independent stop, "
                      "not a key binding for one multiplexer")
        self.assertNotIn("send-key", out,
                         "naming a cmux key strands every tmux seat")

    def test_the_help_does_not_promise_a_signal(self):
        helptext = subprocess.run([*ALB, "--help"], capture_output=True,
                                  text=True).stdout
        line = [l for l in helptext.splitlines() if "--stop" in l]
        self.assertTrue(line)
        self.assertNotIn("interrupt", " ".join(line).lower(),
                         "it requests; it has never interrupted anything")


class TheRitualUsesTheVerdictsOwnWords(unittest.TestCase):
    """Pi's third follow-up. I changed the verdict from dead to unresponsive
    and left the doc saying dead - the same disagreement Codex found between
    the binary and agent-setup.md, arriving through a terminology change
    rather than a missing command.

    An agent reads the doc to learn what the exit code means. If the doc
    promises certainty the code deliberately withdrew, the withdrawal is
    decorative."""

    def setUp(self):
        self.doc = (ROOT_DIR / "docs" / "agent-setup.md").read_text(
            encoding="utf-8")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        (self.root / "state" / "health.json").write_text(
            json.dumps({"heartbeat": time.time() - 900, "state": "running"}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_doc_says_what_the_binary_says(self):
        out = subprocess.run([*ALB, "--check", "--root", str(self.root)],
                             capture_output=True, text=True).stdout.lower()
        self.assertIn("unresponsive", out)
        self.assertIn("unresponsive", self.doc.lower(),
                      "the doc must teach the word the code actually returns")

    def test_the_doc_does_not_promise_death(self):
        exit2 = [l for l in self.doc.splitlines() if "exit 2" in l]
        self.assertTrue(exit2)
        self.assertNotIn("is dead", " ".join(exit2).lower(),
                         "a policy threshold cannot establish death")
