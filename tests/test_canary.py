"""Canary: prove the send path is alive, on a schedule the operator owns."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from canary import probe  # noqa: E402
from send import reply  # noqa: E402


class FakeSender:
    def __init__(self, outcome="sent"):
        self.outcome = outcome
        self.calls = []

    def send(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.outcome == "ambiguous":
            raise reply.AmbiguousOutcome("connection reset")
        return "ok"


class CanaryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for name in ("inbox", "processed", "state"):
            (self.root / name).mkdir()
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_sends_to_the_operators_own_allowlisted_chat(self):
        sender = FakeSender()
        probe.run(sender, self.root)
        self.assertEqual(sender.calls[0][0], "111")

    def test_it_refuses_when_the_allowlist_is_empty(self):
        """No allowlisted chat means nowhere legitimate to send. A canary that
        invents a destination is worse than no canary."""
        (self.root / "allowlist.json").write_text(json.dumps({"chats": []}))
        with self.assertRaises(probe.NoCanaryTarget):
            probe.run(FakeSender(), self.root)

    def test_it_goes_through_the_real_send_path(self):
        """A canary that bypasses the claim ledger tests nothing that matters -
        it must exercise what a real reply exercises."""
        probe.run(FakeSender(), self.root)
        claims = list((self.root / "state" / "reply-attempts").glob("*.json"))
        self.assertEqual(len(claims), 1)
        self.assertEqual(json.loads(claims[0].read_text())["outcome"], "sent")

    def test_two_runs_do_not_collide_on_the_claim(self):
        """The claim is per letter and text. A fixed canary body would make the
        second run refuse as a replay and look like a failure."""
        probe.run(FakeSender(), self.root)
        probe.run(FakeSender(), self.root)
        self.assertEqual(len(list((self.root / "state" / "reply-attempts").glob("*.json"))), 2)

    def test_the_outcome_is_logged_locally(self):
        """The operator has no bus and no team. The log is the record."""
        probe.run(FakeSender(), self.root)
        log = (self.root / "state" / "canary.log").read_text(encoding="utf-8")
        self.assertIn("sent", log)

    def test_a_failure_is_logged_and_raised(self):
        with self.assertRaises(reply.AmbiguousOutcome):
            probe.run(FakeSender(outcome="ambiguous"), self.root)
        log = (self.root / "state" / "canary.log").read_text(encoding="utf-8")
        self.assertIn("ambiguous", log.lower())

    def test_the_canary_letter_does_not_pollute_the_real_inbox(self):
        """A fixture letter is not mail. It must not be swept, read or acted on
        as though someone had sent it."""
        probe.run(FakeSender(), self.root)
        self.assertEqual(list((self.root / "inbox").glob("*.md")), [])
