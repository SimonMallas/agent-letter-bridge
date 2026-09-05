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
from alb.outbound import store as outbound  # noqa: E402
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

    def test_the_platform_field_wins_when_a_letter_carries_both(self):
        """Two independent fixes for the same defect chose opposite orders.

        Every real letter carries exactly one of these keys, so both orders
        behave identically today and neither side's tests could tell them
        apart. They differ only for a letter carrying both - which is precisely
        what a migration or a hand-edited letter produces, and precisely when
        being wrong is silent.

        The platform field wins: it is the format letters are written in now,
        and a stale chat_id would send a reply to whatever the old shape said.
        """
        letter_id = store.publish(
            self.inbox, "incoming",
            {"chat_id": "0000000", "telegram_chat_id": "8675309"})
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


class LetterFirstOutbound(unittest.TestCase):
    """W1 slice 2: send_reply writes the outbound LETTER first - its O_EXCL
    create is the claim - then brackets the platform call in immutable events.
    The old body-hash claim is gone: cardinality is per SOURCE letter, changed
    text against a claimed source refuses, and the message id the platform
    returns lands in the events, never on the letter."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"; self.inbox.mkdir()
        self.outbox = self.root / "outbox"; self.outbox.mkdir()
        self.state = self.root / "state"; self.state.mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["8675309"]}), encoding="utf-8")
        self.letter_id = store.publish(self.inbox, "incoming",
                                       {"chat_id": "8675309"})

    def tearDown(self):
        self.tmp.cleanup()

    def _send(self, sender, text="a reply"):
        return reply.send_reply(
            sender, self.inbox, self.state, self.allow, self.letter_id, text,
            outbox=self.outbox, agent="codex")

    def events(self, out_id):
        d = self.state / "receipts" / out_id
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def test_the_letter_exists_before_the_platform_is_called(self):
        seen = {}
        outbox = self.outbox

        class Peeking:
            def send(self, chat_id, text):
                seen["letters"] = list(outbox.glob("*.md"))
                return "42"
        self._send(Peeking())
        self.assertEqual(len(seen["letters"]), 1)

    def test_events_bracket_the_call_and_carry_the_message_id(self):
        class MidSender:
            def send(self, chat_id, text):
                return "4242"
        out_id = self._send(MidSender())
        names = self.events(out_id)
        self.assertEqual(names, ["1-composed.json", "2-sending.json", "3-sent.json"])
        sent = json.loads((self.state / "receipts" / out_id / "3-sent.json").read_text())
        self.assertEqual(sent["platform_message_id"], "4242")

    def test_the_letter_never_carries_the_message_id(self):
        class MidSender:
            def send(self, chat_id, text): return "4242"
        out_id = self._send(MidSender())
        text = next(self.outbox.glob(f"{out_id}.md")).read_text()
        self.assertNotIn("4242", text)

    def test_second_reply_to_same_source_refuses_even_with_new_text(self):
        self._send(FakeSender())
        with self.assertRaises(reply.AlreadyClaimed):
            self._send(FakeSender(), text="totally different words")
        self.assertEqual(len(list(self.outbox.glob("*.md"))), 1)

    def test_ambiguous_records_event_and_dead_letters(self):
        with self.assertRaises(reply.AmbiguousOutcome):
            self._send(FakeSender("ambiguous"))
        out_id = f"reply-{self.letter_id}"
        self.assertIn("3-ambiguous.json", self.events(out_id))
        dead = list((self.state / "dead-letters").glob("*.json"))
        self.assertEqual(len(dead), 1)
        payload = json.loads(dead[0].read_text())
        self.assertEqual(payload["letter_id"], self.letter_id)

    def test_refusal_records_event_no_dead_letter(self):
        with self.assertRaises(reply.DefiniteRefusal):
            self._send(FakeSender("refused"))
        out_id = f"reply-{self.letter_id}"
        self.assertIn("3-refused.json", self.events(out_id))
        self.assertEqual(list((self.state / "dead-letters").glob("*")), [])

    def test_unclassified_failure_is_ambiguous(self):
        class Buggy:
            def send(self, chat_id, text): raise RuntimeError("adapter bug")
        with self.assertRaises(reply.AmbiguousOutcome):
            self._send(Buggy())
        self.assertIn("3-ambiguous.json", self.events(f"reply-{self.letter_id}"))

    def test_allowlist_still_checked_before_any_letter_is_written(self):
        self.allow.write_text(json.dumps({"chats": ["999"]}), encoding="utf-8")
        with self.assertRaises(reply.NotPermitted):
            self._send(FakeSender())
        self.assertEqual(list(self.outbox.glob("*.md")), [])


class ThrottledKeepsTheClaim(LetterFirstOutbound):
    """A throttled send is the one failure we know the outcome of: the
    platform never looked, so nothing was delivered and nothing may be
    dead-lettered. The claim must survive the wait - releasing it mid-throttle
    would let a second composer pick the source up and invent the duplicate
    the never-retry doctrine exists to prevent."""

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=12)

    def test_it_records_the_throttle_without_dead_lettering(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        out_id = f"reply-{self.letter_id}"
        events = self.events(out_id)
        self.assertTrue(any("throttled" in name for name in events), events)
        self.assertFalse(any("ambiguous" in name for name in events), events)
        dead = self.state / "dead-letters"
        self.assertFalse(dead.exists() and any(dead.iterdir()),
                         "a throttle is provably undelivered - never dead-lettered")

    def test_the_outbound_letter_survives_so_the_claim_holds(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        out_id = f"reply-{self.letter_id}"
        self.assertTrue((self.outbox / f"{out_id}.md").exists(),
                        "the letter IS the claim; a throttle must not release it")


class AThrottledSendCanBeResumed(LetterFirstOutbound):
    """The claim is the letter, so a second compose refuses - correctly. But
    that means a throttled send had no way back: the retry policy was said to
    be "the caller's", while the caller was structurally unable to retry.

    Resume reuses the existing letter rather than composing a new one, and
    refuses to touch anything that is not actually waiting."""

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    class Working:
        def __init__(self):
            self.sent = []

        def send(self, chat_id, text):
            self.sent.append((chat_id, text))
            return "9001"

    def test_a_second_compose_still_refuses(self):
        """Unchanged, and it must stay that way: the whole no-double-send
        property rests on it."""
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        with self.assertRaises(outbound.AlreadyClaimed):
            self._send(self.Throttling())

    def test_resume_sends_the_waiting_letter_without_reclaiming(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        sender = self.Working()
        out_id = reply.resume_throttled(
            sender, self.inbox, self.state, self.allow,
            f"reply-{self.letter_id}", outbox=self.outbox)
        self.assertEqual(out_id, f"reply-{self.letter_id}")
        self.assertEqual(len(sender.sent), 1)
        events = self.events(out_id)
        self.assertTrue(any("sent" in name for name in events), events)

    def test_resume_refuses_a_letter_that_is_not_waiting(self):
        """A letter already sent, or never throttled, must not be re-sent by
        a resume - that is the double-send this design exists to prevent."""
        sender = self.Working()
        self._send(sender)
        with self.assertRaises(reply.NotDeferred):
            reply.resume_throttled(
                sender, self.inbox, self.state, self.allow,
            f"reply-{self.letter_id}", outbox=self.outbox)
        self.assertEqual(len(sender.sent), 1, "resume must not re-send")


class ResumeFindsWhatTheFirstAttemptHadInHand(LetterFirstOutbound):
    """The first send held the inbound letter; a resume has to find it again,
    and sweeps move letters. Looking only in the inbox means resume works
    until someone files their mail."""

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    class Working:
        def __init__(self):
            self.sent = []

        def send(self, chat_id, text):
            self.sent.append((chat_id, text))
            return "9001"

    def test_resume_works_after_the_source_is_filed(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        processed = self.root / "processed"
        processed.mkdir()
        (self.inbox / f"{self.letter_id}.md").rename(
            processed / f"{self.letter_id}.md")
        sender = self.Working()
        reply.resume_throttled(
            sender, self.inbox, self.state, self.allow,
            f"reply-{self.letter_id}", outbox=self.outbox,
            searched=[self.inbox, processed])
        self.assertEqual(len(sender.sent), 1)


class ACrashDuringResumeIsGenuinelyUnknown(LetterFirstOutbound):
    """Deliberate, not an oversight. Once resume writes `sending`, the syscall
    may have reached the platform - exactly the ambiguity the first attempt
    has, arrived at by the same route. So it dead-letters, and it SHOULD:
    sparing it would mean claiming certainty about a send we did not watch
    return. The deferred state ends when a new attempt begins."""

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    def test_a_resume_interrupted_mid_send_is_ambiguous_not_deferred(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        out_id = f"reply-{self.letter_id}"
        # The crash: the resume's own "sending" is written, nothing follows.
        outbound.record_event(self.state, out_id, "sending")
        self.assertEqual(outbound.reconcile(self.state).get(out_id), "ambiguous")


class OnlyOneResumerCrossesTheSendBoundary(LetterFirstOutbound):
    """The deferred check and the send are two steps, so two processes can
    both read "throttled" before either writes "sending" - and both send. The
    O_EXCL on each event file makes history append-only; it does not make the
    transition out of deferred exclusive, which is a different property.

    The window is narrow - between reconcile() and the "sending" event - so a
    test that races the SEND instead misses it entirely and passes while the
    hazard stands. This one blocks inside that window on purpose.
    """

    class Working:
        def __init__(self):
            self.calls = 0

        def send(self, chat_id, text):
            self.calls += 1
            return "9001"

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    def test_two_resumers_in_the_check_window_produce_one_send(self):
        import threading
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling())
        out_id = f"reply-{self.letter_id}"
        sender = self.Working()
        in_window, proceed = threading.Event(), threading.Event()
        real_record = outbound.record_event
        held = {"done": False}

        def blocking_record(state, letter_id, event, **fields):
            # Stop the first resumer exactly where the hazard lives: past the
            # deferred check, before the state it would consume is written.
            if event == "sending" and not held["done"]:
                held["done"] = True
                in_window.set()
                proceed.wait(2)
            return real_record(state, letter_id, event, **fields)

        outcomes = []

        def resume():
            try:
                reply.resume_throttled(
                    sender, self.inbox, self.state, self.allow, out_id,
                    outbox=self.outbox)
                outcomes.append("sent")
            except Exception as exc:  # noqa: BLE001 - the loser's refusal
                outcomes.append(type(exc).__name__)

        with mock.patch.object(outbound, "record_event", blocking_record):
            first = threading.Thread(target=resume)
            first.start()
            in_window.wait(2)
            second = threading.Thread(target=resume)
            second.start()
            second.join(3)
            proceed.set()
            first.join(3)

        self.assertEqual(sender.calls, 1,
                         f"exactly one send may cross the boundary, got {outcomes}")


class ResumeSendsWhatWasComposed(LetterFirstOutbound):
    """Retyping is the resume gesture, so the operator may retype different
    text - and the letter that exists is the one from the first attempt.
    Sending the old body under the new instruction is safe and dishonest:
    the operator sees success and the recipient sees something else."""

    class Throttling:
        def send(self, chat_id, text):
            raise reply.Throttled("throttled with HTTP 429", retry_after=1)

    class Working:
        def __init__(self):
            self.sent = []

        def send(self, chat_id, text):
            self.sent.append(text)
            return "9001"

    def test_resuming_with_different_text_refuses_rather_than_substituting(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling(), text="the original")
        sender = self.Working()
        with self.assertRaises(reply.TextChanged):
            reply.resume_throttled(
                sender, self.inbox, self.state, self.allow,
                f"reply-{self.letter_id}", outbox=self.outbox,
                text="something else entirely")
        self.assertEqual(sender.sent, [], "never send text nobody asked for")

    def test_resuming_with_the_same_text_proceeds(self):
        with self.assertRaises(reply.Throttled):
            self._send(self.Throttling(), text="the original")
        sender = self.Working()
        reply.resume_throttled(
            sender, self.inbox, self.state, self.allow,
            f"reply-{self.letter_id}", outbox=self.outbox, text="the original")
        self.assertEqual(sender.sent, ["the original"])


class ResumeRefusesPathShapedIdentifiers(LetterFirstOutbound):
    """The lock file was opened from raw caller text before anything checked
    it, so a public library call could create files anywhere the process can
    write. The refusal has to come BEFORE the filesystem is touched: a
    NotDeferred raised afterwards is not a refusal, it is a report of
    something that already happened."""

    def test_a_traversing_id_is_refused_before_anything_is_written(self):
        for bad in ("../escaped", "../../outside", "/absolute", "a/b",
                    ".", "..", "", "with space"):
            with self.subTest(out_id=bad):
                with self.assertRaises(store.UnsafeIdentifier):
                    reply.resume_throttled(
                        None, self.inbox, self.state, self.allow, bad,
                        outbox=self.outbox)
        stray = list(self.root.rglob("*.resume"))
        self.assertEqual(stray, [], f"nothing may be written: {stray}")


class MalformedReceiptNamesDoNotCrashReconciliation(LetterFirstOutbound):
    """The ordering comment claimed unparsable names sort first and cannot
    pose as current state. True of the sort key, and _history then split the
    same names unconditionally - so a stray file raised IndexError instead.
    A comment describing a safety the code does not have is worse than no
    comment: it stops the next reader checking."""

    def test_a_stray_file_does_not_crash_the_startup_pass(self):
        out_id = f"reply-{self.letter_id}"
        d = self.state / "receipts" / out_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "1-composed.json").write_text("{}", encoding="utf-8")
        (d / "stray").write_text("", encoding="utf-8")
        (d / "notanumber-sending.json").write_text("{}", encoding="utf-8")
        verdicts = outbound.reconcile(self.state)
        self.assertEqual(verdicts.get(out_id), "unsent")


class AnEmptyEventNameIsAlsoMalformed(LetterFirstOutbound):
    """Kimi's edge: "3-.json" has a dash and a number, so it passed the
    malformed filter and put an EMPTY event into the history. As the trailing
    entry it then masked the deferred verdict - a throttled letter read as
    unsent, because "" is not in DEFERRED.

    Not a duplicate-send hole: re-composing still meets AlreadyClaimed. But
    "malformed entries are skipped" was not true, and a claim that is nearly
    true is the kind the next reader stops checking."""

    def test_a_dash_with_no_event_name_does_not_mask_the_state(self):
        out_id = f"reply-{self.letter_id}"
        d = self.state / "receipts" / out_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "1-composed.json").write_text("{}", encoding="utf-8")
        (d / "2-sending.json").write_text("{}", encoding="utf-8")
        (d / "3-throttled.json").write_text("{}", encoding="utf-8")
        (d / "4-.json").write_text("{}", encoding="utf-8")
        self.assertEqual(outbound.reconcile(self.state).get(out_id), "throttled",
                         "an empty event name must not hide the real state")
