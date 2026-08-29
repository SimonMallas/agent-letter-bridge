"""Poller: untrusted. Fetch, write the letter, THEN ack.

The poller is the only process that touches external input, so it holds the
least privilege: it may not ring, notify, execute, or send.
"""
import ast
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from poller import loop  # noqa: E402


class FakePlatform:
    """A stand-in for the platform API. Records what was acked."""

    def __init__(self, updates=None, raise_conflict=False):
        self.updates = updates or []
        self.raise_conflict = raise_conflict
        self.acked_offset = None

    def fetch(self, offset):
        if self.raise_conflict:
            raise loop.PlatformConflict("another consumer holds this token")
        return self.updates

    def ack(self, offset):
        self.acked_offset = offset


def update(uid, chat, text):
    return {"update_id": uid, "chat_id": chat, "text": text}


class PollerBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.ledger = self.root / "delivered.json"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, platform):
        return loop.poll_once(platform, self.inbox, self.ledger, self.allow)

    def test_an_allowed_sender_becomes_a_letter(self):
        self._run(FakePlatform([update(1, "111", "hello")]))
        letters = list(self.inbox.glob("*.md"))
        self.assertEqual(len(letters), 1)
        self.assertIn("hello", letters[0].read_text(encoding="utf-8"))

    def test_an_unlisted_sender_produces_silence(self):
        """No letter, no error, no trace. Silence IS the deny path working."""
        self._run(FakePlatform([update(1, "999", "let me in")]))
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    def test_the_offset_is_acked_only_after_the_letter_lands(self):
        platform = FakePlatform([update(7, "111", "hello")])
        self._run(platform)
        self.assertEqual(platform.acked_offset, 7)

    def test_a_failed_write_must_not_ack(self):
        """THE INVARIANT. Acking an update whose letter never landed loses the
        message permanently once the platform's retention window passes."""
        platform = FakePlatform([update(7, "111", "hello")])
        with mock.patch("letter.store.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._run(platform)
        self.assertIsNone(platform.acked_offset, "acked an update that never landed")

    def test_a_platform_conflict_yields_rather_than_erroring(self):
        """A conflict means another consumer holds the token. Yield cleanly so
        the holder keeps running; never fight for it."""
        with self.assertRaises(loop.PlatformConflict):
            self._run(FakePlatform(raise_conflict=True))


class PollerIsStructurallyIncapable(unittest.TestCase):
    """Not 'does not ring' - CANNOT ring. Proved against the source, so a
    future edit that adds the capability fails this test."""

    FORBIDDEN = ("ring", "doorbell", "notify", "send_message", "sendMessage",
                 "subprocess", "popen", "system", "exec")

    def test_the_poller_package_contains_no_forbidden_capability(self):
        for path in (ROOT / "src" / "poller").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id.lower())
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr.lower())
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = getattr(node, "module", "") or ""
                    names.add(mod.lower())
                    for alias in node.names:
                        names.add(alias.name.lower())
            for banned in self.FORBIDDEN:
                self.assertNotIn(
                    banned, names,
                    f"{path.name} references '{banned}' - the poller must be "
                    f"incapable of this, not merely abstain from it",
                )


if __name__ == "__main__":
    unittest.main()
