"""The loop must OBSERVE the wait it captured, not merely carry it.

A retry_after attached to an exception and never read is a floor that exists
only in the tests. This file drives the real polling loop and watches what it
sleeps for - the assertion Codex's block asked for, because the adapter test
proves classification and says nothing about consumption.
"""
import argparse
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb import cli  # noqa: E402
from alb.adapters.telegram import api  # noqa: E402


class Slept(Exception):
    """Escapes the infinite loop once the sleep we came to inspect happens."""


class TheLoopHonoursThePlatformsFloor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        (self.root / "inbox").mkdir()
        (self.root / "processed").mkdir()
        (self.root / "outbox").mkdir()
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        self.args = argparse.Namespace(interval=2, once=False, mail_root=None)

    def tearDown(self):
        self.tmp.cleanup()

    def _slept_for(self, retry_after):
        """Run one loop turn against a throttling platform; return the sleep."""
        platform = mock.Mock()
        platform.fetch.side_effect = api.TransientFailure(
            "getUpdates deferred: HTTP 429", retry_after)
        seen = []

        def record(seconds):
            seen.append(seconds)
            raise Slept

        with mock.patch.object(cli.time, "sleep", side_effect=record):
            with self.assertRaises(Slept):
                cli._poll_forever(platform, mock.Mock(), "surface:1",
                                  self.root, self.args, {"ALB_TO": "agent"})
        return seen[0]

    def test_a_stated_wait_longer_than_our_backoff_is_obeyed(self):
        """interval 2 gives a 10s backoff. A platform asking for 47 must not
        be answered in 10 - that just earns the next 429."""
        self.assertGreaterEqual(self._slept_for(47), 47)

    def test_a_wait_beyond_our_cap_is_still_obeyed(self):
        """The 30s cap was ours, invented for network blips. It must not
        silently override a number the platform actually stated."""
        self.assertGreaterEqual(self._slept_for(120), 120)

    def test_our_own_backoff_still_applies_when_none_is_stated(self):
        """The healthy control: without it, "honours the floor" and "sleeps
        whatever it is told, including nothing" are the same test."""
        self.assertEqual(self._slept_for(None), 10)


class TheLoopSaysItIsStillThereWhileItWaits(unittest.TestCase):
    """Grok's finding, from the other end: a bridge that correctly waits out
    a rate limit completes no cycles, so under the old rule its heartbeat
    froze for exactly as long as it behaved correctly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for sub in ("state", "inbox", "processed", "outbox"):
            (self.root / sub).mkdir()
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        self.args = argparse.Namespace(interval=2, once=False, mail_root=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_throttled_wait_writes_degraded_not_silence(self):
        platform = mock.Mock()
        platform.fetch.side_effect = api.TransientFailure(
            "getUpdates deferred: HTTP 429", 1)

        def stop(_):
            raise Slept

        with mock.patch.object(cli.time, "sleep", side_effect=stop):
            with self.assertRaises(Slept):
                cli._poll_forever(platform, mock.Mock(), "surface:1",
                                  self.root, self.args, {"ALB_TO": "agent"})
        health = json.loads(
            (self.root / "state" / "health.json").read_text(encoding="utf-8"))
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["reason"], "throttled_429")


class AStartingBridgeSaysSoBeforeItBlocks(unittest.TestCase):
    """The gate refused this as a pin until the test existed: I had checked
    that _write_heartbeat CAN say "starting" and never that the loop says it.
    The same error as testing that retry_after is stored and not that anything
    reads it - a unit proved, a behaviour assumed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for sub in ("state", "inbox", "processed", "outbox"):
            (self.root / sub).mkdir()
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        self.args = argparse.Namespace(interval=2, once=False, mail_root=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_heartbeat_is_written_before_the_first_fetch(self):
        seen = {}
        platform = mock.Mock()

        def fetch(*a, **kw):
            # The moment before any poll could have completed.
            path = self.root / "state" / "health.json"
            seen["at_first_fetch"] = (
                json.loads(path.read_text(encoding="utf-8")) if path.exists()
                else None)
            raise Slept

        platform.fetch.side_effect = fetch
        with self.assertRaises(Slept):
            cli._poll_forever(platform, mock.Mock(), "surface:1",
                              self.root, self.args, {"ALB_TO": "agent"})
        self.assertIsNotNone(seen["at_first_fetch"],
                             "a restarted bridge must not present the previous "
                             "process's timestamp while it blocks")
        self.assertEqual(seen["at_first_fetch"]["state"], "starting")


class AYieldIsRecordedBeforeLeaving(unittest.TestCase):
    """Codex F4. The bridge yields a contested token and exits 0 - correctly.
    But it used to leave without saying so, the last health record went stale,
    and a wake-check five minutes later called a deliberate stand-down a
    death. The supervisor would then restart it to fight for the token it had
    just refused to fight for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for sub in ("state", "inbox", "processed", "outbox"):
            (self.root / sub).mkdir()
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        self.args = argparse.Namespace(interval=2, once=False, mail_root=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_health_file_says_yielded_after_a_conflict(self):
        from alb.poller import loop as poller
        platform = mock.Mock()
        platform.fetch.side_effect = poller.PlatformConflict("another consumer")
        rc = cli._poll_forever(platform, mock.Mock(), "surface:1",
                               self.root, self.args, {"ALB_TO": "agent"})
        self.assertEqual(rc, 0, "yielding is success, not failure")
        health = json.loads(
            (self.root / "state" / "health.json").read_text(encoding="utf-8"))
        self.assertEqual(health["state"], "yielded")


class TheBridgeStandsDownWhenAsked(unittest.TestCase):
    """The other half of --stop. A request nothing reads is a command that
    lies, and this one would lie at the moment an operator most needs it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for sub in ("state", "inbox", "processed", "outbox"):
            (self.root / sub).mkdir()
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        self.args = argparse.Namespace(interval=2, once=False, mail_root=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_requested_stop_ends_the_loop_cleanly(self):
        from alb.bridge import singleton
        gen = "abc123"
        (self.root / "state" / "stop-requested").write_text(
            json.dumps({"generation": gen}), encoding="utf-8")
        platform = mock.Mock()
        platform.fetch.side_effect = AssertionError(
            "must stand down BEFORE reaching for the platform")
        rc = cli._poll_forever(platform, mock.Mock(), "surface:1",
                               self.root, self.args, {"ALB_TO": "agent"},
                               generation=gen)
        self.assertEqual(rc, 0, "a requested stop is success, not failure")
        health = json.loads(
            (self.root / "state" / "health.json").read_text(encoding="utf-8"))
        self.assertEqual(health["state"], "yielded")
        self.assertEqual(health["reason"], "requested",
                         "an operator must be able to tell a requested stop "
                         "from a token conflict")

    def test_the_request_is_cleared_so_the_next_start_is_not_stopped(self):
        from alb.bridge import singleton
        gen = "abc123"
        (self.root / "state" / "stop-requested").write_text(
            json.dumps({"generation": gen}), encoding="utf-8")
        cli._poll_forever(mock.Mock(), mock.Mock(), "surface:1",
                          self.root, self.args, {"ALB_TO": "agent"},
                          generation=gen)
        self.assertFalse(singleton.stop_requested(self.root, gen),
                         "an honoured request must not stop the next bridge too")
