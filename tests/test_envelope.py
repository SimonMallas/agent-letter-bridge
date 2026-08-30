"""Letters ship in the inter-agent envelope from day one.

The format a public v0.1 freezes is the one every user's disk carries forever.
Writing the standard envelope now costs three lines; retrofitting it after
release costs a converter or a compatibility story permanently.

It also makes integration a directory path rather than a feature: point the
bridge at an existing inter-agent inbox and the letters are already the right
shape.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from letter import store  # noqa: E402


class Envelope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _publish(self, **kw):
        meta = store.envelope(sender="telegram-bridge", recipient="claude", **kw)
        lid = store.publish(self.inbox, "hello", meta)
        return lid, (self.inbox / f"{lid}.md").read_text(encoding="utf-8")

    def test_the_envelope_carries_the_standard_fields(self):
        _, text = self._publish()
        for field in ("id:", "from:", "to:", "type:", "re:", "priority:",
                      "requires_ack:", "deadline:"):
            with self.subTest(field=field):
                self.assertIn(field, text)

    def test_the_id_timestamp_is_dashed(self):
        """Byte parity with the living inter-agent format, checked against its
        parser rather than its appearance: the timestamp is extracted with a
        DASHED date pattern, so an undashed id parses as unknown and any
        timestamp-derived logic silently loses its input.

        Frozen at v0.1, so this is fixed before a public letter exists.
        """
        import re
        letter_id, _ = self._publish()
        self.assertRegex(letter_id, r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}-")

    def test_an_empty_value_is_written_as_a_bare_key(self):
        """Production writes 're:' with nothing after it. Both parse the same,
        but the format is frozen, so match the bytes rather than rely on a
        parser being forgiving."""
        _, text = self._publish()
        self.assertIn("\nre:\n", text)
        self.assertIn("\ndeadline:\n", text)

    def test_the_id_field_is_the_letter_id(self):
        letter_id, text = self._publish()
        self.assertIn(f"id: {letter_id}", text)

    def test_sender_and_recipient_come_from_configuration(self):
        _, text = self._publish()
        self.assertIn("from: telegram-bridge", text)
        self.assertIn("to: claude", text)

    def test_platform_fields_are_carried_alongside_not_instead(self):
        meta = store.envelope(sender="telegram-bridge", recipient="claude",
                              extra={"telegram_chat_id": "111",
                                     "telegram_update_id": "7"})
        lid = store.publish(self.inbox, "hello", meta)
        text = (self.inbox / f"{lid}.md").read_text(encoding="utf-8")
        self.assertIn("telegram_chat_id: 111", text)
        self.assertIn("from: telegram-bridge", text)

    def test_the_envelope_precedes_the_platform_fields(self):
        """A reader scanning the head of a letter should meet routing before
        transport trivia."""
        # Built platform-fields-FIRST on purpose. If the ordering were
        # incidental - a side effect of how envelope() happens to build the
        # dict - this test would pass without _ordered doing any work. The
        # mutation gate caught exactly that.
        meta = {"telegram_chat_id": "111", "telegram_update_id": "7"}
        meta.update(store.envelope(sender="s", recipient="r"))
        lid = store.publish(self.inbox, "hello", meta)
        text = (self.inbox / f"{lid}.md").read_text(encoding="utf-8")
        self.assertLess(text.index("from:"), text.index("telegram_chat_id:"))

    def test_an_enveloped_letter_still_resolves(self):
        letter_id, _ = self._publish()
        found = store.resolve(self.inbox, letter_id)
        self.assertEqual(found.body, "hello")
        self.assertEqual(found.meta["from"], "telegram-bridge")

    def test_type_defaults_to_info_and_no_ack_is_requested(self):
        """Platform mail is not a task. It must not enter an inter-agent
        letterbox demanding an acknowledgement."""
        _, text = self._publish()
        self.assertIn("type: info", text)
        self.assertIn("requires_ack: false", text)
