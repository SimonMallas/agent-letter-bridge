"""Gateway session, heartbeats, and the rule that confirm owns the marker."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from alb.adapters.discord.api import Discord  # noqa: E402
from alb.adapters.errors import FetchFailed, TransientFailure  # noqa: E402
from discord_fakes import (  # noqa: E402
    CHANNEL, MESSAGE, TOKEN, Clock, FakeSocket, close_frame, hello,
    message_create, ready, resumed, text_frame,
)

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
PRIVILEGED = (1 << 1) | (1 << 8) | (1 << 15)


class Connect:
    def __init__(self, clock, scripts):
        self.clock = clock
        self.scripts = list(scripts)
        self.sockets = []
        self.urls = []

    def __call__(self, url, timeout=30):
        self.urls.append(url)
        frames, ack = self.scripts.pop(0)
        sock = FakeSocket(self.clock, frames, ack=ack)
        self.sockets.append(sock)
        return sock


class Gateway(unittest.TestCase):
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
        if method == "GET" and path.startswith("/channels/"):
            return []
        raise AssertionError((method, path))

    def bridge(self, *scripts, poll_timeout=25):
        self.connect = Connect(self.clock, scripts)
        return Discord(
            TOKEN, state=self.root / "state", poll_timeout=poll_timeout,
            call=self.call, connect=self.connect, clock=self.clock,
            monotonic=self.clock,
        )

    def sent(self, index=0):
        return [item["payload"] for item in self.connect.sockets[index].sent]

    def ops(self, index=0):
        return [item.get("op") for item in self.sent(index)]

    def test_identify_sends_intents_4608_and_no_privileged_bits(self):
        bridge = self.bridge(([hello(), ready()], True))
        self.assertEqual(bridge.fetch(), [])
        identify = next(item for item in self.sent() if item.get("op") == 2)
        self.assertEqual(identify["d"]["intents"], 4608)
        self.assertEqual(identify["d"]["intents"] & PRIVILEGED, 0)
        self.assertEqual(self.calls, [("GET", "/gateway/bot")])
        self.assertIn("v=10", self.connect.urls[0])
        self.assertIn("encoding=json", self.connect.urls[0])
        session = json.loads(
            (self.root / "state" / "discord-session.json").read_text()
        )
        self.assertEqual(session["session_id"], "session-1")
        self.assertEqual(
            (self.root / "state" / "discord-session.json").stat().st_mode & 0o777,
            0o600,
        )
        self.assertNotIn(TOKEN, json.dumps(session))

    def test_resume_is_used_when_a_fresh_session_exists(self):
        path = self.root / "state" / "discord-session.json"
        path.write_text(json.dumps({
            "session_id": "session-1",
            "resume_gateway_url": "wss://resume.example",
            "seq": 4,
            "at": self.clock.now - 10,
        }))
        bridge = self.bridge(([hello(), resumed()], True))
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(self.calls, [])
        self.assertTrue(self.connect.urls[0].startswith("wss://resume.example"))
        resume = next(item for item in self.sent() if item.get("op") == 6)
        self.assertEqual(resume["d"]["session_id"], "session-1")
        self.assertEqual(resume["d"]["seq"], 4)
        self.assertNotIn(2, self.ops())

        stale = self.clock.now
        path.write_text(json.dumps({
            "session_id": "session-1",
            "resume_gateway_url": "wss://resume.example",
            "seq": 4,
            "at": stale - Discord.SESSION_FRESH,
        }))
        bridge.close()
        bridge._ws = None
        self.connect.scripts.append(([hello(), ready()], True))
        bridge.fetch()
        self.assertIn(("GET", "/gateway/bot"), self.calls)
        self.assertIn(2, self.ops(1))

    def test_missing_heartbeat_ack_closes_and_resumes(self):
        bridge = self.bridge(
            ([hello(5000), ready()], False),
            ([hello(5000), resumed()], True),
            poll_timeout=20,
        )
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertIn("heartbeat not acknowledged", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(self.connect.sockets[0].closed, 4000)
        self.assertNotEqual(self.connect.sockets[0].closed, 1000)
        bridge.fetch()
        self.assertTrue(self.connect.urls[1].startswith("wss://gateway.example"))
        self.assertIn(6, self.ops(1))

    def test_heartbeat_is_sent_during_a_slow_cycle(self):
        bridge = self.bridge(([hello(5000), ready()], True), poll_timeout=12)
        bridge.fetch()
        beats = [item for item in self.sent() if item.get("op") == 1]
        self.assertGreaterEqual(len(beats), 2)
        self.assertIsInstance(beats[-1]["d"], int)

        before = len(beats)
        self.clock.advance(5)
        bridge._file_urls["100000000000000031"] = (
            "https://cdn.discordapp.com/attachments/100000000000000001/pic.png"
        )

        def get_bytes(url, max_bytes, opener=None):
            self.assertGreater(
                len([item for item in self.sent() if item.get("op") == 1]),
                before,
            )
            self.assertNotIn(TOKEN, url)
            return PNG

        original = __import__(
            "alb.adapters.discord.rest", fromlist=["get_bytes"],
        ).get_bytes
        from alb.adapters.discord import rest
        rest.get_bytes = get_bytes
        try:
            self.assertEqual(bridge.download_photo("100000000000000031"), PNG)
        finally:
            rest.get_bytes = original

    def test_fatal_close_codes_are_fetch_failed(self):
        cases = (
            (4004, "authentication failed"),
            (4010, "invalid shard"),
            (4011, "sharding required"),
            (4012, "invalid API version"),
            (4013, "invalid intent"),
            (4014, "disallowed intent"),
        )
        for code, meaning in cases:
            bridge = self.bridge(([hello(), ready(), close_frame(code)], True))
            with self.assertRaises(FetchFailed) as caught:
                bridge.fetch()
            self.assertIn(meaning, str(caught.exception))
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIsInstance(caught.exception, TransientFailure)
        bridge = self.bridge(([hello(), ready(), close_frame(4001)], True))
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertNotIsInstance(caught.exception, FetchFailed)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_invalid_session_not_resumable_reidentifies(self):
        path = self.root / "state" / "discord-session.json"
        path.write_text(json.dumps({
            "session_id": "session-1",
            "resume_gateway_url": "wss://resume.example",
            "seq": 4,
            "at": self.clock.now - 10,
        }))
        bridge = self.bridge(
            ([hello(), text_op9()], True),
            ([hello(), ready(session="session-2")], True),
        )
        bridge.fetch()
        self.assertIn(6, self.ops(0))
        self.assertIn(2, self.ops(1))
        identify = next(item for item in self.sent(1) if item.get("op") == 2)
        resume = next(
            item for item in self.connect.sockets[0].sent
            if item["payload"].get("op") == 6
        )
        gap = self.connect.sockets[1].sent[0]["at"] - resume["at"]
        self.assertGreaterEqual(gap, 1)
        self.assertLessEqual(gap, 5)
        self.assertEqual(identify["d"]["intents"], 4608)
        session = json.loads(path.read_text())
        self.assertEqual(session["session_id"], "session-2")

    def test_idle_fetch_without_ack_is_not_liveness(self):
        bridge = self.bridge(([hello(120000), ready()], False))
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertIn("heartbeat not acknowledged", str(caught.exception))
        self.assertIsNone(self.connect.sockets[0].closed)

    def test_transient_mid_batch_requeues_the_batch(self):
        first_id = MESSAGE
        second_id = "100000000000000022"
        bridge = self.bridge(([
            hello(), ready(),
            message_create(first_id, "one"),
            message_create(second_id, "two"),
        ], True))
        first = bridge.fetch()
        self.assertEqual(
            [item["update_id"] for item in first],
            [f"discord:{CHANNEL}:{first_id}", f"discord:{CHANNEL}:{second_id}"],
        )
        second = bridge.fetch()
        self.assertEqual(
            [item["update_id"] for item in second],
            [item["update_id"] for item in first],
        )
        bridge.ack(first[0]["update_id"])
        bridge.ack(first[1]["update_id"])
        bridge.confirm()
        third = bridge.fetch()
        self.assertEqual(third, [])

    def test_repr_and_errors_never_contain_the_token(self):
        bridge = self.bridge(([hello(), ready(), close_frame(4004)], True))
        self.assertEqual(repr(bridge), "<Discord bot>")
        self.assertNotIn(TOKEN, repr(bridge))
        with self.assertRaises(FetchFailed) as caught:
            bridge.fetch()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(TOKEN, repr(caught.exception))

    def test_confirm_writes_markers_and_fetch_does_not(self):
        bridge = self.bridge(([
            hello(), ready(), message_create(MESSAGE, "hello"),
        ], True))
        items = bridge.fetch()
        markers = self.root / "state" / "discord-markers.json"
        self.assertFalse(markers.exists())
        bridge.ack(items[0]["update_id"])
        self.assertFalse(markers.exists())
        bridge.confirm()
        saved = json.loads(markers.read_text())
        self.assertEqual(saved["channels"][CHANNEL], MESSAGE)
        self.assertEqual(markers.stat().st_mode & 0o777, 0o600)


def text_op9():
    return text_frame({"op": 9, "d": False})


if __name__ == "__main__":
    unittest.main()
