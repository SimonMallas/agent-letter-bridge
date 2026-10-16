"""Messages sent while Slack was disconnected are recovered, once, after confirm."""
import json
import pathlib
import socket
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.errors import TransientFailure  # noqa: E402
from alb.adapters.slack import web  # noqa: E402
from alb.adapters.slack.api import Slack  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"
TEAM = "T0000001"
BOT_USER = "U0000009"
HUMAN = "U0000001"
DM = "D0000001"
CHANNEL = "C0000001"
OLD = "1700000000.000100"
NEW = "1700000000.000200"


class Clock:
    def __init__(self):
        self.now = 1700000000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeSocket:
    def __init__(self, clock, frames):
        self.clock = clock
        self.frames = list(frames)
        self.sent = []
        self.pings = []
        self.timeout = 30
        self.closed = False

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        self.clock.advance(self.timeout)
        raise socket.timeout("quiet")

    def send_text(self, text):
        self.sent.append(text)

    def ping(self, payload=b""):
        self.pings.append(payload)
        # Socket Mode answers a client ping. Without this, a second fetch on a
        # quiet socket looks dead instead of simply empty.
        self.frames.append(("pong", b""))

    def settimeout(self, seconds):
        self.timeout = seconds

    def close(self):
        self.closed = True


def hello():
    return ("text", json.dumps({"type": "hello", "num_connections": 1}))


def live(ts, text="live", channel=DM, user=HUMAN, kind="message", thread_ts=""):
    event = {
        "type": kind, "user": user, "text": text, "ts": ts, "channel": channel,
    }
    if channel.startswith("D"):
        event["channel_type"] = "im"
    if thread_ts:
        event["thread_ts"] = thread_ts
    return ("text", json.dumps({
        "type": "events_api", "envelope_id": "env-" + ts,
        "payload": {"event": event},
    }))


class CatchUp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.allow = pathlib.Path(self.tmp.name) / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [f"{DM}/{HUMAN}"]}))
        self.clock = Clock()
        self.history = {DM: [{"ok": True, "messages": []}]}
        self.replies = {}
        self.history_calls = []
        self.reply_calls = []
        self.fail_history = {}

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, method, token, params=None):
        params = params or {}
        if method == "auth.test":
            return {"ok": True, "team_id": TEAM, "user_id": BOT_USER}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://socket.example/1"}
        if method == "conversations.history":
            self.history_calls.append(dict(params))
            channel = params.get("channel")
            failure = self.fail_history.get(channel)
            if failure is not None:
                self.fail_history[channel] = None
                raise failure
            pages = self.history.get(channel) or [{"ok": True, "messages": []}]
            index = sum(1 for call in self.history_calls if call.get("channel") == channel)
            return pages[min(index, len(pages)) - 1]
        if method == "conversations.replies":
            self.reply_calls.append(dict(params))
            return self.replies.get(params.get("ts"), {"ok": True, "messages": []})
        raise AssertionError(method)

    def bridge(self, frames, allow=True):
        sock = FakeSocket(self.clock, frames)
        bridge = Slack(
            BOT, APP, state=self.state,
            allowlist_path=self.allow if allow else None,
            poll_timeout=25, call=self._call, connect=lambda url, timeout=30: sock,
            clock=self.clock,
        )
        return bridge, sock

    def markers(self):
        path = self.state / "slack-markers.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def test_first_run_reads_the_last_24_hours(self):
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        expected = self.clock() - 86400
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(len(self.history_calls), 1)
        call = self.history_calls[0]
        self.assertEqual(call["channel"], DM)
        self.assertEqual(call["oldest"], expected)
        self.assertIs(call["inclusive"], False)

    def test_messages_sent_while_off_are_queued_before_live_events(self):
        self.history[DM] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "while you were out", "ts": OLD},
        ]}]
        bridge, _sock = self.bridge([hello(), live(NEW, "now")])
        items = bridge.fetch()
        self.assertEqual([item["message_id"] for item in items], [OLD, NEW])

    def test_marker_advances_only_after_confirm(self):
        self.history[DM] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "while you were out", "ts": OLD},
        ]}]
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        items = bridge.fetch()
        self.assertEqual(len(items), 1)
        bridge.ack(items[0]["update_id"])
        self.assertIsNone(self.markers())
        bridge.confirm()
        self.assertEqual(self.markers()["conversations"][DM], OLD)
        path = self.state / "slack-markers.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_marker_skips_past_dropped_bot_messages(self):
        self.history[DM] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "earlier", "ts": OLD},
            {"type": "message", "user": BOT_USER, "bot_id": "B0000001",
             "text": "our reply", "ts": NEW},
        ]}]
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        items = bridge.fetch()
        self.assertEqual([item["message_id"] for item in items], [OLD])
        bridge.ack(items[0]["update_id"])
        bridge.confirm()
        self.assertEqual(self.markers()["conversations"][DM], NEW)

    def test_failed_catchup_freezes_that_conversation_marker(self):
        self.fail_history[DM] = web.HttpError("conversations.history", 503)
        bridge, _sock = self.bridge([hello(), live(NEW, "still live")])
        items = bridge.fetch()
        self.assertEqual([item["message_id"] for item in items], [NEW])
        bridge.ack(items[0]["update_id"])
        bridge.confirm()
        saved = self.markers()
        self.assertTrue(saved is None or DM not in saved.get("conversations", {}))
        self.history[DM] = [{"ok": True, "messages": []}]
        self.assertEqual(bridge.fetch(), [])
        channels = [call["channel"] for call in self.history_calls]
        self.assertGreaterEqual(channels.count(DM), 2)

    def test_pagination_stops_at_max_pages(self):
        pages = []
        for number in range(1, 12):
            pages.append({
                "ok": True,
                "messages": [{
                    "type": "message", "user": HUMAN, "text": f"page {number}",
                    "ts": f"1700000000.{number:06d}",
                }],
                "response_metadata": {"next_cursor": f"cursor-{number}"},
            })
        self.history[DM] = pages
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        bridge.fetch()
        self.assertEqual(len(self.history_calls), Slack.MAX_PAGES)

    def test_known_threads_are_caught_up_with_replies(self):
        path = self.state / "slack-markers.json"
        path.write_text(json.dumps({
            "conversations": {DM: OLD},
            "threads": {DM: {OLD: OLD}},
        }))
        self.replies[OLD] = {"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "a reply", "ts": NEW,
             "thread_ts": OLD},
        ]}
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        items = bridge.fetch()
        self.assertEqual(len(self.reply_calls), 1)
        call = self.reply_calls[0]
        self.assertEqual(call["channel"], DM)
        self.assertEqual(call["ts"], OLD)
        self.assertEqual(call["oldest"], OLD)
        self.assertIs(call["inclusive"], False)
        self.assertEqual([item["text"] for item in items], ["a reply"])

    def test_channel_history_only_mentions_become_items(self):
        self.allow.write_text(json.dumps({"chats": [f"{CHANNEL}/{HUMAN}"]}))
        self.history[CHANNEL] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "talking among ourselves",
             "ts": OLD},
            {"type": "message", "user": HUMAN, "text": f"<@{BOT_USER}> from history",
             "ts": NEW},
        ]}]
        bridge, _sock = self.bridge([hello(), ("pong", b"")])
        items = bridge.fetch()
        self.assertEqual([item["text"] for item in items], ["from history"])
        self.assertEqual(items[0]["chat_id"], f"{CHANNEL}/{HUMAN}")

    def test_live_and_catchup_copies_of_one_message_share_an_update_id(self):
        self.history[DM] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "same", "ts": OLD},
        ]}]
        bridge, _sock = self.bridge([hello(), live(OLD, "same")])
        items = bridge.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["update_id"], f"slack:{TEAM}:{DM}:{OLD}")
