"""Durable-letter contract. Each test names the invariant it pins."""
import pathlib
import sys
import tempfile
import unittest

from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from letter import store  # noqa: E402


class PublishAndResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_published_letter_is_found_by_its_exact_id(self):
        letter_id = store.publish(self.inbox, "hello from the phone", {"chat": "1"})
        found = store.resolve(self.inbox, letter_id)
        self.assertEqual(found.body, "hello from the phone")
        self.assertEqual(found.meta["chat"], "1")


class FenceDiscipline(unittest.TestCase):
    """A one-fence file must NEVER parse: otherwise body lines become routing
    metadata. This is the fence-spoof class."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_fence_file_is_refused(self):
        (self.inbox / "forged.md").write_text(
            "---\nchat: attacker\nbody pretending to be metadata\n", encoding="utf-8"
        )
        with self.assertRaises(store.MalformedLetter):
            store.resolve(self.inbox, "forged")

    def test_a_letter_written_with_crlf_still_parses(self):
        """We write LF, but the store may one day read a letter a foreign
        writer produced - a Windows tool, an editor, a copy through another
        system. Refusing it would look like corruption rather than a line
        ending."""
        (self.inbox / "foreign.md").write_text(
            "---\r\nchat_id: 111\r\n---\r\nhello from elsewhere\r\n",
            encoding="utf-8", newline="")
        found = store.resolve(self.inbox, "foreign")
        self.assertEqual(found.meta["chat_id"], "111")
        self.assertEqual(found.body, "hello from elsewhere")

    def test_a_crlf_single_fence_is_still_refused(self):
        """Normalising line endings must not weaken the fence rule."""
        (self.inbox / "forged.md").write_text(
            "---\r\nchat_id: attacker\r\nbody as metadata\r\n",
            encoding="utf-8", newline="")
        with self.assertRaises(store.MalformedLetter):
            store.resolve(self.inbox, "forged")

    def test_no_fence_file_is_refused(self):
        (self.inbox / "plain.md").write_text("just text\n", encoding="utf-8")
        with self.assertRaises(store.MalformedLetter):
            store.resolve(self.inbox, "plain")


class IdentifierDiscipline(unittest.TestCase):
    """Identifiers are not paths, and resolution is exact. Both prevent a
    reply reaching the wrong chat, or state escaping the store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_shaped_identifiers_are_refused_not_resolved(self):
        for crafted in ("../escape", "sub/dir", "/absolute", "..", "a\\b"):
            with self.subTest(id=crafted):
                with self.assertRaises(store.UnsafeIdentifier):
                    store.resolve(self.inbox, crafted)

    def test_a_partial_identifier_does_not_resolve(self):
        """Substring matching misdelivers. Only an exact id resolves."""
        letter_id = store.publish(self.inbox, "body", {"chat": "1"})
        with self.assertRaises(store.NoSuchLetter):
            store.resolve(self.inbox, letter_id[:10])

    def test_unknown_identifier_refuses(self):
        with self.assertRaises(store.NoSuchLetter):
            store.resolve(self.inbox, "20260101T000000-deadbeef")


class AtomicPublish(unittest.TestCase):
    """A partial letter is never visible. Publish is temp-file + hardlink, so
    the destination name either exists complete or does not exist at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_failed_publish_leaves_no_letter_behind(self):
        """If the link step fails, no letter may appear. A direct write to the
        destination would leave a readable, possibly partial letter."""
        with mock.patch("letter.store.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.publish(self.inbox, "half a message", {"chat": "1"})
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    def test_a_failed_publish_leaves_no_temp_file_behind(self):
        with mock.patch("letter.store.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.publish(self.inbox, "half a message", {"chat": "1"})
        self.assertEqual(list(self.inbox.iterdir()), [])


class DeliveredIdsLedger(unittest.TestCase):
    """Redelivery must never produce a duplicate letter.

    The ledger is consulted BEFORE publish and written AFTER the letter. That
    order is not arbitrary: it fails toward duplicate-with-evidence. Writing the
    ledger first would silently skip a redelivered letter that never landed.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)
        self.ledger = self.inbox / "delivered.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_same_update_publishes_only_once(self):
        first = store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})
        second = store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})
        self.assertIsNotNone(first)
        self.assertIsNone(second, "a redelivered update produced a second letter")
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)

    def test_different_updates_each_publish(self):
        store.publish_once(self.inbox, self.ledger, "update-1", "one", {})
        store.publish_once(self.inbox, self.ledger, "update-2", "two", {})
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 2)

    def test_a_crash_between_letter_and_ledger_does_not_duplicate(self):
        """THE CRASH WINDOW. The letter lands, then the process dies before the
        ledger records it. On redelivery the ledger is silent, so a ledger-only
        check would publish a SECOND letter for the same update.

        The ledger is a fast path, not the only evidence. The letters on disk
        are also evidence, and they outlive the ledger write."""
        with mock.patch("letter.store._record_delivered",
                        side_effect=OSError("crash after the letter landed")):
            with self.assertRaises(OSError):
                store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)

        # The platform redelivers. The ledger never recorded it.
        again = store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})
        self.assertIsNone(again, "republished an update whose letter already exists")
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1, "duplicate letter")

    def test_a_letter_already_swept_to_processed_is_not_republished(self):
        """The inbox is swept, so the evidence moves. A durable lookup must
        cover processed too, or every swept letter can be republished."""
        processed = self.inbox.parent / "processed"
        processed.mkdir(exist_ok=True)
        store.publish_once(self.inbox, self.ledger, "update-1", "hi", {},
                           searched=[self.inbox, processed])
        for f in self.inbox.glob("*.md"):
            f.rename(processed / f.name)
        self.ledger.unlink(missing_ok=True)

        again = store.publish_once(self.inbox, self.ledger, "update-1", "hi", {},
                                   searched=[self.inbox, processed])
        self.assertIsNone(again, "republished a letter that was already swept")
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 0)

    def test_a_token_collision_does_not_suppress_a_real_letter(self):
        """The filename token is a truncated digest, so it is an INDEX, not an
        identity. Two distinct updates can collide in 48 bits.

        A collision that suppressed publication would silently LOSE a message -
        strictly worse than the duplicate this lookup exists to prevent. The
        match must be verified against the letter's own recorded update id
        before it is believed.
        """
        with mock.patch("letter.store.update_token", return_value="collide"):
            first = store.publish_once(self.inbox, self.ledger, "update-1", "one", {})
            self.assertIsNotNone(first)
            self.ledger.unlink(missing_ok=True)  # force the file lookup path
            second = store.publish_once(self.inbox, self.ledger, "update-2", "two", {})
        self.assertIsNotNone(second, "a token collision suppressed a different update")
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 2)

    def test_a_verified_match_still_suppresses_a_true_redelivery(self):
        """The verification must not disable the dedup it protects."""
        store.publish_once(self.inbox, self.ledger, "update-1", "one", {})
        self.ledger.unlink(missing_ok=True)
        again = store.publish_once(self.inbox, self.ledger, "update-1", "one", {})
        self.assertIsNone(again, "verification broke the dedup")

    def test_a_failed_publish_is_not_recorded_as_delivered(self):
        """THE ORDERING PROOF. If the letter never landed, the ledger must not
        claim it did - otherwise the redelivery is silently dropped and the
        message is lost, which is the exact failure this project exists to
        prevent."""
        with mock.patch("letter.store.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})

        # The platform redelivers. It must publish this time.
        recovered = store.publish_once(self.inbox, self.ledger, "update-1", "hi", {})
        self.assertIsNotNone(recovered, "ledger recorded a letter that never landed")
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
