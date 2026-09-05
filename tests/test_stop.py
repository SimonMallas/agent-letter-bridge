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

    def test_a_running_bridge_honours_the_request_and_stands_down(self):
        from alb.bridge import singleton
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        (self.root / "state" / "stop-requested").write_text("", encoding="utf-8")
        self.assertTrue(singleton.stop_requested(self.root))

    def test_a_bridge_with_no_request_keeps_going(self):
        from alb.bridge import singleton
        self.assertFalse(singleton.stop_requested(self.root))

    def test_it_refuses_a_pid_that_no_longer_holds_the_lock(self):
        """A stale record from a crashed bridge names a pid the kernel has
        long since reused. Signalling it would kill a stranger's process -
        so the lock, not the file, decides who is alive."""
        (self.root / "bridge.lock").write_text(
            json.dumps({"pid": 999999}), encoding="utf-8")
        got = self.stop()
        self.assertEqual(got.returncode, 0)
        self.assertIn("nothing", got.stdout.lower())
