"""W1: the outbound letter, whose creation IS the claim.

The v0.2 spec's crash machine, first slice. One logical reply per source
letter, established in a single atomic operation: the outbound letter id is
deterministic from the source id, and its O_EXCL create in outbox/ is the
claim. Delivery outcomes are immutable event FILES; the letter is never
rewritten after creation.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.outbound import store as outbound  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.send.reply import AlreadyClaimed  # noqa: E402


SOURCE_ID = "2026-09-02T190000-telegram-msg-77-abcd1234"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.mail = base / "mail"
        (self.mail / "outbox").mkdir(parents=True)
        self.root = base / "root"
        (self.root / "state").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def compose(self, body="the reply", **kw):
        return outbound.compose(
            self.mail / "outbox", self.root / "state",
            source_id=SOURCE_ID, origin_chat="111",
            sender="codex", body=body, **kw)


class TheLetterIsTheClaim(Base):
    def test_the_id_is_deterministic_from_the_source(self):
        letter_id = self.compose()
        self.assertEqual(letter_id, f"reply-{SOURCE_ID}")

    def test_a_second_compose_for_the_same_source_is_refused(self):
        """The whole point: no separate claim step can race the letter,
        because they are one atomic operation. The loser fails BEFORE any
        orphan exists."""
        self.compose()
        with self.assertRaises(AlreadyClaimed):
            self.compose(body="different words entirely")

    def test_the_refusal_leaves_no_orphan(self):
        self.compose()
        try:
            self.compose(body="second attempt")
        except AlreadyClaimed:
            pass
        letters_on_disk = list((self.mail / "outbox").glob("*.md"))
        self.assertEqual(len(letters_on_disk), 1)

    def test_the_letter_parses_with_the_standard_store(self):
        """Outbound letters are letters - the same two-fence envelope the
        rest of the product already reads."""
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.body.strip(), "the reply")

    def test_gate0_routable_ids_and_correspondent_provenance(self):
        """from/to stay routable participants; the external principal is a
        provenance field. The correspondent key is the stored stable opaque
        derivation, never the alias, never the raw chat id."""
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.meta.get("from"), "codex")
        self.assertEqual(stored.meta.get("to"), "telegram-bridge")
        self.assertEqual(stored.meta.get("re"), SOURCE_ID)
        self.assertEqual(stored.meta.get("type"), "info")
        key = stored.meta.get("correspondent", "")
        self.assertEqual(len(key), 16)
        self.assertNotIn("111", key)

    def test_the_store_beats_the_derivation(self):
        """Authoritative means the stored value wins even when it does not
        match what today's derivation would produce - that is what lets the
        scheme change without splitting identities."""
        import json as j
        (self.root / "state" / "correspondents.json").write_text(
            j.dumps({"telegram:111": "legacykey0000001"}), encoding="utf-8")
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.meta.get("correspondent"), "legacykey0000001")

    def test_the_correspondent_key_is_stable_across_composes(self):
        """Derived once, stored, the store authoritative thereafter."""
        self.compose()
        first = json.loads((self.root / "state" / "correspondents.json").read_text())
        letter_id2 = outbound.compose(
            self.mail / "outbox", self.root / "state",
            source_id="2026-09-02T191111-telegram-msg-78-ffff0000",
            origin_chat="111", sender="codex", body="again")
        second = json.loads((self.root / "state" / "correspondents.json").read_text())
        self.assertEqual(first, second)


class EventsAreImmutableFiles(Base):
    def test_composed_event_exists_after_compose(self):
        letter_id = self.compose()
        event = self.root / "state" / "receipts" / letter_id / "1-composed.json"
        self.assertTrue(event.is_file())
        payload = json.loads(event.read_text())
        self.assertEqual(payload["event"], "composed")
        self.assertIn("at", payload)

    def test_no_platform_fact_on_the_letter_ever(self):
        """The letter is written pre-send and never rewritten; anything the
        platform returns later belongs in events, not the envelope."""
        letter_id = self.compose()
        text = next((self.mail / "outbox").glob(f"{letter_id}.md")).read_text()
        self.assertNotIn("message_id", text)

    def test_record_event_appends_a_new_numbered_file(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sent",
                              platform_message_id="4242")
        d = self.root / "state" / "receipts" / letter_id
        names = sorted(p.name for p in d.iterdir())
        self.assertEqual(names, ["1-composed.json", "2-sending.json", "3-sent.json"])
        sent = json.loads((d / "3-sent.json").read_text())
        self.assertEqual(sent["platform_message_id"], "4242")

    def test_a_seq_collision_refuses_rather_than_truncates(self):
        """The pin the gate demanded: two processes computing the same next
        sequence must not silently rewrite history. Whatever names the file,
        O_EXCL at the open is the guarantee - simulate the race by forcing
        the same path twice and expect refusal, with the first content
        intact."""
        from unittest import mock
        letter_id = self.compose()
        d = self.root / "state" / "receipts" / letter_id
        taken = d / "2-sending.json"
        outbound.record_event(self.root / "state", letter_id, "sending")
        original = taken.read_text()
        with mock.patch.object(outbound, "_event_path", return_value=taken):
            with self.assertRaises(FileExistsError):
                outbound.record_event(self.root / "state", letter_id, "sending")
        self.assertEqual(taken.read_text(), original)

    def test_events_never_overwrite(self):
        """Immutable means immutable: recording the same transition twice
        yields two files, not one rewritten - history is append-only even
        when the caller stutters."""
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sending")
        d = self.root / "state" / "receipts" / letter_id
        self.assertEqual(len(list(d.iterdir())), 3)


class RestartReconciliation(Base):
    def test_in_flight_without_terminal_is_ambiguous(self):
        """Code cannot prove whether the syscall reached the platform."""
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        verdicts = outbound.reconcile(self.root / "state")
        self.assertEqual(verdicts, {letter_id: "ambiguous"})

    def test_composed_but_never_sending_is_unsent(self):
        letter_id = self.compose()
        verdicts = outbound.reconcile(self.root / "state")
        self.assertEqual(verdicts, {letter_id: "unsent"})

    def test_terminal_states_reconcile_clean(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sent",
                              platform_message_id="1")
        self.assertEqual(outbound.reconcile(self.root / "state"), {})
