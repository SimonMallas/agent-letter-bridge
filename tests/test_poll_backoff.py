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
