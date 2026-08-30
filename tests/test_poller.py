"""Poller: untrusted. Fetch, write the letter, THEN ack.

The poller is the only process that touches external input, so it holds the
least privilege: it may not ring, notify, execute, or send.
"""
import ast
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from poller import loop  # noqa: E402


class FakePlatform:
    """Models the REAL contract, in which three things are distinct:

      ack()     RECORDS a high-water mark locally. Consumes nothing.
      confirm() TRANSMITS it. This is what makes the platform forget.
      fetch()   returns everything above what has been CONFIRMED.

    An earlier version consumed on ack(), which made the suite unable to
    reproduce a live defect: a cycle that acked and exited had told the
    platform nothing, and every test still passed. A double that collapses
    recording into consuming cannot test consumption.
    """

    def __init__(self, updates=None, raise_conflict=False):
        self.updates = list(updates or [])
        self.raise_conflict = raise_conflict
        self.staged = None      # ack()ed, not yet transmitted
        self.confirmed = None   # what the platform has actually been told
        self.fetch_count = 0

    def fetch(self, offset=None):
        if self.raise_conflict:
            raise loop.PlatformConflict("another consumer holds this token")
        self.fetch_count += 1
        if self.confirmed is None:
            return list(self.updates)
        return [u for u in self.updates if u["update_id"] > self.confirmed]

    def ack(self, update_id):
        if self.staged is None or update_id > self.staged:
            self.staged = update_id

    def confirm(self):
        self.confirmed = self.staged

    def pending(self):
        return self.fetch(None)


def update(uid, chat, text):
    return {"update_id": uid, "chat_id": chat, "text": text}


class PollerBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.ledger = self.root / "delivered.json"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, platform, health=None):
        return loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                              health_path=health)

    def test_an_allowed_sender_becomes_a_letter(self):
        self._run(FakePlatform([update(1, "111", "hello")]))
        letters = list(self.inbox.glob("*.md"))
        self.assertEqual(len(letters), 1)
        self.assertIn("hello", letters[0].read_text(encoding="utf-8"))

    def test_an_unlisted_sender_produces_silence(self):
        """No letter, no error, no trace. Silence IS the deny path working."""
        self._run(FakePlatform([update(1, "999", "let me in")]))
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    def test_the_offset_is_acked_only_after_the_letter_lands(self):
        platform = FakePlatform([update(7, "111", "hello")])
        self._run(platform)
        self.assertEqual(platform.staged, 7)

    def test_a_failed_write_must_not_ack(self):
        """THE INVARIANT. Acking an update whose letter never landed loses the
        message permanently once the platform's retention window passes."""
        platform = FakePlatform([update(7, "111", "hello")])
        with mock.patch("letter.store.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._run(platform)
        self.assertIsNone(platform.staged, "acked an update that never landed")

    def test_a_non_message_update_is_consumed_without_a_letter(self):
        """No chat means the fail-closed allowlist denies it, which is exactly
        the behaviour wanted: consumed, no letter, no wedge."""
        platform = FakePlatform([{"update_id": 1, "chat_id": "", "text": ""}])
        self._run(platform)
        self.assertEqual(list(self.inbox.glob("*.md")), [])
        self.assertEqual(platform.staged, 1, "non-message update did not advance the mark")

    def test_a_denied_sender_is_still_consumed(self):
        """A deny must advance the offset. The platform queue is a single
        high-water mark: an unacked update stays at the head forever, so a
        stranger's message would wedge the queue and no allowed mail behind it
        would ever arrive. Silence must not mean stuck."""
        platform = FakePlatform([update(1, "999", "let me in")])
        self._run(platform)
        self.assertEqual(platform.staged, 1,
                         "the mark did not advance past a denied update")

    def test_a_denied_sender_does_not_block_allowed_mail_behind_it(self):
        """THE WEDGE. Denied first, allowed second, in one batch."""
        platform = FakePlatform([
            update(1, "999", "let me in"),
            update(2, "111", "real message"),
        ])
        self._run(platform)
        letters = list(self.inbox.glob("*.md"))
        self.assertEqual(len(letters), 1)
        self.assertIn("real message", letters[0].read_text(encoding="utf-8"))
        self.assertEqual(platform.staged, 2, "the mark did not cover the batch")

    def test_a_denied_newest_update_does_not_stall_the_next_poll(self):
        """Allowed first, denied newest - the bad ordering. If the deny is not
        consumed, the next poll returns it again, forever."""
        platform = FakePlatform([
            update(1, "111", "real message"),
            update(2, "999", "let me in"),
        ])
        self._run(platform)
        self.assertEqual(platform.staged, 2, "the mark stopped at the denied update")
        platform.confirm()
        self._run(platform)
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1, "republished on re-poll")

    def test_a_swept_letter_is_not_republished_on_late_redelivery(self):
        """The inbox is swept, so the evidence moves. The poller must tell the
        store everywhere letters travel, or a late redelivery republishes."""
        processed = self.root / "processed"
        processed.mkdir()
        platform = FakePlatform([update(1, "111", "hello")])
        self._run(platform)
        for f in self.inbox.glob("*.md"):
            f.rename(processed / f.name)
        self.ledger.unlink(missing_ok=True)

        # The platform redelivers an update whose letter was already swept.
        replay = FakePlatform([update(1, "111", "hello")])
        loop.poll_once(replay, self.inbox, self.ledger, self.allow,
                       processed=processed)
        self.assertEqual(list(self.inbox.glob("*.md")), [],
                         "republished a letter that was already swept")

    def test_every_poll_writes_a_heartbeat(self):
        """Freshness equals liveness, so a supervisor needs no cooperation from
        the process. The watchdog documents a writer; this is that writer."""
        health = self.root / "health.json"
        self._run(FakePlatform([update(1, "111", "hello")]), health=health)
        self.assertTrue(health.is_file(), "no heartbeat written")
        self.assertIn("heartbeat", json.loads(health.read_text(encoding="utf-8")))

    def test_an_empty_poll_still_writes_a_heartbeat(self):
        """A quiet bridge is not a dead one. If only busy polls wrote the
        heartbeat, silence would read as death."""
        health = self.root / "health.json"
        self._run(FakePlatform([]), health=health)
        self.assertTrue(health.is_file(), "a quiet poll wrote no heartbeat")

    def test_a_conflict_does_not_write_a_heartbeat(self):
        """A yielding poller is not alive for monitoring purposes; claiming
        liveness while exiting would hide the handover."""
        health = self.root / "health.json"
        with self.assertRaises(loop.PlatformConflict):
            self._run(FakePlatform(raise_conflict=True), health=health)
        self.assertFalse(health.is_file(), "claimed liveness while yielding")

    def test_a_platform_conflict_yields_rather_than_erroring(self):
        """A conflict means another consumer holds the token. Yield cleanly so
        the holder keeps running; never fight for it."""
        with self.assertRaises(loop.PlatformConflict):
            self._run(FakePlatform(raise_conflict=True))


class PollerIsStructurallyIncapable(unittest.TestCase):
    """Not 'does not ring' - CANNOT ring. Proved against the source, so a
    future edit that adds the capability fails this test."""

    FORBIDDEN = ("ring", "doorbell", "notify", "send_message", "sendMessage",
                 "subprocess", "popen", "system", "exec")

    def test_the_poller_is_never_handed_a_transport_or_a_credential(self):
        """THE LOAD-BEARING PROOF, stronger than the identifier scan below.

        The AST check proves the absence of named identifiers. This proves
        UNCONSTRUCTIBILITY: poll_once is not given a notifier, a transport, a
        surface or a token, so there is nothing inside it from which a ring or
        a send could be built. Capability is denied by what is passed in, not
        by what the source happens to mention.
        """
        import inspect
        params = set(inspect.signature(loop.poll_once).parameters)
        self.assertEqual(
            params,
            {"platform", "inbox", "ledger", "allowlist_path", "health_path",
             "processed"},
            "the poller's signature changed - if it now receives a transport, "
            "surface or credential, ringing became constructible inside it",
        )

    def test_the_poller_package_contains_no_forbidden_capability(self):
        for path in (ROOT / "src" / "poller").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id.lower())
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr.lower())
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = getattr(node, "module", "") or ""
                    names.add(mod.lower())
                    for alias in node.names:
                        names.add(alias.name.lower())
            for banned in self.FORBIDDEN:
                self.assertNotIn(
                    banned, names,
                    f"{path.name} references '{banned}' - the poller must be "
                    f"incapable of this, not merely abstain from it",
                )


if __name__ == "__main__":
    unittest.main()
