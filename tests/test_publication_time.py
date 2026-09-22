"""Publication timestamps are UTC metadata, never transport receipts."""
import datetime
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alb.letter import store
from alb.outbound import store as outbound
from alb.send.reply import AlreadyClaimed


class PublicationTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_utc_publication_under_non_utc_hosts_and_calendar_boundaries(self):
        original = os.environ.get("TZ")
        samples = ("2026-01-31T23:59:59Z", "2026-07-31T23:59:59Z",
                   "2026-03-29T01:00:00Z", "2026-10-25T01:00:00Z")
        zones = ("UTC0", "UTC-14", "UTC+12", "GMT0BST,M3.5.0/1,M10.5.0/2")
        non_utc_controls = 0
        try:
            for zone in zones:
                os.environ["TZ"] = zone
                time.tzset()
                for sent in samples:
                    with self.subTest(zone=zone, sent=sent):
                        instant = datetime.datetime.fromisoformat(sent.replace("Z", "+00:00"))
                        epoch = instant.timestamp()
                        expected_stamp = instant.strftime("%Y-%m-%dT%H%M%S")
                        if time.strftime("%Y-%m-%dT%H%M%S", time.localtime(epoch)) != expected_stamp:
                            non_utc_controls += 1
                        with mock.patch.object(store.time, "time", return_value=epoch):
                            ident = store.publish(self.inbox, "synthetic", store.envelope("relay", "agent"))
                            reply_id = outbound.compose(self.root / "outbox", self.root / "state",
                                                        ident, "123", "agent", "synthetic reply")
                        self.assertEqual(ident[:17], expected_stamp)
                        self.assertEqual(store.resolve(self.inbox, ident).meta["sent"], sent)
                        self.assertEqual(store.resolve(self.root / "outbox", reply_id).meta["sent"], sent)
            self.assertGreater(non_utc_controls, 0, "timezone controls never changed local time")
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_id_and_sent_share_one_clock_read_at_midnight(self):
        epoch = datetime.datetime(2026, 1, 31, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp()
        with mock.patch.object(store.time, "time", side_effect=[epoch, epoch + 1]) as clock:
            ident = store.publish(self.inbox, "synthetic", {})
        clock.assert_called_once_with()
        self.assertEqual(ident[:17], "2026-01-31T235959")
        self.assertEqual(store.resolve(self.inbox, ident).meta["sent"], "2026-01-31T23:59:59Z")

    def test_envelope_built_earlier_does_not_stamp_publication(self):
        before = datetime.datetime(2026, 1, 31, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp()
        with mock.patch.object(store.time, "time", return_value=before):
            meta = store.envelope("relay", "agent")
        with mock.patch.object(store.time, "time", return_value=before + 1):
            ident = store.publish(self.inbox, "synthetic", meta)
        self.assertEqual(ident[:17], "2026-02-01T000000")
        self.assertEqual(store.resolve(self.inbox, ident).meta["sent"], "2026-02-01T00:00:00Z")

    def test_publisher_owns_sent_without_mutating_callers_metadata(self):
        meta = {"id": "", "sent": "not a timestamp\ninjected: value"}
        original = dict(meta)
        with mock.patch.object(store.time, "time", return_value=0):
            ident = store.publish(self.inbox, "synthetic", meta)
        found = store.resolve(self.inbox, ident)
        self.assertEqual(meta, original)
        self.assertEqual(found.meta["id"], ident)
        self.assertEqual(found.meta["sent"], "1970-01-01T00:00:00Z")
        self.assertNotIn("injected", found.meta)

    def test_tokenized_and_plain_publications_get_utc_sent(self):
        for update in (None, "synthetic-update"):
            with self.subTest(update=update), mock.patch.object(store.time, "time", return_value=0):
                ident = store.publish(self.inbox, "synthetic", {}, update_id=update)
                self.assertEqual(ident[:17], "1970-01-01T000000")
                self.assertEqual(store.resolve(self.inbox, ident).meta["sent"], "1970-01-01T00:00:00Z")
                if update is not None:
                    self.assertTrue(ident.endswith("-u" + store.update_token(update)))

    def test_legacy_redelivery_preserves_original_bytes_and_missing_sent(self):
        update = "synthetic-legacy-update"
        ident = "2026-01-01T000000-1234abcd-u" + store.update_token(update)
        original = store._serialise({"id": ident, "source_id": update}, "legacy synthetic body").encode()
        path = self.inbox / (ident + ".md")
        path.write_bytes(original)
        with mock.patch.object(store.time, "time", side_effect=AssertionError("must not republish")):
            self.assertIsNone(store.publish_once(self.inbox, self.root / "delivered.json", update,
                                                "different body", {}))
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn("sent", store.resolve(self.inbox, ident).meta)

    def test_reply_keeps_claim_identity_and_does_not_rewrite_on_retry(self):
        source_id = "2026-01-31T235900-synthetic"
        outbox, state = self.root / "outbox", self.root / "state"
        with mock.patch.object(outbound.time, "time", return_value=0):
            ident = outbound.compose(outbox, state, source_id, "123", "agent", "synthetic reply")
        self.assertEqual(ident, "reply-" + source_id)
        found = store.resolve(outbox, ident)
        self.assertEqual(found.meta["sent"], "1970-01-01T00:00:00Z")
        self.assertEqual(found.meta["re"], source_id)
        self.assertEqual(found.meta["thread"], source_id)
        path = outbox / (ident + ".md")
        original = path.read_bytes()
        with self.assertRaises(AlreadyClaimed):
            outbound.compose(outbox, state, source_id, "123", "agent", "different reply")
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
