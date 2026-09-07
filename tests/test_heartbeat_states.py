"""A correct pause must not read as a death.

Three findings, one shape. The heartbeat was written only after a COMPLETED
cycle, which is an honest claim about work done and a misleading one about
being alive:

  - a freshly restarted bridge holds the previous process's timestamp until
    its first long poll returns, so a healthy process reads stale for as long
    as the poll blocks - and a wake-check in that window restarts something
    that is working;
  - a bridge correctly backing off from a 429 completes no cycles at all, so
    it looks progressively deader the longer it correctly waits.

So the file answers two different questions now: am I alive, and what was I
last doing. Conflating them is what made a correct pause indistinguishable
from a corpse.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.poller import loop  # noqa: E402


class TheHeartbeatSaysWhatKindOfAlive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "health.json"

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_a_completed_cycle_still_reads_as_running(self):
        loop._write_heartbeat(self.path)
        self.assertEqual(self.read()["state"], "running")

    def test_a_starting_bridge_says_so_before_its_first_poll(self):
        """Otherwise the first thing a restarted bridge does is look dead."""
        loop._write_heartbeat(self.path, state="starting")
        got = self.read()
        self.assertEqual(got["state"], "starting")
        self.assertIn("heartbeat", got)

    def test_a_backing_off_bridge_is_degraded_not_absent(self):
        loop._write_heartbeat(self.path, state="degraded", reason="throttled_429")
        got = self.read()
        self.assertEqual(got["state"], "degraded")
        self.assertEqual(got["reason"], "throttled_429")

    def test_the_timestamp_moves_even_when_no_work_completes(self):
        loop._write_heartbeat(self.path, state="degraded", reason="throttled_429")
        first = self.read()["heartbeat"]
        loop._write_heartbeat(self.path, state="degraded", reason="throttled_429")
        self.assertGreaterEqual(self.read()["heartbeat"], first)

    def test_a_reason_is_bounded_and_never_carries_detail(self):
        """The reason is a code an operator can act on, not a message that
        might carry a chat id, a body, or a token into a health file."""
        loop._write_heartbeat(self.path, state="degraded",
                              reason="throttled_429 chat=424242424 body=hello")
        self.assertNotIn("424242424", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("hello", self.path.read_text(encoding="utf-8"))
