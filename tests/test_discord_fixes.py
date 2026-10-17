"""Faults found by reading the inbound branch against the gateway and the API."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from alb.adapters.discord.api import Discord, bot_user_from_token  # noqa: E402
from discord_fakes import (  # noqa: E402
    BOT, CHANNEL, MESSAGE, TOKEN, USER, Clock, FakeSocket, hello,
    message_create, resumed,
)


class Connect:
    def __init__(self, clock, frames):
        self.clock = clock
        self.frames = frames

    def __call__(self, url, timeout=30):
        return FakeSocket(self.clock, self.frames, ack=True)


class Fixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        self.clock = Clock()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, token, body=None, timeout=30, opener=None):
        self.calls.append((method, path))
        self.assertNotIn(TOKEN, path)
        if path == "/gateway/bot":
            return {"url": "wss://gateway.example"}
        if method == "GET" and "/messages?" in path:
            return []
        if method == "GET" and path.startswith("/channels/"):
            return {"id": path.rsplit("/", 1)[-1], "type": 1}
        raise AssertionError((method, path))

    def test_resumed_fresh_process_delivers_and_catches_up(self):
        # The saved session has no bot user. A restart must still know who it is.
        self.assertEqual(bot_user_from_token(TOKEN), BOT)
        (self.root / "state" / "discord-session.json").write_text(json.dumps({
            "session_id": "s1",
            "resume_gateway_url": "wss://resume.example",
            "seq": 4,
            "at": self.clock.now - 10,
        }), encoding="utf-8")
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{USER}"]}), encoding="utf-8")
        bridge = Discord(
            TOKEN, state=self.root / "state", allowlist_path=allow,
            poll_timeout=1, call=self.call,
            connect=Connect(self.clock, [
                hello(), resumed(), message_create(MESSAGE, "sent during the restart"),
            ]),
            clock=self.clock, monotonic=self.clock,
        )
        items = bridge.fetch()
        self.assertEqual([item["text"] for item in items], ["sent during the restart"])
        history = [path for _method, path in self.calls if "/messages?" in path]
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].startswith(
            f"/channels/{CHANNEL}/messages?limit=100&after=",
        ))
        self.assertTrue(history[0].split("after=", 1)[1])
        saved = json.loads(
            (self.root / "state" / "discord-session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["bot_user"], BOT)
        self.assertNotIn(TOKEN, json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
