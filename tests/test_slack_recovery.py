"""Catch-up never moves a marker past mail that has not landed.

Both cases here were reproduced by an adversarial review through the real
cycle (run.run_once), not the adapter alone:

  - a transient failure part-way through a batch (a photo download waiting
    out a 429) dropped the rest of the batch from memory, and a later idle
    cycle confirmed a bot message's mark past two human messages that never
    became letters;
  - a history longer than the page cap was published newest-first, the
    marker moved to the newest message, and the unread older tail was
    excluded on every reconnect after.
"""
import json
import pathlib
import socket
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.errors import TransientFailure  # noqa: E402
from alb.adapters.slack.api import Slack  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"
TEAM = "T0000001"
BOT_USER = "U0000009"
HUMAN = "U0000001"
DM = "D0000001"
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde" + b"\x00" * 64)


def ts(n):
    return f"1700000000.{n:06d}"


class Clock:
    def __init__(self):
        self.now = 1700000100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class QuietSocket:
    """Says hello, then nothing but answered pings."""

    def __init__(self, clock):
        self.clock = clock
        self.frames = [("text", json.dumps({"type": "hello", "num_connections": 1}))]
        self.sent = []
        self.timeout = 30

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        self.clock.advance(self.timeout)
        raise socket.timeout("quiet")

    def send_text(self, text):
        self.sent.append(text)

    def ping(self, payload=b""):
        self.frames.append(("pong", b""))

    def settimeout(self, seconds):
        self.timeout = seconds

    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "state").mkdir()
        (self.root / "inbox").mkdir()
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": [f"{DM}/{HUMAN}"]}))
        self.clock = Clock()
        self.history_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def history(self, params):
        raise NotImplementedError

    def _call(self, method, token, params=None):
        params = dict(params or {})
        if method == "auth.test":
            return {"ok": True, "team_id": TEAM, "user_id": BOT_USER}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://socket.example/1"}
        if method == "conversations.history":
            self.history_calls.append(params)
            return self.history(params)
        if method == "conversations.replies":
            return {"ok": True, "messages": []}
        raise AssertionError(method)

    def bridge(self):
        return Slack(BOT, APP, state=self.root / "state",
                     allowlist_path=self.root / "allowlist.json",
                     poll_timeout=5, call=self._call,
                     connect=lambda url, timeout=30: QuietSocket(self.clock),
                     clock=self.clock)

    def cycle(self, bridge):
        return run.run_once(bridge, None, "", self.root,
                            sender="slack-bridge", recipient="agent")

    def letters(self):
        found = {}
        for path in (self.root / "inbox").glob("*.md"):
            letter = store.resolve(path.parent, path.stem)
            found[letter.meta["slack_ts"]] = letter.body
        return found

    def marker(self):
        path = self.root / "state" / "slack-markers.json"
        if not path.exists():
            return None
        return json.loads(path.read_text()).get("conversations", {}).get(DM)


class TransientMidBatch(Base):
    def history(self, params):
        return {"ok": True, "messages": [
            {"type": "message", "user": BOT_USER, "bot_id": "B1",
             "text": "bot", "ts": ts(300)},
            {"type": "message", "user": HUMAN, "text": "plain text",
             "ts": ts(200)},
            {"type": "message", "user": HUMAN, "text": "", "ts": ts(100),
             "subtype": "file_share",
             "files": [{"id": "F1", "mimetype": "image/png",
                        "url_private_download": "https://files.slack.com/f"}]},
        ]}

    def flaky_photo(self, bridge, failures):
        state = {"left": failures}

        def download(file_id):
            if state["left"]:
                state["left"] -= 1
                raise TransientFailure("slack deferred: HTTP 429", 3)
            return PNG

        bridge.download_photo = download

    def test_a_transient_mid_batch_never_lets_a_later_cycle_skip_unpublished_mail(self):
        bridge = self.bridge()
        self.flaky_photo(bridge, failures=2)
        with self.assertRaises(TransientFailure):
            self.cycle(bridge)
        self.assertEqual(self.letters(), {})
        # Still waiting out the 429: nothing confirmed, nothing moved.
        with self.assertRaises(TransientFailure):
            self.cycle(bridge)
        self.assertIsNone(self.marker())
        self.assertEqual(self.letters(), {})
        # The wait is over: both human messages land, then the marker moves.
        self.cycle(bridge)
        self.assertEqual(set(self.letters()), {ts(100), ts(200)})
        self.assertEqual(self.marker(), ts(300))

    def test_an_idle_cycle_after_a_failed_one_cannot_confirm_past_a_hole(self):
        bridge = self.bridge()
        self.flaky_photo(bridge, failures=1)
        with self.assertRaises(TransientFailure):
            self.cycle(bridge)
        self.assertIsNone(self.marker())
        # The very next cycle carries the unconfirmed batch again rather than
        # confirming the bot message's mark on an empty fetch.
        self.cycle(bridge)
        self.assertEqual(set(self.letters()), {ts(100), ts(200)})
        self.assertEqual(self.marker(), ts(300))

    def test_a_restart_after_the_failure_still_recovers_both(self):
        bridge = self.bridge()
        self.flaky_photo(bridge, failures=1)
        with self.assertRaises(TransientFailure):
            self.cycle(bridge)
        bridge.close()
        fresh = self.bridge()
        self.flaky_photo(fresh, failures=0)
        self.cycle(fresh)
        self.assertEqual(set(self.letters()), {ts(100), ts(200)})
        self.assertEqual(self.marker(), ts(300))


class LongHistory(Base):
    """Eleven pages of one human message each, newest first, as Slack pages
    conversations.history. The cap is ten pages per conversation per cycle."""

    PAGES = 11

    def history(self, params):
        if params.get("cursor") == "stale":
            return {"ok": False, "error": "invalid_cursor"}
        cursor = int(params.get("cursor") or 0)
        row = self.PAGES - cursor
        body = {"ok": True, "messages": [
            {"type": "message", "user": HUMAN, "text": f"row-{row}",
             "ts": ts(row)}]}
        if cursor + 1 < self.PAGES:
            body["response_metadata"] = {"next_cursor": str(cursor + 1)}
        return body

    def test_a_history_longer_than_the_cap_is_finished_next_cycle(self):
        bridge = self.bridge()
        self.cycle(bridge)
        self.assertEqual(len(self.letters()), 10)
        # Not finished, so the marker has not moved past the unread tail.
        self.assertIsNone(self.marker())
        self.cycle(bridge)
        self.assertEqual(len(self.letters()), 11)
        self.assertIn(ts(1), self.letters())
        self.assertEqual(self.marker(), ts(11))
        # Continued from where it stopped, with the window it started with.
        resumed = self.history_calls[10]
        self.assertEqual(resumed["cursor"], "10")
        self.assertEqual(resumed["oldest"], self.history_calls[0]["oldest"])

    def test_nothing_is_lost_across_a_reconnect_mid_backlog(self):
        bridge = self.bridge()
        self.cycle(bridge)
        bridge.close()
        fresh = self.bridge()
        self.cycle(fresh)
        self.cycle(fresh)
        self.assertEqual(len(self.letters()), 11)
        self.assertEqual(self.marker(), ts(11))

    def test_an_expired_cursor_starts_the_read_again_from_the_frozen_marker(self):
        bridge = self.bridge()
        self.cycle(bridge)
        key = next(iter(bridge._continuations))
        oldest, _cursor = bridge._continuations[key]
        bridge._continuations[key] = (oldest, "stale")
        before = len(self.history_calls)
        self.cycle(bridge)
        self.assertEqual(self.history_calls[before]["cursor"], "stale")
        self.assertIsNone(self.marker())
        for _ in range(3):
            self.cycle(bridge)
        restarted = [c for c in self.history_calls[before + 1:] if "cursor" not in c]
        self.assertTrue(restarted, "the read starts again without the cursor")
        self.assertEqual(len(self.letters()), 11)
        self.assertEqual(self.marker(), ts(11))


if __name__ == "__main__":
    unittest.main()
