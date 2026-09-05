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
