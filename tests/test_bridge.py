"""Wiring: config, and one full cycle of poll -> letter -> ring."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from bridge import run  # noqa: E402
from poller import loop  # noqa: E402


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "bridge.env"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text, mode=0o600):
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, mode)

    def test_a_missing_env_file_refuses_before_anything_starts(self):
        with self.assertRaises(run.ConfigError):
            run.load_config(self.path)

    def test_a_world_readable_env_file_refuses(self):
        """The file holds a bot token. Refusing loudly beats starting with a
        credential any local process can read."""
        self._write("ALB_TOKEN=1:x\nALB_SURFACE=S\n", mode=0o644)
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("permissions", str(caught.exception).lower())

    def test_a_missing_required_key_refuses_and_names_it(self):
        self._write("ALB_TOKEN=1:x\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("ALB_SURFACE", str(caught.exception))

    def test_the_token_is_never_included_in_a_config_error(self):
        self._write("ALB_TOKEN=1:SECRETVALUE\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertNotIn("SECRETVALUE", str(caught.exception))

    def test_a_complete_config_loads(self):
        self._write("ALB_TOKEN=1:x\nALB_SURFACE=SURFACE-1\n")
        config = run.load_config(self.path)
        self.assertEqual(config["ALB_SURFACE"], "SURFACE-1")


class FakePlatform:
    def __init__(self, updates=None, conflict=False):
        self.updates = list(updates or [])
        self.conflict = conflict
        self.acked = None

    def fetch(self, offset=None):
        if self.conflict:
            raise loop.PlatformConflict("another consumer holds this token")
        return [u for u in self.updates
                if self.acked is None or u["update_id"] > self.acked]

    def ack(self, update_id):
        if self.acked is None or update_id > self.acked:
            self.acked = update_id


class FakeTransport:
    def __init__(self):
        self.rung = []

    def deliver(self, surface, line):
        self.rung.append((surface, line))


class OneCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for name in ("inbox", "processed", "state"):
            (self.root / name).mkdir()
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cycle(self, platform, transport):
        return run.run_once(platform, transport, "SURFACE-1", self.root)

    def test_a_permitted_message_becomes_a_letter_and_one_ring(self):
        transport = FakeTransport()
        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]), transport)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
        self.assertEqual(len(transport.rung), 1)

    def test_a_batch_rings_once_not_once_per_letter(self):
        """Coalescing. N letters, one ring: the recipient sweeps the inbox, so
        a ring per letter is noise that can outrun the reader."""
        transport = FakeTransport()
        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "one"},
            {"update_id": 2, "chat_id": "111", "text": "two"},
            {"update_id": 3, "chat_id": "111", "text": "three"}]), transport)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 3)
        self.assertEqual(len(transport.rung), 1)

    def test_nothing_new_means_no_ring(self):
        transport = FakeTransport()
        self._cycle(FakePlatform([]), transport)
        self.assertEqual(transport.rung, [])

    def test_a_denied_sender_produces_no_ring(self):
        transport = FakeTransport()
        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "999", "text": "let me in"}]), transport)
        self.assertEqual(transport.rung, [], "rang for a message that was denied")

    def test_a_conflict_stops_the_cycle_without_ringing(self):
        transport = FakeTransport()
        with self.assertRaises(loop.PlatformConflict):
            self._cycle(FakePlatform(conflict=True), transport)
        self.assertEqual(transport.rung, [])

    def test_a_ring_failure_does_not_lose_the_letter(self):
        """Letters are authoritative; rings only accelerate. A dead notifier
        must not cost a message - the mail is already on disk."""
        class BrokenTransport:
            def deliver(self, surface, line):
                raise RuntimeError("relay is dead")

        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]), BrokenTransport())
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
