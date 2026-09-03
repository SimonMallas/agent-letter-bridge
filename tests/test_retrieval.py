"""W4: the retrieval CLI. Read-only, stdlib, exact - correct first.

One deviation from the spec's surface, stated for review rather than
smuggled: the spec wrote `alb list`; this CLI's grammar is flags, and one
grammar beats two, so the verbs land as --list/--show/--search/--thread/
--export with the spec's names kept. Reviewers rule.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_platform import FakePlatform  # noqa: E402
from alb.poller import loop  # noqa: E402
from alb.send import reply as send_mod  # noqa: E402
from alb import retrieval  # noqa: E402


def update(uid, chat, text, message_id=None):
    item = {"update_id": uid, "chat_id": chat, "text": text}
    if message_id is not None:
        item["message_id"] = str(message_id)
    return item


class Sender:
    def __init__(self): self.n = 0
    def send(self, chat_id, text):
        self.n += 1
        return str(700 + self.n)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for d in ("inbox", "processed", "outbox", "state"):
            (self.root / d).mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.ledger = self.root / "state" / "delivered.json"

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, updates):
        return loop.poll_once(FakePlatform(updates), self.root / "inbox",
                              self.ledger, self.allow,
                              processed=self.root / "processed",
                              state=self.root / "state")

    def send(self, letter_id, text="a reply"):
        return send_mod.send_reply(
            Sender(), self.root / "inbox", self.root / "state", self.allow,
            letter_id, text, outbox=self.root / "outbox", agent="codex")

    def mail(self):
        return [self.root / "inbox", self.root / "processed",
                self.root / "outbox"]


class Listing(Base):
    def test_lists_both_directions_newest_last(self):
        [a] = self.poll([update(1, "111", "hello", message_id=1)])
        out = self.send(a)
        rows = retrieval.list_letters(self.mail())
        self.assertEqual([r["id"] for r in rows], [a, out])
        self.assertEqual(rows[0]["direction"], "in")
        self.assertEqual(rows[1]["direction"], "out")

    def test_filter_by_correspondent(self):
        self.allow.write_text(json.dumps({"chats": ["111", "222"]}))
        [a] = self.poll([update(1, "111", "one", message_id=1)])
        [b] = self.poll([update(2, "222", "two", message_id=2)])
        key = retrieval.show(self.mail(), a)["correspondent"]
        rows = retrieval.list_letters(self.mail(), correspondent=key)
        self.assertEqual([r["id"] for r in rows], [a])


class ShowAndSearch(Base):
    def test_show_returns_envelope_and_body(self):
        [a] = self.poll([update(1, "111", "the body text", message_id=1)])
        got = retrieval.show(self.mail(), a)
        self.assertEqual(got["meta"]["id"], a)
        self.assertIn("the body text", got["body"])

    def test_show_unknown_is_exact_refusal(self):
        self.poll([update(1, "111", "x", message_id=1)])
        with self.assertRaises(retrieval.NoSuchLetter):
            retrieval.show(self.mail(), "no-such-id")

    def test_search_is_exact_substring_body_and_envelope(self):
        [a] = self.poll([update(1, "111", "the quick brown fox", message_id=1)])
        self.poll([update(2, "111", "nothing here", message_id=2)])
        hits = retrieval.search(self.mail(), "quick brown")
        self.assertEqual([h["id"] for h in hits], [a])
        self.assertEqual(retrieval.search(self.mail(), "zebra"), [])


class ThreadView(Base):
    def test_thread_returns_both_halves_in_order(self):
        [a] = self.poll([update(1, "111", "question", message_id=1)])
        out = self.send(a, "answer")
        [c] = self.poll([update(2, "111", "follow-up", message_id=2)])
        rows = retrieval.thread(self.mail(), a)
        self.assertEqual([r["id"] for r in rows], [a, out, c])

    def test_thread_accepts_any_member_id(self):
        [a] = self.poll([update(1, "111", "q", message_id=1)])
        out = self.send(a)
        by_reply = retrieval.thread(self.mail(), out)
        self.assertEqual(by_reply[0]["id"], a)


class Export(Base):
    def test_export_thread_is_a_tar_of_letters_and_receipts(self):
        [a] = self.poll([update(1, "111", "q", message_id=1)])
        out = self.send(a)
        dest = self.root / "export.tar"
        retrieval.export_thread(self.mail(), self.root / "state", a, dest)
        with tarfile.open(dest) as tar:
            names = tar.getnames()
        self.assertTrue(any(a in n for n in names))
        self.assertTrue(any(out in n and n.endswith(".md") for n in names))
        self.assertTrue(any("receipts" in n for n in names))

    def test_export_is_read_only_on_the_store(self):
        [a] = self.poll([update(1, "111", "q", message_id=1)])
        before = sorted(p.name for p in (self.root / "inbox").iterdir())
        retrieval.export_thread(self.mail(), self.root / "state", a,
                                self.root / "e.tar")
        after = sorted(p.name for p in (self.root / "inbox").iterdir())
        self.assertEqual(before, after)


class ThroughTheBinary(Base):
    """The verbs are things the BINARY does - proven through cli.main, no
    config and no token required, same standing as --status."""

    def _run(self, *argv):
        from alb import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.root), *argv])
        return code, out.getvalue()

    def test_list_show_thread_export_work_without_config(self):
        [a] = self.poll([update(1, "111", "hello world", message_id=1)])
        out_id = self.send(a)
        code, listing = self._run("--list")
        self.assertEqual(code, 0)
        self.assertIn(a, listing); self.assertIn(out_id, listing)
        code, shown = self._run("--show", a)
        self.assertEqual(code, 0); self.assertIn("hello world", shown)
        code, th = self._run("--thread", out_id)
        self.assertEqual(code, 0); self.assertIn(a, th)
        dest = self.root / "t.tar"
        code, msg = self._run("--export", a, "--out", str(dest))
        self.assertEqual(code, 0); self.assertTrue(dest.exists())

    def test_unknown_id_exits_nonzero(self):
        code, _ = self._run("--show", "nope")
        self.assertEqual(code, 1)


class ExactMeansExact(Base):
    def test_a_truncated_real_id_refuses(self):
        """The pin the gate demanded: 'no-such-id' also fails a PREFIX match,
        so only a truncated real id distinguishes exact from startswith."""
        [a] = self.poll([update(1, "111", "x", message_id=1)])
        with self.assertRaises(retrieval.NoSuchLetter):
            retrieval.show(self.mail(), a[:12])
        with self.assertRaises(retrieval.NoSuchLetter):
            retrieval.thread(self.mail(), a[:12])
