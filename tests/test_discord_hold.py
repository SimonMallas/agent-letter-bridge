"""Codex HOLD on the merged Discord candidate. Loss, budget, and health."""
import json
import pathlib
import sys
import unittest
import urllib.parse
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from alb.adapters.discord.api import Discord, snowflake_at  # noqa: E402
from alb.adapters.discord.rest import HttpError  # noqa: E402
from alb.adapters.errors import TransientFailure  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store  # noqa: E402
from discord_fakes import (  # noqa: E402
    BOT, CHANNEL, GUILD, MESSAGE, THREAD, TOKEN, USER, Clock, FakeSocket,
    hello, message_create, ready, resumed, text_frame,
)
from test_discord_catchup import OTHER, CatchUp  # noqa: E402
from test_discord_gateway import PNG  # noqa: E402


def _bodies(root):
    found = []
    for path in (root / "inbox").glob("*.md"):
        found.append(store.resolve(path.parent, path.stem).body.strip())
    return found


def _after(path):
    return int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["after"][0])


class Hold(unittest.TestCase):
    def _box(self):
        box = CatchUp()
        box.setUp()
        self.addCleanup(box.tearDown)
        run.prepare_root(box.root)
        return box

    def test_transient_thread_lookup_keeps_the_message(self):
        for failure in (
            OSError("temporary network failure"),
            HttpError(429, retry_after=1),
        ):
            with self.subTest(failure=type(failure).__name__):
                self._lost_thread(failure)

    def _lost_thread(self, failure):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        message_id = str(int(snowflake_at(box.clock() - 10)) + 1)
        newer = str(int(message_id) + 1)
        lookups = {"n": 0}
        rows = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            if path == f"/channels/{THREAD}":
                lookups["n"] += 1
                if lookups["n"] == 1:
                    raise failure
                return {
                    "id": THREAD, "type": 11, "parent_id": CHANNEL,
                    "guild_id": GUILD,
                }
            if path == f"/channels/{CHANNEL}":
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD}
            if f"/channels/{THREAD}/messages?" in path:
                return [row for row in rows if int(row["id"]) > _after(path)]
            return box.call(method, path, token, body)

        first = message_create(
            message_id, f"<@{BOT}> first", channel=THREAD, guild=GUILD,
            mentions=[{"id": BOT}],
        )
        second = message_create(
            newer, f"<@{BOT}> second", channel=THREAD, guild=GUILD,
            mentions=[{"id": BOT}],
        )
        rows[:] = [json.loads(frame[1])["d"] for frame in (first, second)]
        sockets = [[FakeSocket(box.clock, [hello(), ready(), first])]]

        def connect(url, timeout=30):
            return sockets[0].pop(0)

        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=connect,
            clock=box.clock, monotonic=box.clock,
        )
        first_cycle = run.run_once(bridge, None, "", box.root)
        saved = box.markers()
        self.assertEqual(len(first_cycle), 0)
        self.assertEqual(first_cycle.denied, 0)
        self.assertEqual(saved["unresolved"][THREAD], str(int(message_id) - 1))
        self.assertNotIn(THREAD, saved.get("channels") or {})

        bridge._ws.frames.append(second)
        run.run_once(bridge, None, "", box.root)
        sockets[0].append(FakeSocket(box.clock, [hello(), resumed()]))
        bridge.close()
        run.run_once(bridge, None, "", box.root)
        self.assertEqual(sorted(_bodies(box.root)), ["first", "second"])

    def test_a_429_holds_a_channel_not_yet_read(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}", f"{OTHER}/{USER}")
        base = int(snowflake_at(box.clock() - 10))
        old, new = str(base + 1), str(base + 2)
        left = {"n": 1}

        def call(method, path, token, body=None, timeout=30, opener=None):
            if f"/channels/{CHANNEL}/messages?" in path:
                if left["n"]:
                    left["n"] -= 1
                    raise HttpError(429, retry_after=3)
                return []
            if f"/channels/{OTHER}/messages?" in path:
                row = {
                    "id": old, "channel_id": OTHER, "type": 0,
                    "content": "missed history", "author": {"id": USER},
                }
                return [row] if int(old) > _after(path) else []
            return box.call(method, path, token, body)

        sock = FakeSocket(box.clock, [
            hello(), ready(), message_create(new, "live newer", channel=OTHER),
        ])
        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=lambda url, timeout=30: sock,
            clock=box.clock, monotonic=box.clock,
        )
        first = run.run_once(bridge, None, "", box.root)
        saved = box.markers() or {}
        self.assertEqual(len(first), 1)
        self.assertNotEqual((saved.get("channels") or {}).get(OTHER), new)
        run.run_once(bridge, None, "", box.root)
        self.assertEqual(sorted(_bodies(box.root)), ["live newer", "missed history"])

    def test_a_429_holds_a_thread_not_yet_read(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        base = int(snowflake_at(box.clock() - 10))
        old, new = str(base + 1), str(base + 2)
        floor = str(base)
        (box.root / "state" / "discord-markers.json").write_text(json.dumps({
            "channels": {},
            "threads": {CHANNEL: {THREAD: floor}},
            "parents": {THREAD: CHANNEL},
        }), encoding="utf-8")
        left = {"n": 1}
        missed = {
            "id": old, "channel_id": THREAD, "type": 0, "guild_id": GUILD,
            "content": f"<@{BOT}> missed thread", "author": {"id": USER},
            "mentions": [{"id": BOT}],
        }

        def call(method, path, token, body=None, timeout=30, opener=None):
            if path == f"/channels/{CHANNEL}":
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD}
            if path == f"/channels/{THREAD}":
                return {
                    "id": THREAD, "type": 11, "parent_id": CHANNEL,
                    "guild_id": GUILD,
                }
            if f"/channels/{CHANNEL}/messages?" in path:
                if left["n"]:
                    left["n"] -= 1
                    raise HttpError(429, retry_after=3)
                return []
            if f"/channels/{THREAD}/messages?" in path:
                return [missed] if int(old) > _after(path) else []
            return box.call(method, path, token, body)

        live = message_create(
            new, f"<@{BOT}> live thread", channel=THREAD, guild=GUILD,
            mentions=[{"id": BOT}],
        )
        sock = FakeSocket(box.clock, [hello(), ready(), live])
        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=lambda url, timeout=30: sock,
            clock=box.clock, monotonic=box.clock,
        )
        run.run_once(bridge, None, "", box.root)
        saved = box.markers()
        self.assertEqual(saved["threads"][CHANNEL][THREAD], floor)
        run.run_once(bridge, None, "", box.root)
        self.assertIn("missed thread", _bodies(box.root))

    def test_reconnect_before_ready_stops_after_three_attempts(self):
        for frame in (
            {"op": 7, "d": None},
            {"op": 9, "d": False},
            {"op": 9, "d": True},
        ):
            with self.subTest(op=frame["op"], resumable=frame["d"]):
                self._storm(frame)

    def _storm(self, frame):
        clock = Clock()
        sockets = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            self.assertNotIn(TOKEN, path)
            return {
                "url": "wss://gateway.example",
                "session_start_limit": {
                    "remaining": 5, "reset_after": 1000, "max_concurrency": 1,
                },
            }

        def connect(url, timeout=30):
            if len(sockets) >= 6:
                raise OSError("fixture stops after six attempts")
            sock = FakeSocket(clock, [hello(), text_frame(frame)], ack=True)
            sockets.append(sock)
            return sock

        bridge = Discord(
            TOKEN, call=call, connect=connect, clock=clock, monotonic=clock,
        )
        with self.assertRaises(TransientFailure):
            bridge.fetch()
        identifies = [
            item for sock in sockets for item in sock.sent
            if item["payload"].get("op") == 2
        ]
        self.assertEqual(len(sockets), 3)
        self.assertEqual(len(identifies), 3)

    def test_a_spent_session_budget_does_not_identify(self):
        opened = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            self.assertNotIn(TOKEN, path)
            return {
                "url": "wss://gateway.example",
                "session_start_limit": {
                    "total": 1000, "remaining": 0, "reset_after": 86400000,
                    "max_concurrency": 1,
                },
            }

        def connect(url, timeout=30):
            opened.append(url)
            raise AssertionError("identify opened a socket")

        bridge = Discord(TOKEN, call=call, connect=connect, clock=Clock(), monotonic=Clock())
        with self.assertRaises(TransientFailure) as caught:
            bridge.fetch()
        self.assertEqual(caught.exception.retry_after, 86400.0)
        self.assertEqual(opened, [])
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_slow_publish_is_not_a_live_cycle_and_the_next_fetch_resumes(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        sockets = []

        def connect(url, timeout=30):
            if not sockets:
                frames = [hello(10000), ready(), message_create(MESSAGE, "durable")]
            else:
                frames = [hello(10000), resumed()]
            sock = FakeSocket(box.clock, frames, ack=True)
            sockets.append(sock)
            return sock

        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=box.call, connect=connect,
            clock=box.clock, monotonic=box.clock,
        )

        original = store.publish_once

        def slow(*args, **kwargs):
            box.clock.advance(35)
            return original(*args, **kwargs)

        with patch.object(store, "publish_once", slow):
            result = run.run_once(bridge, None, "", box.root)
        self.assertEqual(len(result), 1)
        health = json.loads((box.root / "state" / "health.json").read_text())
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["reason"], "network")
        beats = [
            item for item in sockets[0].sent if item["payload"].get("op") == 1
        ]
        self.assertEqual(len(beats), 1)
        run.run_once(bridge, None, "", box.root)
        self.assertEqual(sockets[0].closed, 4000)
        self.assertIn(6, [item["payload"].get("op") for item in sockets[1].sent])

    def test_identifies_keep_five_seconds_between_them(self):
        for frame in (
            {"op": 7, "d": None},
            {"op": 9, "d": False},
            {"op": 9, "d": True},
        ):
            with self.subTest(op=frame["op"], resumable=frame["d"]):
                times = self._identify_times(frame)
                gaps = [later - earlier for earlier, later in zip(times, times[1:])]
                self.assertEqual(len(times), 6)
                self.assertTrue(all(gap >= 5 for gap in gaps), gaps)

    def _identify_times(self, frame):
        clock = Clock()
        sockets = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            self.assertNotIn(TOKEN, path)
            return {
                "url": "wss://gateway.example",
                "session_start_limit": {
                    "remaining": 5, "reset_after": 1000, "max_concurrency": 1,
                },
            }

        def connect(url, timeout=30):
            sock = FakeSocket(clock, [hello(), text_frame(frame)], ack=True)
            sockets.append(sock)
            return sock

        bridge = Discord(
            TOKEN, call=call, connect=connect, clock=clock, monotonic=clock,
        )
        for _ in range(2):
            with self.assertRaises(TransientFailure):
                bridge.fetch()
            clock.advance(10)
        return [
            item["at"] for sock in sockets for item in sock.sent
            if item["payload"].get("op") == 2
        ]

    def test_resume_does_not_wait_on_the_identify_window(self):
        box = self._box()
        clock = box.clock
        sockets = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            return {
                "url": "wss://gateway.example",
                "session_start_limit": {
                    "remaining": 5, "reset_after": 1000, "max_concurrency": 1,
                },
            }

        def connect(url, timeout=30):
            frames = [hello(), ready()] if not sockets else [hello(), resumed()]
            sock = FakeSocket(clock, frames, ack=True)
            sockets.append(sock)
            return sock

        bridge = Discord(
            TOKEN, state=box.root / "state", poll_timeout=1,
            call=call, connect=connect, clock=clock, monotonic=clock,
        )
        bridge.fetch()
        identify_at = next(
            item["at"] for item in sockets[0].sent
            if item["payload"].get("op") == 2
        )
        bridge.close()
        bridge.fetch()
        resume_at = next(
            item["at"] for item in sockets[1].sent
            if item["payload"].get("op") == 6
        )
        self.assertLess(resume_at - identify_at, 5)
        self.assertNotIn(
            2, [item["payload"].get("op") for item in sockets[1].sent],
        )

    def test_a_late_download_heartbeat_stays_lapsed(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        sockets = []
        file_id = "100000000000000031"

        def connect(url, timeout=30):
            if not sockets:
                frames = [hello(10000), ready(), message_create(
                    MESSAGE, "photo", attachments=[{
                        "id": file_id,
                        "content_type": "image/png",
                        "url": (
                            "https://cdn.discordapp.com/attachments/"
                            "100000000000000001/pic.png"
                        ),
                    }],
                )]
            else:
                frames = [hello(10000), resumed()]
            sock = FakeSocket(box.clock, frames, ack=True)
            sockets.append(sock)
            return sock

        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=box.call, connect=connect,
            clock=box.clock, monotonic=box.clock,
        )

        def get_bytes(url, max_bytes, opener=None):
            box.clock.advance(35)
            return PNG

        with patch("alb.adapters.discord.rest.get_bytes", get_bytes):
            result = run.run_once(bridge, None, "", box.root)
        self.assertEqual(len(result), 1)
        beats = [
            item["at"] for item in sockets[0].sent
            if item["payload"].get("op") == 1
        ]
        self.assertEqual(len(beats), 2)
        self.assertGreater(beats[1], beats[0] + 20)
        self.assertTrue(bridge.heartbeat_expired())
        health = json.loads((box.root / "state" / "health.json").read_text())
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["reason"], "network")
        run.run_once(bridge, None, "", box.root)
        self.assertEqual(len(sockets), 2)
        self.assertEqual(sockets[0].closed, 4000)
        self.assertIn(6, [item["payload"].get("op") for item in sockets[1].sent])
        self.assertFalse(bridge.heartbeat_expired())

    def test_a_heartbeat_one_interval_late_is_not_lapsed(self):
        clock = Clock()
        sock = FakeSocket(clock, [hello(10000), ready()], ack=True)

        def call(method, path, token, body=None, timeout=30, opener=None):
            return {"url": "wss://gateway.example"}

        bridge = Discord(
            TOKEN, poll_timeout=1, call=call,
            connect=lambda url, timeout=30: sock,
            clock=clock, monotonic=clock,
        )
        bridge.fetch()
        first = next(
            item["at"] for item in sock.sent if item["payload"].get("op") == 1
        )
        self.assertLess(clock(), first + 20)
        clock.advance((first + 20) - clock())
        bridge._file_urls["100000000000000031"] = (
            "https://cdn.discordapp.com/attachments/100000000000000001/pic.png"
        )

        def get_bytes(url, max_bytes, opener=None):
            return PNG

        with patch("alb.adapters.discord.rest.get_bytes", get_bytes):
            self.assertEqual(bridge.download_photo("100000000000000031"), PNG)
        beats = [item["at"] for item in sock.sent if item["payload"].get("op") == 1]
        self.assertEqual(beats[1] - beats[0], 20)
        self.assertFalse(bridge.heartbeat_expired())

    def test_a_thread_lookup_waits_out_retry_after(self):
        lookups, health, saved = self._persistent_lookup(
            HttpError(429, retry_after=300),
        )
        self.assertEqual(len(lookups), 1)
        self.assertEqual(health, ["running", "running", "running"])
        self.assertIn(THREAD, saved.get("unresolved") or {})

    def test_a_lookup_without_retry_after_runs_again(self):
        lookups, health, _saved = self._persistent_lookup(HttpError(503), cycles=2)
        self.assertGreaterEqual(len(lookups), 2)
        self.assertEqual(health, ["running", "running"])

    def _persistent_lookup(self, failure, cycles=3):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        lookups = []
        message_id = str(int(snowflake_at(box.clock() - 10)) + 1)

        def call(method, path, token, body=None, timeout=30, opener=None):
            if path == f"/channels/{THREAD}":
                lookups.append(box.clock())
                raise failure
            if path == f"/channels/{CHANNEL}":
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD}
            if "/messages?" in path:
                return []
            return box.call(method, path, token, body)

        frame = message_create(
            message_id, f"<@{BOT}> wait", channel=THREAD, guild=GUILD,
            mentions=[{"id": BOT}],
        )
        sock = FakeSocket(box.clock, [hello(), ready(), frame])
        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=lambda url, timeout=30: sock,
            clock=box.clock, monotonic=box.clock,
        )
        health = []
        for _ in range(cycles):
            result = run.run_once(bridge, None, "", box.root)
            self.assertEqual(len(result), 0)
            saved = json.loads((box.root / "state" / "health.json").read_text())
            health.append(saved["state"])
        return lookups, health, box.markers() or {}

    def test_a_global_rate_limit_holds_every_unresolved_lookup(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        (box.root / "state" / "discord-markers.json").write_text(json.dumps({
            "channels": {}, "threads": {}, "parents": {},
            "unresolved": {THREAD: "1", OTHER: "1"},
        }), encoding="utf-8")
        lookups = []

        def call(method, path, token, body=None, timeout=30, opener=None):
            if path in (f"/channels/{THREAD}", f"/channels/{OTHER}"):
                lookups.append(path)
                raise HttpError(429, retry_after=300, is_global=True)
            if path == f"/channels/{CHANNEL}":
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD}
            if "/messages?" in path:
                return []
            return box.call(method, path, token, body)

        sock = FakeSocket(box.clock, [hello(), ready()])
        bridge = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=lambda url, timeout=30: sock,
            clock=box.clock, monotonic=box.clock,
        )
        bridge.fetch()
        self.assertEqual(lookups, [f"/channels/{THREAD}"])
        saved = box.markers()
        self.assertEqual(saved["unresolved"][THREAD], "1")
        self.assertEqual(saved["unresolved"][OTHER], "1")
        bridge.fetch()
        self.assertEqual(lookups, [f"/channels/{THREAD}"])

    def test_a_fresh_adapter_retries_a_lookup_immediately(self):
        box = self._box()
        allow = box.allow(f"{CHANNEL}/{USER}")
        message_id = str(int(snowflake_at(box.clock() - 10)) + 1)
        lookups = {"n": 0}
        row = {}

        def call(method, path, token, body=None, timeout=30, opener=None):
            if path == f"/channels/{THREAD}":
                lookups["n"] += 1
                if lookups["n"] == 1:
                    raise HttpError(429, retry_after=300)
                return {
                    "id": THREAD, "type": 11, "parent_id": CHANNEL,
                    "guild_id": GUILD,
                }
            if path == f"/channels/{CHANNEL}":
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD}
            if f"/channels/{THREAD}/messages?" in path:
                if row and int(row["id"]) > _after(path):
                    return [row]
                return []
            if "/messages?" in path:
                return []
            return box.call(method, path, token, body)

        frame = message_create(
            message_id, f"<@{BOT}> held", channel=THREAD, guild=GUILD,
            mentions=[{"id": BOT}],
        )
        row.update(json.loads(frame[1])["d"])
        sockets = []

        def connect(url, timeout=30):
            frames = [hello(), ready(), frame] if not sockets else [hello(), resumed()]
            sock = FakeSocket(box.clock, frames, ack=True)
            sockets.append(sock)
            return sock

        first = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=connect,
            clock=box.clock, monotonic=box.clock,
        )
        first.fetch()
        self.assertEqual(
            box.markers()["unresolved"][THREAD], str(int(message_id) - 1),
        )
        fresh = Discord(
            TOKEN, state=box.root / "state", allowlist_path=allow,
            poll_timeout=1, call=call, connect=connect,
            clock=box.clock, monotonic=box.clock,
        )
        result = run.run_once(fresh, None, "", box.root)
        self.assertEqual(_bodies(box.root), ["held"])
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(lookups["n"], 2)


if __name__ == "__main__":
    unittest.main()
