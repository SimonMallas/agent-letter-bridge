"""Socket Mode inbound. An envelope is acknowledged only from confirm."""
import json
import os
import pathlib
import socket
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.errors import FetchFailed, TransientFailure  # noqa: E402
from alb.adapters.slack import web  # noqa: E402
from alb.adapters.slack.api import Slack  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"
TS = "1700000000.000100"


class Clock:
    def __init__(self):
        self.now = 1700000000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeSocket:
    """Scripted frames. A timeout moves the shared clock by the socket timeout."""

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

    def settimeout(self, seconds):
        self.timeout = seconds

    def close(self):
        self.closed = True


def hello(connections=1):
    return ("text", json.dumps({
        "type": "hello", "num_connections": connections,
    }))


def dm(envelope, ts=TS, user="U0000001"):
    return ("text", json.dumps({
        "type": "events_api",
        "envelope_id": envelope,
        "payload": {"event": {
            "type": "message",
            "channel_type": "im",
            "user": user,
            "channel": "D0000001",
            "ts": ts,
            "text": "hello",
        }},
    }))


def dropped(envelope):
    return ("text", json.dumps({
        "type": "events_api",
        "envelope_id": envelope,
        "payload": {"event": {"type": "reaction_added", "user": "U0000001"}},
    }))


def acked(sock):
    found = []
    for text in sock.sent:
        if "envelope_id" not in text:
            continue
        found.append(json.loads(text)["envelope_id"])
    return found


class SlackInbound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.clock = Clock()
        self.opens = []
        self.sockets = []
        self.auth_calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, method, token, params=None):
        if method == "auth.test":
            self.auth_calls += 1
            if token != BOT:
                raise AssertionError("auth.test used the wrong token")
            return {"ok": True, "team_id": "T0000001", "user_id": "U0000009"}
        if method == "apps.connections.open":
            if token != APP:
                raise AssertionError("connections.open used the wrong token")
            self.opens.append(method)
            return {"ok": True, "url": f"wss://socket.example/{len(self.opens)}"}
        raise AssertionError(method)

    def _connect(self, url, timeout=30):
        self.assertNotIn(BOT, url)
        self.assertNotIn(APP, url)
        return self.sockets.pop(0)

    def bridge(self, *sockets, **kw):
        self.sockets.extend(sockets)
        return Slack(
            BOT, APP, state=self.state, poll_timeout=25,
            call=self._call, connect=self._connect, clock=self.clock, **kw,
        )

    def test_nothing_is_acked_before_confirm(self):
        sock = FakeSocket(self.clock, [hello(), dm("env-1")])
        bridge = self.bridge(sock)
        items = bridge.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(acked(sock), [])
        bridge.ack(items[0]["update_id"])
        self.assertEqual(acked(sock), [])
        bridge.confirm()
        self.assertEqual(acked(sock), ["env-1"])

    def test_confirm_acks_every_envelope_including_dropped_ones(self):
        sock = FakeSocket(self.clock, [hello(), dm("env-keep"), dropped("env-drop")])
        bridge = self.bridge(sock)
        items = bridge.fetch()
        self.assertEqual([item["update_id"] for item in items], [
            f"slack:T0000001:D0000001:{TS}",
        ])
        bridge.ack(items[0]["update_id"])
        bridge.confirm()
        self.assertEqual(acked(sock), ["env-keep", "env-drop"])

    def test_retried_envelope_for_the_same_message_is_acked_too(self):
        sock = FakeSocket(self.clock, [hello(), dm("env-a"), dm("env-b")])
        bridge = self.bridge(sock)
        items = bridge.fetch()
        self.assertEqual(len(items), 1)
        bridge.ack(items[0]["update_id"])
        bridge.confirm()
        self.assertEqual(acked(sock), ["env-a", "env-b"])

    def test_disconnect_reconnects_with_a_fresh_url(self):
        first = FakeSocket(self.clock, [
            hello(),
            ("text", json.dumps({"type": "disconnect", "reason": "refresh_requested"})),
        ])
        second = FakeSocket(self.clock, [hello(), ("pong", b"")])
        urls = []
        real_connect = self._connect

        def connect(url, timeout=30):
            urls.append(url)
            return real_connect(url, timeout)

        self._connect = connect
        bridge = self.bridge(first, second)
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(urls, [
            "wss://socket.example/1", "wss://socket.example/2",
        ])
        self.assertEqual(self.auth_calls, 1)
        self.assertTrue(first.closed)

    def test_silent_socket_raises_transient_and_reconnects(self):
        silent = FakeSocket(self.clock, [hello()])
        nxt = FakeSocket(self.clock, [hello(), ("pong", b"")])
        bridge = self.bridge(silent, nxt)
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertIn("network:", str(caught.exception))
        self.assertIn("silent", str(caught.exception))
        self.assertNotIn(BOT, str(caught.exception))
        self.assertNotIn(APP, str(caught.exception))
        self.assertTrue(silent.pings)
        self.assertTrue(silent.closed)
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(len(self.opens), 2)
        self.assertEqual(nxt.pings, [])

    def test_server_ping_counts_as_liveness(self):
        sock = FakeSocket(self.clock, [hello(), ("pong", b"")])
        bridge = self.bridge(sock)
        self.assertEqual(bridge.fetch(), [])
        self.assertEqual(sock.pings, [])

    def test_auth_failure_is_fetch_failed_not_transient(self):
        def call(method, token, params=None):
            return {"ok": False, "error": "invalid_auth"}

        bridge = Slack(BOT, APP, call=call, connect=self._connect, clock=self.clock)
        with self.assertRaises(FetchFailed) as caught:
            bridge.fetch()
        self.assertNotIsInstance(caught.exception, TransientFailure)
        self.assertIn("invalid_auth", str(caught.exception))
        self.assertNotIn(BOT, str(caught.exception))
        self.assertNotIn(APP, str(caught.exception))
        self.assertEqual(self.sockets, [])

    def test_rate_limited_open_is_transient_with_retry_after(self):
        def call(method, token, params=None):
            if method == "auth.test":
                return {"ok": True, "team_id": "T0000001", "user_id": "U0000009"}
            raise web.HttpError("apps.connections.open", 429, 17)

        bridge = Slack(BOT, APP, call=call, connect=self._connect, clock=self.clock)
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertEqual(caught.exception.retry_after, 17)
        self.assertIn("429", str(caught.exception))
        self.assertNotIn(BOT, str(caught.exception))

    def test_hello_connection_count_is_recorded(self):
        sock = FakeSocket(self.clock, [hello(2), ("pong", b"")])
        bridge = self.bridge(sock)
        self.assertEqual(bridge.fetch(), [])
        path = self.state / "slack-connection.json"
        self.assertEqual(json.loads(path.read_text())["connections"], 2)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_repr_and_errors_never_contain_tokens(self):
        bridge = Slack(BOT, APP, call=self._call, clock=self.clock)
        self.assertEqual(repr(bridge), "<Slack app>")
        self.assertNotIn(BOT, repr(bridge))
        self.assertNotIn(APP, repr(bridge))
        text = "".join(os.listdir(self.tmp.name))
        self.assertNotIn(BOT, text)
        self.assertNotIn(APP, text)
