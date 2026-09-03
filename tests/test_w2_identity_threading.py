"""W2: identity and threading, per the approved spec.

Inbound letters gain telegram_message_id and a stable correspondent key;
a private exact-match index maps (platform, origin, message id) -> letter id
in BOTH directions; threads are one-per-correspondent, cut only by /new at
position zero; platform reply-to links via the index without moving the
current-thread pointer.
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
from alb import msgindex  # noqa: E402


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
        return loop.poll_once(FakePlatform(updates), self.inbox, self.ledger,
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
        self.assertNotIn("111", ka)

    def test_different_chats_get_different_keys(self):
        [a] = self.poll([update(1, "111", "x", message_id=1)])
        [b] = self.poll([update(2, "222", "y", message_id=2)])
        self.assertNotEqual(self.letter(a).meta["correspondent"],
                            self.letter(b).meta["correspondent"])


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
    def test_first_letter_roots_its_own_thread(self):
        [lid] = self.poll([update(1, "111", "hello", message_id=1)])
        self.assertEqual(self.letter(lid).meta.get("thread"), lid)

    def test_the_thread_continues_for_the_correspondent(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "two", message_id=2)])
        self.assertEqual(self.letter(b).meta["thread"], a)

    def test_slash_new_at_position_zero_cuts(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "/new fresh start", message_id=2)])
        [c] = self.poll([update(3, "111", "continues", message_id=3)])
        self.assertEqual(self.letter(b).meta["thread"], b)
        self.assertEqual(self.letter(c).meta["thread"], b)

    def test_slash_new_stays_in_the_body(self):
        """Letters are never rewritten - the token is content too."""
        [b] = self.poll([update(1, "111", "/new fresh", message_id=1)])
        self.assertIn("/new fresh", self.letter(b).body)

    def test_slash_new_not_at_position_zero_is_just_text(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "111", "please /new nothing", message_id=2)])
        self.assertEqual(self.letter(b).meta["thread"], a)

    def test_correspondents_do_not_share_threads(self):
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "222", "other person", message_id=2)])
        self.assertEqual(self.letter(b).meta["thread"], b)


class PlatformReplyLinks(Base):
    def test_reply_to_maps_re_and_joins_that_thread(self):
        [a] = self.poll([update(1, "111", "question", message_id=10)])
        [b] = self.poll([update(2, "111", "/new topic", message_id=11)])
        [c] = self.poll([update(3, "111", "answer to old", message_id=12,
                                reply_to=10)])
        meta = self.letter(c).meta
        self.assertEqual(meta.get("re"), a)
        self.assertEqual(meta.get("thread"), a)

    def test_replying_into_an_old_thread_does_not_move_the_pointer(self):
        [a] = self.poll([update(1, "111", "old", message_id=10)])
        [b] = self.poll([update(2, "111", "/new current", message_id=11)])
        self.poll([update(3, "111", "reply to old", message_id=12, reply_to=10)])
        [d] = self.poll([update(4, "111", "plain continues current", message_id=13)])
        self.assertEqual(self.letter(d).meta["thread"], b)

    def test_unknown_reply_target_is_exact_match_only(self):
        [a] = self.poll([update(1, "111", "one", message_id=10)])
        [b] = self.poll([update(2, "111", "reply to unseen", message_id=12,
                                reply_to=999)])
        meta = self.letter(b).meta
        self.assertEqual(meta.get("re", ""), "")
        self.assertEqual(meta["thread"], a)


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

    def test_a_phone_reply_to_the_bots_message_resolves_to_the_outbound_letter(self):
        from alb.send import reply as send_mod
        [a] = self.poll([update(1, "111", "q", message_id=10)])
        class Sender:
            def send(self, chat_id, text): return "777"
        out_id = send_mod.send_reply(
            Sender(), self.inbox, self.state, self.allow, a, "answer",
            outbox=self.root / "outbox", agent="codex")
        [c] = self.poll([update(2, "111", "thanks!", message_id=11, reply_to=777)])
        meta = self.letter(c).meta
        self.assertEqual(meta.get("re"), out_id)
