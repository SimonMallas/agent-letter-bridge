"""`alb --stop`: the product doing the killing, instead of an agent guessing.

Grok had to work out how to interrupt his own bridge and got it wrong twice -
once on a key name, once by signalling a pid he found himself. Both times the
knowledge lived in his head rather than in the tool, which is how four seats
ended up with four arrangements.

The identity problem solves itself: flock is released by the kernel when the
holder dies, so if the lock CANNOT be taken, whoever recorded their pid in it
is provably still alive. No pid-reuse window, no start-time comparison, no
subprocess to ask the OS who is who.
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ALB = str(ROOT / ".venv" / "bin" / "alb")
from alb.bridge import singleton  # noqa: E402


class StoppingTheBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def stop(self, timeout=20):
        return subprocess.run([ALB, "--stop", "--root", str(self.root)],
                              capture_output=True, text=True, timeout=timeout)

    def test_the_holder_records_a_pid_so_it_can_be_found(self):
        with singleton.hold(self.root):
            data = json.loads(
                (self.root / "bridge.lock").read_text(encoding="utf-8"))
            self.assertEqual(data["pid"], os.getpid())

    def test_stopping_nothing_says_so_rather_than_pretending(self):
        got = self.stop()
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("nothing", got.stdout.lower())

    def test_a_stop_request_is_left_for_the_bridge_to_honour(self):
        """Nothing is signalled. Pi's block: a failed flock proves someone
        held the lock at that INSTANT, not at the instant of the kill - the
        holder can exit, release, and have its number reused in between, and
        we would then signal a stranger with the confidence of having proved
        they were ours. Worse than guessing, because it looks verified.

        So the only process that ever acts on a stop is the one that owns the
        work. A stale request cannot reach a stranger, because a stranger is
        not running our loop."""
        script = (
            "import sys, time;"
            "sys.path.insert(0, %r);" % str(ROOT / "src") +
            "from alb.bridge import singleton;"
            "ctx = singleton.hold(%r);" % str(self.root) +
            "ctx.__enter__();"
            "time.sleep(300)")
        child = subprocess.Popen([sys.executable, "-c", script])
        for _ in range(100):
            if (self.root / "bridge.lock").exists() and (
                    self.root / "bridge.lock").read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        try:
            got = self.stop(timeout=45)
            # The holder in this test never checks for the request, so the
            # command must report that honestly rather than claim success.
            self.assertNotEqual(got.returncode, 0)
            self.assertTrue((self.root / "state" / "stop-requested").exists(),
                            "the request must outlive the command that made it")
            self.assertIn("still running", got.stdout.lower() + got.stderr.lower())
        finally:
            child.kill()
            child.wait(timeout=5)  # reap it; an unwaited child warns at exit

    def test_a_request_names_the_run_it_was_meant_for(self):
        from alb.bridge import singleton
        with singleton.hold(self.root) as generation:
            asked = singleton.request_stop(self.root)
            self.assertEqual(asked, generation)
            self.assertTrue(singleton.stop_requested(self.root, generation))

    def test_a_request_for_an_earlier_run_never_stops_a_later_one(self):
        """Pi's race. Holder A is asked to stop and dies before reading it;
        B starts on the same root. Without a generation, B stands down for a
        request that was never about it - the stop becoming an outage."""
        from alb.bridge import singleton
        with singleton.hold(self.root) as first:
            singleton.request_stop(self.root)
        with singleton.hold(self.root) as second:
            self.assertNotEqual(first, second)
            self.assertFalse(singleton.stop_requested(self.root, second),
                             "a stale request must not stop the next bridge")

    def test_a_bridge_with_no_request_keeps_going(self):
        from alb.bridge import singleton
        with singleton.hold(self.root) as generation:
            self.assertFalse(singleton.stop_requested(self.root, generation))

    def test_nothing_running_leaves_no_trap_for_the_next_bridge(self):
        from alb.bridge import singleton
        self.assertIsNone(singleton.request_stop(self.root))
        self.assertFalse((self.root / "state" / "stop-requested").exists())

    def test_a_malformed_request_is_not_a_stop(self):
        """Fails closed: an unreadable request is not a reason to stop
        handling mail, and 'I could not read it' must never mean 'stop'."""
        from alb.bridge import singleton
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        for raw in ("{not json", "", "[]", '{"generation": null}'):
            with self.subTest(raw=raw[:12]):
                (self.root / "state" / "stop-requested").write_text(
                    raw, encoding="utf-8")
                with singleton.hold(self.root) as generation:
                    self.assertFalse(
                        singleton.stop_requested(self.root, generation))

    def test_it_refuses_a_pid_that_no_longer_holds_the_lock(self):
        """A stale record from a crashed bridge names a pid the kernel has
        long since reused. Signalling it would kill a stranger's process -
        so the lock, not the file, decides who is alive."""
        (self.root / "bridge.lock").write_text(
            json.dumps({"pid": 999999}), encoding="utf-8")
        got = self.stop()
        self.assertEqual(got.returncode, 0)
        self.assertIn("nothing", got.stdout.lower())


class TheStopRemembersWhatItAsked(unittest.TestCase):
    """Codex's seam. --stop knew the generation it had just asked to stop and
    then went back to disk for it. If the holder exits in that gap the re-read
    returns nothing, the unhonoured request is neither recognised nor cleared,
    and the command reports a clean stop it never confirmed.

    The value was in hand. Reading it again was the bug."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_unhonoured_request_is_cleared_even_if_the_holder_vanishes(self):
        from alb.bridge import singleton
        script = (
            "import sys, time;"
            "sys.path.insert(0, %r);" % str(ROOT / "src") +
            "from alb.bridge import singleton;"
            "ctx = singleton.hold(%r);" % str(self.root) +
            "ctx.__enter__();"
            "time.sleep(300)")
        child = subprocess.Popen([sys.executable, "-c", script])
        for _ in range(100):
            lock = self.root / "bridge.lock"
            if lock.exists() and lock.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        asked = singleton.request_stop(self.root)
        self.assertIsNotNone(asked)
        child.kill(); child.wait(timeout=5)  # the holder dies without reading it
        got = subprocess.run([ALB, "--stop", "--root", str(self.root)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertFalse((self.root / "state" / "stop-requested").exists(),
                         "an unhonoured request must not be left behind")


class TheSeamItselfIsPinned(unittest.TestCase):
    """Codex F1-test: my previous regression never reached the seam it
    claimed to pin. It killed the holder BEFORE invoking --stop, so the
    command took the nothing-is-running branch - where the mutant behaves
    identically. A pin whose named test cannot distinguish the mutant is a
    green light wired to nothing, which is worse than an absent test because
    it retires the question.

    This one drives the seam deterministically: request_stop returns a
    generation, and every holder check after it says absent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_generation_is_carried_not_reread(self):
        from unittest import mock
        from alb import cli
        from alb.bridge import singleton

        (self.root / "state" / singleton.STOP_REQUEST).write_text(
            json.dumps({"generation": "gen-A"}), encoding="utf-8")
        seen = {"reread": 0, "checked_with": [], "cleared": 0}

        def request_stop(root):
            return "gen-A"

        def current_generation(root):
            seen["reread"] += 1
            return None          # the holder vanished in the gap

        def running_pid(root):
            return None          # lock free: the wait ends immediately

        def stop_requested(root, generation):
            seen["checked_with"].append(generation)
            return generation == "gen-A"

        def clear_stop_request(root):
            seen["cleared"] += 1

        with mock.patch.multiple(
                cli.singleton, request_stop=request_stop,
                current_generation=current_generation, running_pid=running_pid,
                stop_requested=stop_requested,
                clear_stop_request=clear_stop_request):
            rc = cli.main(["--stop", "--root", str(self.root)])

        self.assertEqual(rc, 0)
        self.assertEqual(seen["reread"], 0,
                         "the generation was in hand; re-reading it is the bug")
        self.assertIn("gen-A", seen["checked_with"],
                      "consumption must be checked against what we ASKED")
        self.assertEqual(seen["cleared"], 1,
                         "an unhonoured request must be cleared")


class HonouredIsProvedNotInferred(unittest.TestCase):
    """Pi's counterexample. --stop watched the request DISAPPEAR and called
    that honoured - but absence has two causes: the bridge consumed it, or
    something else cleared it after the bridge died for its own reasons. An
    inference from absence cannot tell them apart.

    The bridge already records a requested stand-down in its health file. That
    is positive evidence, and it is what the claim should rest on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run_stop(self, health, age=0):
        """The bridge writes its health AFTER we ask - so the record lands
        while --stop waits, not before it starts. Modelling that order is the
        point: a record from before our request proves nothing about it."""
        from unittest import mock
        from alb import cli
        calls = {"n": 0}

        def running_pid(root):
            calls["n"] += 1
            if calls["n"] == 1:
                return 4242                      # alive when we ask
            if health is not None:
                payload = dict(health)
                payload["heartbeat"] = time.time() - age
                (self.root / "state" / "health.json").write_text(
                    json.dumps(payload), encoding="utf-8")
            return None                          # gone by the first check

        with mock.patch.multiple(
                cli.singleton,
                request_stop=lambda root: "gen-A",
                running_pid=running_pid,
                stop_requested=lambda root, generation: False,
                clear_stop_request=lambda root: None):
            with mock.patch.object(cli.sys, "stdout") as out:
                rc = cli.main(["--stop", "--root", str(self.root)])
        said = " ".join(str(c) for c in out.method_calls)
        return rc, said

    def test_a_recorded_stand_down_is_reported_as_honoured(self):
        rc, said = self._run_stop({"state": "yielded", "reason": "requested",
                                   "generation": "gen-A"})
        self.assertEqual(rc, 0)
        self.assertIn("stopped", said)

    def test_a_bridge_that_died_for_another_reason_is_not_called_honoured(self):
        """It yielded a contested token in the same window. The request
        vanished, but nothing about that was our doing."""
        rc, said = self._run_stop({"state": "yielded", "reason": "conflict"})
        self.assertEqual(rc, 0)
        self.assertNotIn("stopped", said.replace("never read", ""))

    def test_an_old_stand_down_from_a_previous_run_proves_nothing(self):
        rc, said = self._run_stop({"state": "yielded", "reason": "requested",
                                   "generation": "gen-A"}, age=9999)
        self.assertNotIn("stopped", said.replace("never read", ""))


class AStandDownProvesWhichRunStoodDown(unittest.TestCase):
    """Pi: timestamp is not run identity. _recorded_stand_down checked state,
    reason and recency - so a stand-down written by a LATER run satisfied a
    caller asking about an earlier one. Recency is a proxy for identity and
    the generation is the identity itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _stop_seeing(self, health):
        from unittest import mock
        from alb import cli
        calls = {"n": 0}

        def running_pid(root):
            calls["n"] += 1
            if calls["n"] == 1:
                return 4242
            payload = dict(health)
            payload["heartbeat"] = time.time()
            (self.root / "state" / "health.json").write_text(
                json.dumps(payload), encoding="utf-8")
            return None

        with mock.patch.multiple(
                cli.singleton, request_stop=lambda root: "gen-A",
                running_pid=running_pid,
                stop_requested=lambda root, generation: False,
                clear_stop_request=lambda root: None):
            with mock.patch.object(cli.sys, "stdout") as out:
                cli.main(["--stop", "--root", str(self.root)])
        return " ".join(str(c) for c in out.method_calls)

    def test_our_own_generation_standing_down_is_honoured(self):
        said = self._stop_seeing(
            {"state": "yielded", "reason": "requested", "generation": "gen-A"})
        self.assertIn("stopped", said)

    def test_another_runs_stand_down_does_not_answer_for_ours(self):
        said = self._stop_seeing(
            {"state": "yielded", "reason": "requested", "generation": "gen-B"})
        self.assertNotIn("stopped", said.replace("never read", ""))

    def test_a_stand_down_naming_no_run_proves_nothing(self):
        said = self._stop_seeing({"state": "yielded", "reason": "requested"})
        self.assertNotIn("stopped", said.replace("never read", ""))


class TheFallbackDoesNotClaimACause(unittest.TestCase):
    """Pi's wording correction. Absence of a receipt cannot establish WHY the
    bridge is gone: it may have ended for its own reasons, or stood down and
    failed to record it. Naming the first is claiming a cause the evidence
    does not support - the same error as reading the request's disappearance
    as proof it was honoured, one level further out."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_says_unconfirmed_rather_than_naming_a_reason(self):
        from unittest import mock
        from alb import cli
        calls = {"n": 0}

        def running_pid(root):
            calls["n"] += 1
            return 4242 if calls["n"] == 1 else None

        with mock.patch.multiple(
                cli.singleton, request_stop=lambda root: "gen-A",
                running_pid=running_pid,
                stop_requested=lambda root, generation: False,
                clear_stop_request=lambda root: None):
            with mock.patch.object(cli.sys, "stdout") as out:
                cli.main(["--stop", "--root", str(self.root)])
        said = " ".join(str(c) for c in out.method_calls).lower()
        self.assertIn("unconfirmed", said)
        self.assertIn("or that the record did not survive", said,
                      "both explanations must be offered, not one asserted")
