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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from alb.bridge import run  # noqa: E402
from fake_platform import FakePlatform  # noqa: E402
from alb.poller import loop  # noqa: E402


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

    def test_it_runs_without_a_surface(self):
        """The docs promise mail lands with no multiplexer and nothing pings.
        The code required a surface, so it would not start at all - a direct
        contradiction, and the first thing someone installing on a machine
        without cmux would hit."""
        self._write("ALB_TOKEN=1:x\n")
        config = run.load_config(self.path)
        self.assertEqual(config.get("ALB_SURFACE", ""), "")

    def test_a_missing_token_still_refuses_and_names_it(self):
        self._write("ALB_SURFACE=S\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("ALB_TOKEN", str(caught.exception))

    def test_a_missing_required_key_refuses_and_names_it(self):
        self._write("ALB_SURFACE=S\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("ALB_TOKEN", str(caught.exception))

    def test_the_token_is_never_included_in_a_config_error(self):
        # A config that HAS a token but is otherwise incomplete: the error must
        # name what is missing and never echo what is present.
        self._write("ALB_TOKEN=1:SECRETVALUE\nALB_TOKEN=\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertNotIn("SECRETVALUE", str(caught.exception))

    def test_a_complete_config_loads(self):
        self._write("ALB_TOKEN=1:x\nALB_SURFACE=SURFACE-1\n")
        config = run.load_config(self.path)
        self.assertEqual(config["ALB_SURFACE"], "SURFACE-1")


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

    def test_with_no_surface_the_letter_lands_and_nothing_rings(self):
        """Degraded mode is a supported way to run, not a broken install: no
        multiplexer, mail on disk, no bell. The operator finds it by looking."""
        transport = FakeTransport()
        run.run_once(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]),
            transport, "", self.root)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
        self.assertEqual(transport.rung, [])

        # And it is RECORDED. A missing bell that leaves no trace is
        # indistinguishable from a broken one, which is the confusion the
        # whole ring-health file exists to prevent.
        record = json.loads((self.root / "state" / "ring-health.json").read_text())
        self.assertEqual(record["state"], "disabled")
        self.assertIn("ALB_SURFACE", record["reason"])

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
            self._cycle(FakePlatform(raise_conflict=True), transport)
        self.assertEqual(transport.rung, [])

    def test_a_cycle_confirms_consumption_after_the_letters_are_durable(self):
        """Acking internally is not consuming. A cycle that ends without
        telling the platform has consumed nothing and will re-read everything
        on the next poll."""
        platform = FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}])
        self._cycle(platform, FakeTransport())
        self.assertEqual(platform.pending(), [],
                         "the cycle ended without telling the platform anything")

    def test_acking_without_confirming_consumes_nothing(self):
        """THE LIVE BUG, reproduced against the fake.

        This is the defect a real bot found after 98 green tests: a cycle that
        records a high-water mark and exits has told the platform NOTHING, so
        every update returns on the next poll. Persistence does not save this -
        it saves a restart. Only transmitting does.

        The fake can only show this because ack() records and confirm()
        transmits, as the platform does. A double that consumed on ack() would
        pass whether or not confirm was ever called.
        """
        platform = FakePlatform([{"update_id": 1, "chat_id": "111", "text": "hello"}])
        loop.poll_once(platform, self.root / "inbox", self.root / "delivered.json",
                       self.root / "allowlist.json")
        self.assertEqual(platform.staged, 1, "nothing was recorded")
        self.assertEqual(len(platform.pending()), 1,
                         "the fake consumed on ack, so it cannot show this bug")

    def test_a_conflict_confirms_nothing(self):
        platform = FakePlatform(raise_conflict=True)
        with self.assertRaises(loop.PlatformConflict):
            self._cycle(platform, FakeTransport())
        self.assertIsNone(platform.confirmed, "confirmed while yielding")

    def test_a_ring_failure_is_recorded_where_a_human_can_see_it(self):
        """The swallow that protects letters also hides ring death.

        A dead ring - missing binary, stale surface, changed permissions -
        would otherwise fail silently forever: mail lands, nothing pings. That
        is the no-bell state, which is a failure and not a tier. Letters stay
        authoritative; the failure just stops being invisible.
        """
        class BrokenTransport:
            def deliver(self, surface, line):
                raise RuntimeError("relay is dead")

        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]), BrokenTransport())
        record = json.loads((self.root / "state" / "ring-health.json").read_text())
        self.assertEqual(record["state"], "failing")
        self.assertIn("relay is dead", record["reason"])

    def test_a_working_ring_records_success(self):
        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]), FakeTransport())
        record = json.loads((self.root / "state" / "ring-health.json").read_text())
        self.assertEqual(record["state"], "ok")

    def test_a_ring_failure_does_not_lose_the_letter(self):
        """Letters are authoritative; rings only accelerate. A dead notifier
        must not cost a message - the mail is already on disk."""
        class BrokenTransport:
            def deliver(self, surface, line):
                raise RuntimeError("relay is dead")

        self._cycle(FakePlatform([
            {"update_id": 1, "chat_id": "111", "text": "hello"}]), BrokenTransport())
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
