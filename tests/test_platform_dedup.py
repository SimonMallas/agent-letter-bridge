"""Platform-qualified deduplication for several Bridge roots sharing one mailbox."""
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


class Platform(FakePlatform):
    def __init__(self, name, updates):
        super().__init__(updates)
        self.name = name


def update(raw, *, message="hello"):
    return {"update_id": raw, "chat_id": "111", "text": message,
            "message_id": raw, "reply_to_message_id": ""}


class SharedMailbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.mail = self.root / "mail"
        for name in ("inbox", "processed", "outbox"):
            (self.mail / name).mkdir(parents=True, exist_ok=True)
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def poll(self, platform, raw):
        state = self.root / platform / "state"
        state.mkdir(parents=True, exist_ok=True)
        return loop.poll_once(Platform(platform, [update(raw)]), self.mail / "inbox",
                              state / "delivered.json", self.allow,
                              processed=self.mail / "processed", state=state)

    def letters(self):
        return sorted((self.mail / "inbox").glob("*.md")) + sorted((self.mail / "processed").glob("*.md"))

    def test_same_raw_id_across_platforms_publishes_separate_letters(self):
        telegram = self.poll("telegram", "42")
        discord = self.poll("discord", "42")
        slack = self.poll("slack", "42")
        self.assertEqual((len(telegram), len(discord), len(slack), len(self.letters())), (1, 1, 1, 3))
        platforms = {store.resolve(path.parent, path.stem).meta["source_platform"]
                     for path in self.letters()}
        self.assertEqual(platforms, {"telegram", "discord", "slack"})

    def test_same_platform_redelivery_publishes_once(self):
        self.assertEqual(len(self.poll("telegram", "42")), 1)
        self.assertEqual(self.poll("telegram", "42"), [])
        self.assertEqual(len(self.letters()), 1)

    def test_cross_platform_collision_after_filing_still_publishes(self):
        self.assertEqual(len(self.poll("telegram", "42")), 1)
        for path in (self.mail / "inbox").glob("*.md"):
            path.rename(self.mail / "processed" / path.name)
        self.assertEqual(len(self.poll("discord", "42")), 1)
        self.assertEqual(len(self.letters()), 2)

    def test_identifiable_legacy_telegram_letter_prevents_upgrade_republish(self):
        legacy_state = self.root / "telegram" / "state"
        legacy_state.mkdir(parents=True)
        store.publish_once(self.mail / "inbox", legacy_state / "delivered.json", "42", "old", {
            "telegram_update_id": "42", "from": "telegram-bridge", "to": "agent"},
                           searched=[self.mail / "inbox", self.mail / "processed"])
        # Simulate an old ledger lost during upgrade: on-disk platform evidence still wins.
        (legacy_state / "delivered.json").unlink()
        self.assertEqual(self.poll("telegram", "42"), [])
        self.assertEqual(len(self.letters()), 1)

    def test_unqualified_legacy_collision_refuses_without_publishing(self):
        store.publish_once(self.mail / "inbox", self.root / "old-ledger.json", "42", "old", {
            "from": "unknown-bridge", "to": "agent"}, searched=[self.mail / "inbox"])
        with self.assertRaises(store.AmbiguousUpdate):
            self.poll("discord", "42")
        self.assertEqual(len(self.letters()), 1)


if __name__ == "__main__":
    unittest.main()
