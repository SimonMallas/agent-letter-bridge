"""Discord mail becomes a letter through the poller Telegram already uses."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from alb.adapters.discord.api import Discord  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.media import store as media_store  # noqa: E402
from discord_fakes import (  # noqa: E402
    CHANNEL, MESSAGE, TOKEN, USER, Clock, FakeSocket, hello, message_create,
    ready,
)

FILE_ID = "100000000000000031"
CDN_URL = "https://cdn.discordapp.com/attachments/100000000000000004/pic.png"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class Connect:
    def __init__(self, clock, frames):
        self.clock = clock
        self.frames = frames

    def __call__(self, url, timeout=30):
        return FakeSocket(self.clock, self.frames, ack=True)


class FakeTransport:
    def __init__(self):
        self.rung = []

    def deliver(self, surface, line):
        self.rung.append((surface, line))


class DiscordPoller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for name in ("inbox", "processed", "state"):
            (self.root / name).mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{USER}"]}))
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, token, body=None, timeout=30, opener=None):
        self.assertNotIn(TOKEN, path)
        if path == "/gateway/bot":
            return {"url": "wss://gateway.example"}
        if method == "GET" and "/messages?" in path:
            return []
        if method == "GET" and path.startswith("/channels/"):
            return {"id": path.rsplit("/", 1)[-1], "type": 1}
        raise AssertionError((method, path))

    def bridge(self):
        frames = [hello(), ready(), message_create(
            MESSAGE, "a picture", attachments=[{
                "id": FILE_ID,
                "content_type": "image/png",
                "url": CDN_URL,
            }],
        )]
        return Discord(
            TOKEN, state=self.root / "state", allowlist_path=self.allow,
            poll_timeout=1, call=self.call, connect=Connect(self.clock, frames),
            clock=self.clock, monotonic=self.clock,
        )

    def test_discord_message_becomes_a_letter_with_its_image(self):
        def get_bytes(url, max_bytes, opener=None):
            self.assertEqual(url, CDN_URL)
            self.assertNotIn(TOKEN, url)
            return PNG

        with mock.patch("alb.adapters.discord.rest.get_bytes", side_effect=get_bytes):
            result = run.run_once(
                self.bridge(), FakeTransport(), "SURFACE-1", self.root,
            )
        self.assertEqual(len(result), 1)
        found = list((self.root / "inbox").glob("*.md"))
        self.assertEqual(len(found), 1)
        letter = letters.resolve(self.root / "inbox", found[0].stem)
        self.assertEqual(letter.body, "a picture")
        self.assertEqual(letter.meta["discord_channel"], CHANNEL)
        self.assertEqual(letter.meta["discord_message"], MESSAGE)
        self.assertEqual(letter.meta["source_id"], f"discord:{CHANNEL}:{MESSAGE}")
        self.assertTrue(letter.meta.get("correspondent"))
        self.assertFalse(any(key.startswith("telegram_") for key in letter.meta))
        self.assertNotIn("discord_guild", letter.meta)
        self.assertEqual(letter.meta.get("media_kind"), "image")
        self.assertEqual(letter.meta.get("media_type"), "image/png")
        self.assertTrue(letter.meta.get("media_asset_id"))
        data = media_store.load_content(
            self.root / "state", letter.meta["id"], letter.meta["media_asset_id"],
        )
        self.assertEqual(data, PNG)
        blob = found[0].read_text(encoding="utf-8")
        self.assertNotIn(CDN_URL, blob)
        self.assertNotIn(FILE_ID, blob)
        self.assertNotIn("pic.png", blob)
        key = media_store._safe_update_id(f"discord:{CHANNEL}:{MESSAGE}")
        self.assertEqual(len(key), 25)
        self.assertTrue(key.startswith("d"))
        self.assertNotIn(CHANNEL, key)


if __name__ == "__main__":
    unittest.main()
