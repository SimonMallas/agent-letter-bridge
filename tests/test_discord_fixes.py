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
    BOT, CHANNEL, GUILD, MESSAGE, TOKEN, USER, Clock, FakeSocket, hello,
    message_create, ready, resumed,
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
        self.rows = {}
        self.channels = {}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, token, body=None, timeout=30, opener=None):
        self.calls.append((method, path))
        self.assertNotIn(TOKEN, path)
        if path == "/gateway/bot":
            return {"url": "wss://gateway.example"}
        if method == "GET" and "/messages?" in path:
            return self.rows.get(path.split("?", 1)[0], [])
        if method == "GET" and path.startswith("/channels/"):
            channel = path.rsplit("/", 1)[-1]
            return self.channels.get(channel, {"id": channel, "type": 1})
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

    def test_history_rows_keep_the_channel_guild(self):
        mentioned = "100000000000000081"
        plain = "100000000000000082"
        self.channels[CHANNEL] = {"id": CHANNEL, "type": 0, "guild_id": GUILD}
        self.rows[f"/channels/{CHANNEL}/messages"] = [
            {
                "id": plain, "channel_id": CHANNEL, "type": 0,
                "content": "no mention", "author": {"id": USER},
            },
            {
                "id": mentioned, "channel_id": CHANNEL, "type": 0,
                "content": f"<@{BOT}> hi", "author": {"id": USER},
                "mentions": [{"id": BOT}],
            },
        ]
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{USER}"]}), encoding="utf-8")
        bridge = Discord(
            TOKEN, state=self.root / "state", allowlist_path=allow,
            poll_timeout=1, call=self.call,
            connect=Connect(self.clock, [hello(), ready()]),
            clock=self.clock, monotonic=self.clock,
        )
        items = bridge.fetch()
        self.assertEqual([item["message_id"] for item in items], [mentioned])
        self.assertEqual(items[0]["provenance"]["discord_guild"], GUILD)
        self.assertEqual(items[0]["reply_thread"], f"{CHANNEL}:{mentioned}")
        self.assertEqual(
            [path for _method, path in self.calls if path == f"/channels/{CHANNEL}"],
            [f"/channels/{CHANNEL}"],
        )

    def test_a_category_parent_is_not_a_thread(self):
        other = "100000000000000077"
        category = "100000000000000088"
        message_id = "100000000000000123"
        self.channels[other] = {
            "id": other, "type": 0, "guild_id": GUILD, "parent_id": category,
        }
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{USER}"]}), encoding="utf-8")
        bridge = Discord(
            TOKEN, state=self.root / "state", allowlist_path=allow,
            poll_timeout=1, call=self.call,
            connect=Connect(self.clock, [hello(), ready(), message_create(
                message_id, f"<@{BOT}> hi", channel=other, guild=GUILD,
                mentions=[{"id": BOT}],
            )]),
            clock=self.clock, monotonic=self.clock,
        )
        items = bridge.fetch()
        self.assertEqual([item["chat_id"] for item in items], [f"{other}/{USER}"])
        for item in items:
            bridge.ack(item["update_id"])
        bridge.confirm()
        saved = json.loads(
            (self.root / "state" / "discord-markers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved.get("threads") or {}, {})
        self.assertNotIn(other, saved.get("parents") or {})
        self.calls.clear()
        bridge.fetch()
        history = [path for _method, path in self.calls if "/messages" in path]
        self.assertFalse(any(other in path or category in path for path in history))

    def test_catch_up_runs_on_connect_not_every_poll(self):
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{USER}"]}), encoding="utf-8")
        scripts = [[hello(), ready()], [hello(), resumed()]]

        def connect(url, timeout=30):
            return FakeSocket(self.clock, scripts.pop(0), ack=True)

        bridge = Discord(
            TOKEN, state=self.root / "state", allowlist_path=allow,
            poll_timeout=1, call=self.call, connect=connect,
            clock=self.clock, monotonic=self.clock,
        )
        bridge.fetch()
        self.assertTrue(any("/messages?" in path for _method, path in self.calls))
        self.calls.clear()
        bridge.fetch()
        self.assertFalse(any("/messages?" in path for _method, path in self.calls))
        bridge.close()
        self.calls.clear()
        bridge.fetch()
        self.assertTrue(any("/messages?" in path for _method, path in self.calls))


if __name__ == "__main__":
    unittest.main()
