"""W2: exact identity and real-link threading.

Inbound letters gain telegram_message_id and a stable correspondent key; a
private exact-match index maps platform references to stored letters in both
directions. Ordinary inbound messages root themselves; only a resolved
platform reply creates a local thread link.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_platform import FakePlatform  # noqa: E402
from alb.poller import loop  # noqa: E402
from alb.letter import store  # noqa: E402
from alb import msgindex, retrieval  # noqa: E402


def update(uid, chat, text, message_id=None, reply_to=None):
    item = {"update_id": uid, "chat_id": chat, "text": text}
    if message_id is not None:
        item["message_id"] = str(message_id)
    if reply_to is not None:
        item["reply_to_message_id"] = str(reply_to)
    return item


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"; self.inbox.mkdir()
        self.state = self.root / "state"; self.state.mkdir()
        self.ledger = self.state / "delivered.json"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111", "222"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, updates):
        return self.poll_with(FakePlatform(updates))

    def poll_with(self, platform):
        return loop.poll_once(platform, self.inbox, self.ledger,
                              self.allow, state=self.state)

    def letter(self, letter_id):
        return store.resolve(self.inbox, letter_id)


class InboundEnvelopeGrows(Base):
    def test_message_id_recorded_when_present(self):
        [lid] = self.poll([update(1, "111", "hi", message_id=900)])
        self.assertEqual(self.letter(lid).meta.get("telegram_message_id"), "900")

    def test_correspondent_key_is_stable_and_opaque(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "two", message_id=2)])
        ka = self.letter(a).meta.get("correspondent")
        self.assertEqual(len(ka), 16)
        self.assertEqual(ka, self.letter(b).meta.get("correspondent"))
        import hashlib
        self.assertNotEqual(ka, hashlib.sha256(b"telegram:111").hexdigest()[:16])

    def test_different_chats_get_different_keys(self):
        [a] = self.poll([update(1, "111", "x", message_id=1)])
        [b] = self.poll([update(2, "222", "y", message_id=2)])
        self.assertNotEqual(self.letter(a).meta["correspondent"],
                            self.letter(b).meta["correspondent"])

    def test_legacy_correspondent_and_thread_pointer_are_untouched(self):
        (self.state / "correspondents.json").write_text(
            json.dumps({"telegram:111": "legacykey0000001"}), encoding="utf-8")
        pointer = self.state / "threads.json"
        pointer.write_text(json.dumps({"legacykey0000001": "old-root"}), encoding="utf-8")
        before = pointer.read_bytes()
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "two", message_id=2)])
        self.assertEqual(self.letter(a).meta["correspondent"], "legacykey0000001")
        self.assertEqual(self.letter(b).meta["correspondent"], "legacykey0000001")
        self.assertEqual((self.letter(a).meta["thread"], self.letter(b).meta["thread"]),
                         (a, b))
        self.assertEqual(pointer.read_bytes(), before)


class TheIndexIsExactAndTriple(Base):
    def test_inbound_is_indexed_by_the_full_triple(self):
        [lid] = self.poll([update(1, "111", "hi", message_id=900)])
        hit = msgindex.lookup(self.state, "telegram", "111", "900")
        self.assertEqual(hit, lid)

    def test_same_message_id_in_another_chat_is_a_different_key(self):
        """Codex blocker 2 made normative: message ids are chat-scoped."""
        [a] = self.poll([update(1, "111", "x", message_id=5)])
        [b] = self.poll([update(2, "222", "y", message_id=5)])
        self.assertEqual(msgindex.lookup(self.state, "telegram", "111", "5"), a)
        self.assertEqual(msgindex.lookup(self.state, "telegram", "222", "5"), b)

    def test_missing_is_none_never_fuzzy(self):
        self.poll([update(1, "111", "x", message_id=5)])
        self.assertIsNone(msgindex.lookup(self.state, "telegram", "111", "6"))


class ThreadingIsExplicitOnly(Base):
    def test_plain_messages_each_root_themselves(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "two", message_id=2)])
        self.assertEqual((self.letter(a).meta["thread"], self.letter(b).meta["thread"]),
                         (a, b))

    def test_slash_new_is_plain_body_text_and_roots_itself(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "/new fresh start", message_id=2)])
        self.assertIn("/new fresh start", self.letter(b).body)
        self.assertEqual((self.letter(a).meta["thread"], self.letter(b).meta["thread"]),
                         (a, b))

    def test_correspondents_do_not_share_threads(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "222", "other person", message_id=2)])
        self.assertEqual((self.letter(a).meta["thread"], self.letter(b).meta["thread"]),
                         (a, b))


class PlatformRoutingDoesNotBecomeLocalThread(Base):
    def test_native_platform_thread_metadata_is_kept_but_message_roots_locally(self):
        class Slack(FakePlatform):
            name = "slack"
        item = update(1, "111", "channel root", message_id="100")
        item["provenance"] = {"slack_channel": "C1", "slack_ts": "100"}
        item["reply_thread"] = "100"
        [lid] = self.poll_with(Slack([item]))
        meta = self.letter(lid).meta
        self.assertEqual((meta["thread"], meta["reply_thread"]), (lid, "100"))


class PlatformReplyLinks(Base):
    def test_reply_to_maps_re_and_joins_that_thread(self):
        [a] = self.poll([update(1, "111", "question", message_id=10)])
        [c] = self.poll([update(2, "111", "answer to old", message_id=12,
                                reply_to=10)])
        meta = self.letter(c).meta
        self.assertEqual((meta.get("re"), meta.get("thread")), (a, a))

    def test_reply_does_not_change_later_plain_message_roots(self):
        [a] = self.poll([update(1, "111", "old", message_id=10)])
        self.poll([update(2, "111", "reply to old", message_id=12, reply_to=10)])
        [d] = self.poll([update(3, "111", "plain new message", message_id=13)])
        self.assertEqual(self.letter(d).meta["thread"], d)

    def test_unknown_reply_target_is_a_fresh_root_without_re(self):
        [a] = self.poll([update(1, "111", "one", message_id=10)])
        [b] = self.poll([update(2, "111", "reply to unseen", message_id=12,
                                reply_to=999)])
        meta = self.letter(b).meta
        self.assertEqual((meta.get("re", ""), meta["thread"]), ("", b))

    def _corrupt_index_target_roots_the_reply_fresh(self, folder, target, payload, platform_id):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        (directory / f"{target}.md").write_bytes(payload)
        msgindex.record(self.state, "telegram", "111", str(platform_id), target)
        [reply] = self.poll([update(2, "111", "follow-up", message_id=12,
                                    reply_to=platform_id)])
        meta = self.letter(reply).meta
        self.assertEqual((meta.get("re", ""), meta["thread"]), ("", reply))

    def test_malformed_inbox_reply_target_roots_fresh(self):
        self._corrupt_index_target_roots_the_reply_fresh("inbox", "bad-inbox", b"not a letter", 777)

    def test_malformed_outbox_reply_target_roots_fresh(self):
        self._corrupt_index_target_roots_the_reply_fresh("outbox", "bad-outbox", b"not a letter", 778)

    def test_unreadable_reply_target_roots_fresh(self):
        self._corrupt_index_target_roots_the_reply_fresh("outbox", "bad-utf8", b"\xff", 779)

    def _stored_target(self, folder, target, thread):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        meta = store.envelope(sender="telegram-bridge", recipient="agent",
                              extra={"id": target, "thread": thread})
        (directory / f"{target}.md").write_text(store._serialise(meta, "stored"),
                                                   encoding="utf-8")

    def test_conflicting_index_target_threads_root_fresh(self):
        self._stored_target("inbox", "collision", "thread-a")
        self._stored_target("outbox", "collision", "thread-b")
        msgindex.record(self.state, "telegram", "111", "780", "collision")
        [reply] = self.poll([update(2, "111", "follow-up", message_id=12,
                                    reply_to=780)])
        meta = self.letter(reply).meta
        self.assertEqual((meta.get("re", ""), meta["thread"]), ("", reply))

    def test_same_thread_duplicate_target_joins(self):
        self._stored_target("inbox", "duplicate", "thread-a")
        self._stored_target("processed", "duplicate", "thread-a")
        msgindex.record(self.state, "telegram", "111", "781", "duplicate")
        [reply] = self.poll([update(2, "111", "follow-up", message_id=12,
                                    reply_to=781)])
        meta = self.letter(reply).meta
        self.assertEqual((meta.get("re"), meta["thread"]), ("duplicate", "thread-a"))


class OutboundJoinsTheIndexAndThread(Base):
    def test_reply_carries_the_sources_thread_and_lands_in_the_index(self):
        from alb.send import reply as send_mod
        [a] = self.poll([update(1, "111", "start", message_id=10)])
        outbox = self.root / "outbox"

        class Sender:
            def send(self, chat_id, text): return "777"
        out_id = send_mod.send_reply(
            Sender(), self.inbox, self.state, self.allow, a, "the answer",
            outbox=outbox, agent="codex")
        stored = store.resolve(outbox, out_id)
        self.assertEqual(stored.meta.get("thread"), a)
        self.assertEqual(
            msgindex.lookup(self.state, "telegram", "111", "777"), out_id)

    def test_a_phone_reply_to_the_bots_message_inherits_the_outbound_thread(self):
        from alb.send import reply as send_mod
        [a] = self.poll([update(1, "111", "q", message_id=10)])
        outbox = self.root / "outbox"
        class Sender:
            def send(self, chat_id, text): return "777"
        out_id = send_mod.send_reply(
            Sender(), self.inbox, self.state, self.allow, a, "answer",
            outbox=outbox, agent="codex")
        [c] = self.poll([update(2, "111", "thanks!", message_id=11, reply_to=777)])
        meta = self.letter(c).meta
        self.assertEqual((meta.get("re"), meta.get("thread")), (out_id, a))
        self.assertEqual({row["id"] for row in retrieval.thread([self.inbox, outbox], a)},
                         {a, out_id, c})


class TheStampKeepsTheLettersMode(Base):
    """Grok's must-fix: the sanctioned thread stamp rewrote the letter via a
    umask-governed write, so an inbound letter came out 0644 after stamping.
    Same class as the state files this build already caught once."""

    def test_a_stamped_letter_stays_0600(self):
        import os, stat
        old = os.umask(0o022)
        try:
            [lid] = self.poll([update(1, "111", "hello", message_id=1)])
        finally:
            os.umask(old)
        mode = stat.S_IMODE(os.stat(self.inbox / f"{lid}.md").st_mode)
        self.assertEqual(mode, 0o600)
