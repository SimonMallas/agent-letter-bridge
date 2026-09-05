"""The operator's way back from a throttle.

resume_throttled existed but nothing called it: the CLI printed "deferred"
and stopped, and a second --reply-to hit AlreadyClaimed. So at 3am there was
a waiting letter and no command - the structurally-unresumable bug, half
fixed, which is the shape of bug that looks fixed.

The gesture is the one the operator already makes: type the same reply again.
That is explicit and human - never an automatic retry - and it is safe
because resume refuses anything that is not actually deferred.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb import cli  # noqa: E402
from alb.letter import store  # noqa: E402
from alb.send import reply  # noqa: E402


class RetypingTheReplyResumesIt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for sub in ("inbox", "processed", "outbox", "state"):
            (self.root / sub).mkdir()
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["8675309"]}), encoding="utf-8")
        self.letter_id = store.publish(self.root / "inbox", "incoming",
                                       {"chat_id": "8675309"})

    def tearDown(self):
        self.tmp.cleanup()

    def _reply(self, sender, text="a reply"):
        return reply.send_reply(
            sender, self.root / "inbox", self.root / "state",
            self.root / "allowlist.json", self.letter_id, text,
            searched=[self.root / "inbox", self.root / "processed"],
            outbox=self.root / "outbox", agent="codex")

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    class Working:
        def __init__(self):
            self.sent = []

        def send(self, chat_id, text):
            self.sent.append((chat_id, text))
            return "9001"

    def test_the_second_attempt_resumes_instead_of_refusing(self):
        with self.assertRaises(reply.Throttled):
            self._reply(self.Throttling())
        sender = self.Working()
        out_id = cli._reply_or_resume(
            sender, self.root / "inbox", self.root / "state",
            self.root / "allowlist.json", self.letter_id, "a reply",
            searched=[self.root / "inbox", self.root / "processed"],
            outbox=self.root / "outbox", agent="codex")
        self.assertEqual(out_id, f"reply-{self.letter_id}")
        self.assertEqual(len(sender.sent), 1)

    def test_a_delivered_reply_is_still_refused_not_resent(self):
        """The property everything rests on: retyping must never double-send
        a message that already went."""
        sender = self.Working()
        self._reply(sender)
        with self.assertRaises(Exception) as caught:
            cli._reply_or_resume(
                sender, self.root / "inbox", self.root / "state",
                self.root / "allowlist.json", self.letter_id, "a reply",
                searched=[self.root / "inbox", self.root / "processed"],
                outbox=self.root / "outbox", agent="codex")
        self.assertIn("AlreadyClaimed", type(caught.exception).__name__)
        self.assertEqual(len(sender.sent), 1, "never re-sent")
