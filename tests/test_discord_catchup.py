"""History read while the bridge was off. confirm is still the only marker write."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from alb.adapters.discord.api import Discord  # noqa: E402
from alb.adapters.discord.rest import HttpError  # noqa: E402
from discord_fakes import (  # noqa: E402
    BOT, CHANNEL, GUILD, MESSAGE, THREAD, TOKEN, USER, Clock, FakeSocket,
    hello, message_create, ready, resumed,
)

PARENT = CHANNEL
LOW = "100000000000000040"
MID = "100000000000000050"
HIGH = "100000000000000060"
USER_MESSAGE = "100000000000000070"
BOT_MESSAGE = "100000000000000071"
OTHER = "100000000000000012"
EPOCH_MS = 1420070400000
PAGE_BASE = 300000000000000000


class Connect:
    def __init__(self, clock, scripts):
        self.clock = clock
        self.scripts = list(scripts)

    def __call__(self, url, timeout=30):
        frames, ack = self.scripts.pop(0)
        return FakeSocket(self.clock, frames, ack=ack)


def row(message_id, channel=CHANNEL, content="hello", bot=False, guild=None):
    author = {"id": USER}
    if bot:
        author["bot"] = True
    body = {
        "id": message_id,
        "channel_id": channel,
        "type": 0,
        "content": content,
        "author": author,
    }
    if guild is not None:
        # History payloads have no guild_id. The channel lookup carries it.
        body["mentions"] = [{"id": BOT}]
        body["content"] = f"<@{BOT}> {content}"
    return body


def page(number, channel=CHANNEL):
    start = PAGE_BASE + number * 100
    ids = [str(start + offset) for offset in range(100)]
    return [row(message_id, channel=channel, guild=GUILD) for message_id in reversed(ids)]


def snowflake_for(unix_seconds):
    ms = int(float(unix_seconds) * 1000) - EPOCH_MS
    if ms < 0:
        ms = 0
    return str(ms << 22)


class CatchUp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        self.clock = Clock()
        self.calls = []
        self.history = {}

    def tearDown(self):
        self.tmp.cleanup()

    def allow(self, *entries):
        path = self.root / "allowlist.json"
        path.write_text(json.dumps({"chats": list(entries)}), encoding="utf-8")
        return path

    def call(self, method, path, token, body=None, timeout=30, opener=None):
        self.calls.append((method, path))
        self.assertNotIn(TOKEN, path)
        if path == "/gateway/bot":
            return {"url": "wss://gateway.example"}
        if method == "GET" and "/messages?" in path:
            key = path.split("?", 1)[0]
            value = self.history.get(path, self.history.get(key, []))
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(path)
            return value
        if method == "GET" and path.startswith("/channels/"):
            looked = self.history.get(path)
            if looked is None:
                return {"id": path.rsplit("/", 1)[-1], "type": 1}
            if isinstance(looked, Exception):
                raise looked
            return looked
        raise AssertionError((method, path))

    def bridge(self, *scripts, allowlist):
        self.connect = Connect(self.clock, scripts)
        return Discord(
            TOKEN, state=self.root / "state", allowlist_path=allowlist,
            poll_timeout=1, call=self.call, connect=self.connect,
            clock=self.clock, monotonic=self.clock,
        )

    def markers(self):
        path = self.root / "state" / "discord-markers.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_markers(self, channels):
        path = self.root / "state" / "discord-markers.json"
        path.write_text(json.dumps({
            "channels": channels, "threads": {}, "parents": {},
        }), encoding="utf-8")

    def ack_all(self, bridge, items):
        for item in items:
            bridge.ack(item["update_id"])

    def test_first_run_reads_the_last_24_hours(self):
        expected = snowflake_for(self.clock.now - 86400)
        self.history[f"/channels/{CHANNEL}/messages"] = []
        bridge = self.bridge(
            ([hello(), ready()], True),
            allowlist=self.allow(f"{CHANNEL}/{USER}"),
        )
        self.assertEqual(bridge.fetch(), [])
        history = [path for _method, path in self.calls if "/messages?" in path]
        self.assertEqual(history, [
            f"/channels/{CHANNEL}/messages?limit=100&after={expected}",
        ])

    def test_pages_forward_and_finishes_next_cycle(self):
        served = {"n": 0}

        def history_for(_path):
            number = served["n"]
            served["n"] += 1
            if number < 10:
                return page(number)
            return [
                row(str(PAGE_BASE + 1000), guild=GUILD),
                row(str(PAGE_BASE + 1001), guild=GUILD),
            ]

        self.history[f"/channels/{CHANNEL}"] = {
            "id": CHANNEL, "type": 0, "guild_id": GUILD,
        }
        self.history[f"/channels/{CHANNEL}/messages"] = history_for
        bridge = self.bridge(
            ([hello(), ready()], True),
            ([hello(), resumed()], True),
            allowlist=self.allow(f"{CHANNEL}/{USER}"),
        )
        first = bridge.fetch()
        self.assertEqual(len(first), 1000)
        self.ack_all(bridge, first)
        bridge.confirm()
        saved = self.markers()
        self.assertNotIn(CHANNEL, saved["channels"])
        self.assertEqual(len(bridge._held), 1000)
        page_max = str(PAGE_BASE + 9 * 100 + 99)
        self.calls.clear()
        second = bridge.fetch()
        history = [path for _method, path in self.calls if "/messages?" in path]
        self.assertTrue(history)
        self.assertIn(f"after={page_max}", history[0])
        self.assertNotIn(f"after={snowflake_for(self.clock.now - 86400)}", history[0])
        self.ack_all(bridge, second)
        bridge.confirm()
        self.assertEqual(self.markers()["channels"][CHANNEL], str(PAGE_BASE + 1001))
        self.assertEqual(bridge._held, [])

    def test_marker_advances_only_after_confirm(self):
        self.history[f"/channels/{CHANNEL}"] = {
            "id": CHANNEL, "type": 0, "guild_id": GUILD,
        }
        self.history[f"/channels/{CHANNEL}/messages"] = [
            row(BOT_MESSAGE, bot=True, guild=GUILD),
            row(USER_MESSAGE, guild=GUILD),
        ]
        bridge = self.bridge(
            ([hello(), ready()], True),
            allowlist=self.allow(f"{CHANNEL}/{USER}"),
        )
        items = bridge.fetch()
        self.assertEqual([item["update_id"] for item in items], [
            f"discord:{CHANNEL}:{USER_MESSAGE}",
        ])
        self.assertIsNone(self.markers())
        self.ack_all(bridge, items)
        self.assertIsNone(self.markers())
        bridge.confirm()
        self.assertEqual(self.markers()["channels"][CHANNEL], BOT_MESSAGE)

    def test_failed_read_freezes_that_marker(self):
        self.write_markers({CHANNEL: LOW})
        state = {"n": 0}

        def history_for(_path):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("discord network")
            return [row(MID)]

        self.history[f"/channels/{CHANNEL}/messages"] = history_for
        bridge = self.bridge(
            ([hello(), ready(), message_create(HIGH, "later")], True),
            ([hello(), resumed()], True),
            allowlist=self.allow(f"{CHANNEL}/{USER}"),
        )
        first = bridge.fetch()
        self.assertEqual([item["message_id"] for item in first], [HIGH])
        self.ack_all(bridge, first)
        bridge.confirm()
        self.assertEqual(self.markers()["channels"][CHANNEL], LOW)
        second = bridge.fetch()
        self.ack_all(bridge, second)
        bridge.confirm()
        self.assertEqual(self.markers()["channels"][CHANNEL], HIGH)

    def test_thread_mention_is_caught_up_under_its_parent(self):
        self.history[f"/channels/{PARENT}/messages"] = []
        self.history[f"/channels/{THREAD}/messages"] = []
        self.history[f"/channels/{THREAD}"] = {"type": 11, "parent_id": PARENT}
        bridge = self.bridge(
            ([hello(), ready(), message_create(
                MESSAGE, f"<@{BOT}> hi", channel=THREAD, guild=GUILD,
                mentions=[{"id": BOT}],
            )], True),
            ([hello(), resumed()], True),
            allowlist=self.allow(f"{PARENT}/{USER}"),
        )
        items = bridge.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["chat_id"], f"{PARENT}/{USER}")
        self.ack_all(bridge, items)
        bridge.confirm()
        saved = self.markers()
        self.assertEqual(saved["threads"][PARENT][THREAD], MESSAGE)
        self.assertEqual(saved["parents"][THREAD], PARENT)
        self.calls.clear()
        bridge.fetch()
        quiet = [path for _method, path in self.calls if "/messages?" in path]
        self.assertEqual(quiet, [])
        bridge.close()
        self.calls.clear()
        bridge.fetch()
        history = [path for _method, path in self.calls if path.startswith(
            f"/channels/{THREAD}/messages?",
        )]
        self.assertEqual(history, [
            f"/channels/{THREAD}/messages?limit=100&after={MESSAGE}",
        ])

    def test_live_and_catchup_copies_share_an_update_id(self):
        self.history[f"/channels/{CHANNEL}/messages"] = [row(MESSAGE)]
        bridge = self.bridge(
            ([hello(), ready(), message_create(MESSAGE, "hello")], True),
            allowlist=self.allow(f"{CHANNEL}/{USER}"),
        )
        items = bridge.fetch()
        self.assertEqual([item["update_id"] for item in items], [
            f"discord:{CHANNEL}:{MESSAGE}",
        ])

    def test_missing_access_skips_without_moving_the_marker(self):
        self.write_markers({OTHER: LOW})
        self.history[f"/channels/{OTHER}/messages"] = HttpError(403)
        self.history[f"/channels/{CHANNEL}"] = {
            "id": CHANNEL, "type": 0, "guild_id": GUILD,
        }
        self.history[f"/channels/{CHANNEL}/messages"] = [row(HIGH, guild=GUILD)]
        bridge = self.bridge(
            ([hello(), ready()], True),
            allowlist=self.allow(f"{OTHER}/{USER}", f"{CHANNEL}/{USER}"),
        )
        items = bridge.fetch()
        self.assertNotIn(OTHER, bridge._pending)
        self.ack_all(bridge, items)
        bridge.confirm()
        saved = self.markers()
        self.assertEqual(saved["channels"][OTHER], LOW)
        self.assertEqual(saved["channels"][CHANNEL], HIGH)


if __name__ == "__main__":
    unittest.main()
