"""Bounded outbound. Replies only; never originates.

The bridge holds the token, so it is a privilege boundary. Every route out of
here is a reply to a letter that already exists on disk.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alb.letter import store  # noqa: E402
from alb.send import reply  # noqa: E402


class FakeSender:
    def __init__(self, outcome="sent"):
        self.outcome = outcome
        self.calls = []

    def send(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.outcome == "ambiguous":
            raise reply.AmbiguousOutcome("connection reset after POST")
        if self.outcome == "refused":
            raise reply.DefiniteRefusal("rate limited")
        return "ok"


class BoundedReply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["8675309"]}), encoding="utf-8")
        self.letter_id = store.publish(self.inbox, "incoming", {"chat_id": "8675309"})

    def tearDown(self):
        self.tmp.cleanup()

    def _send(self, sender, letter_id=None, text="a reply"):
        return reply.send_reply(
            sender, self.inbox, self.state, self.allow,
            letter_id or self.letter_id, text,
        )

    def test_a_reply_works_on_a_letter_THE_POLLER_ACTUALLY_WROTE(self):
        """The fixtures in this file build letters by hand in a shape the
        poller stopped producing when the routing envelope landed. The poller
        writes telegram_chat_id; the send path read chat_id; so every reply to
        a real letter failed the allowlist with a destination of None.

        Same class as a test double that drifts from production: the letters
        under test must be the letters the system writes.
        """
        from alb.poller import loop

        class OnePlatform:
            def __init__(self):
                self.staged = None

            def fetch(self, offset=None):
                return [{"update_id": 1, "chat_id": "8675309", "text": "hi"}]

            def ack(self, uid):
                self.staged = uid

        loop.poll_once(OnePlatform(), self.inbox, self.root / "delivered.json",
                       self.allow)
        # Pick the POLLER's letter explicitly. setUp also publishes one, and
        # sorting picked between them by a random suffix - so this test passed
        # by resolving the hand-built fixture instead of the real letter,
        # which is the very drift it exists to catch.
        letter_id = [p.stem for p in self.inbox.glob("*-u*.md")][0]

        sender = FakeSender()
        reply.send_reply(sender, self.inbox, self.state, self.allow,
                         letter_id, "a reply")
        self.assertEqual(sender.calls[0][0], "8675309")

    def test_the_destination_comes_from_the_stored_letter(self):
        """Never remembered, never configured, never inferred."""
        sender = FakeSender()
        self._send(sender)
        self.assertEqual(sender.calls[0][0], "8675309")

    def test_a_reply_still_works_after_the_letter_is_swept(self):
        """The poller searches inbox AND processed; the send path searched only
        the inbox. So after a sweep a reply raised NoSuchLetter and the
        operator concluded sending was broken - when the letter was simply
        filed. The send side must search the same set as the receive side."""
        processed = self.root / "processed"
        processed.mkdir(exist_ok=True)
        for f in self.inbox.glob("*.md"):
            f.rename(processed / f.name)
        sender = FakeSender()
        reply.send_reply(sender, self.inbox, self.state, self.allow,
                         self.letter_id, "a reply", searched=[self.inbox, processed])
        self.assertEqual(sender.calls[0][0], "8675309")

    def test_a_reply_to_an_unknown_letter_refuses(self):
        sender = FakeSender()
        with self.assertRaises(store.NoSuchLetter):
            self._send(sender, letter_id="20260101T000000-deadbeef")
        self.assertEqual(sender.calls, [], "sent without a stored letter")

    def test_a_path_shaped_letter_id_refuses(self):
        sender = FakeSender()
        with self.assertRaises(store.UnsafeIdentifier):
            self._send(sender, letter_id="../escape")
        self.assertEqual(sender.calls, [])

    def test_the_allowlist_is_rechecked_at_send(self):
        """Enforced at BOTH ends. A chat removed since the letter arrived must
        not still receive a reply."""
        self.allow.write_text(json.dumps({"chats": []}), encoding="utf-8")
        sender = FakeSender()
        with self.assertRaises(reply.NotPermitted):
            self._send(sender)
        self.assertEqual(sender.calls, [])

    def test_the_claim_directory_is_fsynced_so_the_claim_survives(self):
        """Same class as the letter's directory fsync, and the consequence is
        worse. The claim is what stops a replay double-posting. If its NAME is
        not durable, a crash can lose the claim while the send it recorded has
        already happened - and the retry double-posts, which is exactly what
        claim-before-send exists to prevent."""
        import os as _os
        synced_dirs = []
        real_fsync = _os.fsync

        def spy(fd):
            try:
                if _os.fstat(fd).st_mode & 0o040000:
                    synced_dirs.append(fd)
            except OSError:
                pass
            return real_fsync(fd)

        with mock.patch.object(reply.os, "fsync", side_effect=spy):
            self._send(FakeSender())
        self.assertTrue(synced_dirs, "the claim's directory entry was never made durable")

    def test_the_same_reply_cannot_be_sent_twice(self):
        """Claim before send. A replay or restart refuses rather than
        double-posting; the platform send has no idempotency key."""
        self._send(FakeSender())
        second = FakeSender()
        with self.assertRaises(reply.AlreadyClaimed):
            self._send(second)
        self.assertEqual(second.calls, [], "double-posted on replay")

    def test_an_ambiguous_outcome_dead_letters_and_never_retries(self):
        sender = FakeSender(outcome="ambiguous")
        with self.assertRaises(reply.AmbiguousOutcome):
            self._send(sender)
        dead = list((self.state / "dead-letters").glob("*.json"))
        self.assertEqual(len(dead), 1)
        record = json.loads(dead[0].read_text(encoding="utf-8"))
        self.assertEqual(record["outcome"], "ambiguous")
        self.assertEqual(len(sender.calls), 1, "auto-retried an ambiguous send")

    def test_an_unclassified_exception_dead_letters(self):
        """THE SAFETY NET. If an outcome escaped classification then it is
        unknown by definition, and unknown dead-letters for a human.

        Without this the claim is burned in_flight forever with no record: the
        reply can never be retried (AlreadyClaimed) and nobody is told."""
        class BrokenSender:
            def __init__(self):
                self.calls = []

            def send(self, chat_id, text):
                self.calls.append((chat_id, text))
                raise RuntimeError("a bug in a future adapter")

        sender = BrokenSender()
        with self.assertRaises(reply.AmbiguousOutcome):
            self._send(sender)
        dead = list((self.state / "dead-letters").glob("*.json"))
        self.assertEqual(len(dead), 1, "unclassified failure left no human record")
        claim = list((self.state / "reply-attempts").glob("*.json"))[0]
        self.assertEqual(json.loads(claim.read_text())["outcome"], "ambiguous",
                         "claim left stuck in_flight")

    def test_a_definite_refusal_is_not_recorded_as_ambiguous(self):
        """A rate limit is a definite refusal - the send did not happen. Only
        genuine uncertainty dead-letters."""
        sender = FakeSender(outcome="refused")
        with self.assertRaises(reply.DefiniteRefusal):
            self._send(sender)
        dead = list((self.state / "dead-letters").glob("*.json"))
        self.assertEqual(dead, [], "dead-lettered a definite refusal")


if __name__ == "__main__":
    unittest.main()
