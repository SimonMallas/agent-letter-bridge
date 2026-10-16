"""Slack mail becomes a letter through the poller Telegram already uses."""
import json
import pathlib
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.slack.api import Slack  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.media import store as media_store  # noqa: E402
from alb import msgindex  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"
TEAM = "T0000001"
BOT_USER = "U0000009"
HUMAN = "U0000001"
OTHER = "U0000002"
DM = "D0000001"
TS = "1700000000.000100"
THREAD = "1700000000.000050"
FILE_URL = "https://files.slack.com/files-pri/T0000001-F0000001/pic.png"
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class Clock:
    def __init__(self):
        self.now = 1700000000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeSocket:
    def __init__(self, clock, frames, inbox):
        self.clock = clock
        self.frames = list(frames)
        self.inbox = inbox
        self.sent = []
        self.timeout = 30
        self.closed = False
        self.letter_visible_at_ack = None

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        self.clock.advance(self.timeout)
        raise socket.timeout("quiet")

    def send_text(self, text):
        if "envelope_id" in text and self.letter_visible_at_ack is None:
            self.letter_visible_at_ack = any(self.inbox.glob("*.md"))
        self.sent.append(text)

    def ping(self, payload=b""):
        self.frames.append(("pong", b""))

    def settimeout(self, seconds):
        self.timeout = seconds

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.rung = []

    def deliver(self, surface, line):
        self.rung.append((surface, line))


def hello():
    return ("text", json.dumps({"type": "hello", "num_connections": 1}))


def live(ts, text="hello", user=HUMAN, thread_ts="", files=None, envelope=""):
    event = {
        "type": "message", "user": user, "text": text, "ts": ts, "channel": DM,
        "channel_type": "im",
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    if files:
        event["files"] = files
    return ("text", json.dumps({
        "type": "events_api",
        "envelope_id": envelope or "env-" + ts,
        "payload": {"event": event},
    }))


def image_files():
    return [{
        "id": "F0000001",
        "mimetype": "image/png",
        "url_private_download": FILE_URL,
    }]


class SlackPoller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for name in ("inbox", "processed", "state"):
            (self.root / name).mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [f"{DM}/{HUMAN}"]}))
        self.clock = Clock()
        self.history = {DM: [{"ok": True, "messages": []}]}
        self.sock = None

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, method, token, params=None):
        params = params or {}
        if method == "auth.test":
            return {"ok": True, "team_id": TEAM, "user_id": BOT_USER}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://socket.example/1"}
        if method == "conversations.history":
            pages = self.history.get(params.get("channel")) or [
                {"ok": True, "messages": []}]
            return pages[0]
        if method == "conversations.replies":
            return {"ok": True, "messages": []}
        raise AssertionError(method)

    def bridge(self, frames):
        self.sock = FakeSocket(self.clock, frames, self.root / "inbox")
        return Slack(
            BOT, APP, state=self.root / "state", allowlist_path=self.allow,
            poll_timeout=25, call=self._call,
            connect=lambda url, timeout=30: self.sock, clock=self.clock,
        )

    def cycle(self, bridge):
        return run.run_once(bridge, FakeTransport(), "SURFACE-1", self.root)

    def letter(self):
        found = list((self.root / "inbox").glob("*.md"))
        self.assertEqual(len(found), 1)
        return letters.resolve(self.root / "inbox", found[0].stem)

    def test_slack_dm_becomes_a_letter_with_slack_provenance(self):
        result = self.cycle(self.bridge([hello(), live(TS, "from slack")]))
        self.assertEqual(len(result), 1)
        letter = self.letter()
        self.assertEqual(letter.body, "from slack")
        self.assertEqual(letter.meta["slack_team"], TEAM)
        self.assertEqual(letter.meta["slack_channel"], DM)
        self.assertEqual(letter.meta["slack_ts"], TS)
        self.assertTrue(letter.meta.get("correspondent"))
        self.assertEqual(letter.meta["source_id"], f"slack:{TEAM}:{DM}:{TS}")
        self.assertFalse(any(key.startswith("telegram_") for key in letter.meta))

    def test_letter_is_on_disk_before_the_envelope_is_acked(self):
        self.cycle(self.bridge([hello(), live(TS, "durable")]))
        self.assertIs(self.sock.letter_visible_at_ack, True)
        self.assertIn("env-" + TS, self.sock.sent[0])

    def test_live_retry_after_catchup_is_a_duplicate_not_a_second_letter(self):
        self.history[DM] = [{"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": "same", "ts": TS},
        ]}]
        bridge = self.bridge([hello()])
        first = self.cycle(bridge)
        self.assertEqual(len(first), 1)
        self.sock.frames.append(live(TS, "same"))
        second = self.cycle(bridge)
        self.assertEqual(list(second), [])
        self.assertEqual(second.duplicate, 1)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)

    def test_denied_pair_is_consumed_without_a_letter(self):
        result = self.cycle(self.bridge([
            hello(), live(TS, "nope", user=OTHER, envelope="env-deny"),
        ]))
        self.assertEqual(list(result), [])
        self.assertEqual(result.denied, 1)
        self.assertEqual(list((self.root / "inbox").glob("*.md")), [])
        self.assertTrue(any("env-deny" in text for text in self.sock.sent))

    def test_threaded_reply_to_our_message_links_re_and_thread(self):
        prior = letters.publish(
            self.root / "processed", "earlier",
            letters.envelope(
                "telegram-bridge", "agent", extra={"thread": "root-thread"},
            ),
        )
        msgindex.record(
            self.root / "state", "slack", f"{DM}/{HUMAN}", THREAD, prior,
        )
        self.cycle(self.bridge([
            hello(), live("1700000000.000200", "following up", thread_ts=THREAD),
        ]))
        letter = self.letter()
        self.assertEqual(letter.meta["re"], prior)
        self.assertEqual(letter.meta["thread"], "root-thread")
        self.assertEqual(letter.meta["reply_thread"], THREAD)

    def test_inbound_image_is_stored_and_advertised(self):
        with mock.patch("alb.adapters.slack.web.get_bytes", return_value=PNG_1x1):
            self.cycle(self.bridge([
                hello(), live(TS, "a picture", files=image_files()),
            ]))
        letter = self.letter()
        self.assertEqual(letter.meta.get("media_kind"), "image")
        self.assertEqual(letter.meta.get("media_type"), "image/png")
        self.assertTrue(letter.meta.get("media_asset_id"))
        data = media_store.load_content(
            self.root / "state", letter.meta["id"], letter.meta["media_asset_id"],
        )
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        blob = (self.root / "inbox" / f"{letter.meta['id']}.md").read_text()
        self.assertNotIn("F0000001", blob)
        self.assertNotIn(FILE_URL, blob)

    def test_image_fetch_failure_still_lands_a_letter_with_marker(self):
        def refuse(url, token, max_bytes):
            raise ValueError("file too large")

        with mock.patch("alb.adapters.slack.web.get_bytes", side_effect=refuse):
            self.cycle(self.bridge([
                hello(), live(TS, "a picture", files=image_files()),
            ]))
        letter = self.letter()
        self.assertIn("[photo unavailable]", letter.body)
        self.assertIn("a picture", letter.body)
        self.assertEqual(letter.meta.get("media_status"), "fetch-failed")
        self.assertFalse(letter.meta.get("media_asset_id"))

    def test_media_store_still_refuses_path_shaped_ids(self):
        for bad in ("..", "../secret", "slack:../x:D0000001:1.2", "not-a-slack-id"):
            with self.assertRaises(media_store.MediaError):
                media_store.stage_inbound(self.root / "state", bad, PNG_1x1)
        key = media_store._safe_update_id(f"slack:{TEAM}:{DM}:{TS}")
        self.assertEqual(len(key), 25)
        self.assertTrue(key.startswith("s"))
        self.assertEqual(media_store._safe_update_id("42"), "42")
