"""Discord inbound: the gateway in, letters out.

Discord does not acknowledge events. confirm() is what moves a marker, after
the poller has the letter on disk or has decided to drop it.
"""
import base64
import json
import os
import pathlib
import socket
import struct
import time
import urllib.parse

from alb.adapters import websocket
from alb.adapters.discord import events, rest
from alb.adapters.errors import FetchFailed, TransientFailure
from alb.media import inspect as media_inspect

# GUILD_MESSAGES (1 << 9) and DIRECT_MESSAGES (1 << 12). No privileged bit.
INTENTS = (1 << 9) | (1 << 12)
_DISCORD_EPOCH_MS = 1420070400000
# A parent lookup that did not finish. Not a channel, and not a denial.
_UNRESOLVED = object()

_FATAL = {
    4004: "authentication failed",
    4010: "invalid shard",
    4011: "sharding required",
    4012: "invalid API version",
    4013: "invalid intent",
    4014: "disallowed intent",
}


class Discord:
    """The platform object the poller already knows: fetch, ack, confirm."""

    name = "discord"
    INTENTS = INTENTS
    HELLO_TIMEOUT = 30
    QUIET = 0.2
    SESSION_FRESH = 300
    FIRST_RUN_WINDOW = 86400
    MAX_PAGES = 10
    MAX_THREADS = 200
    CONNECT_ATTEMPTS = 3

    def __init__(self, token, *, state=None, allowlist_path=None, poll_timeout=25,
                 call=rest.call, connect=websocket.connect, clock=time.time,
                 monotonic=time.monotonic):
        self._token = token
        self._state = pathlib.Path(state) if state is not None else None
        self._allowlist_path = allowlist_path
        self._poll_timeout = poll_timeout
        self._call = call
        self._connect = connect
        self._clock = clock
        self._monotonic = monotonic
        self._ws = None
        self._bot_user = bot_user_from_token(token)
        self._session_id = None
        self._resume_url = None
        self._seq = None
        self._queue = []
        self._seen = set()
        self._marks = {}
        self._dropped_marks = []
        self._held = []
        self._staged = {}
        self._pending = set()
        self._resume_after = {}
        self._channel_cache = {}
        self._markers = None
        self._file_urls = {}
        self._inflight = []
        self._heartbeat_interval = None
        self._heartbeat_due = None
        self._awaiting_ack = False
        self._last_ack = None
        self._dispatch = None
        self._control = None
        self._connect_attempts = 0
        self._next_identify = None
        self._heartbeat_lapsed = False
        self._route_after = {}
        self._lookup_blocked_until = None

    def __repr__(self):
        return "<Discord bot>"

    def fetch(self, offset=None):
        self._seen.clear()
        self._requeue_inflight()
        self._connect_attempts = 0
        if self._ws is not None and self.heartbeat_expired():
            # This cycle outran the gateway heartbeat. The socket may be
            # dead. 4000 keeps the session resumable.
            self._close_socket(4000)
        deferred = None
        try:
            try:
                reconnecting = self._ws is None
                self._ensure_connection()
                self._catch_up(full=reconnecting)
            except TransientFailure as exc:
                deferred = exc
            if self._ws is not None:
                self._read_live()
            # A REST wait is not a reason to stop reading the gateway. With
            # the socket live, the deferred channels stay pending (frozen
            # markers) and the cycle returns; raising would have the CLI sleep
            # out the wait - up to a global 429's whole retry_after - with no
            # heartbeat sent. Only a fetch with no live socket defers.
            if deferred is not None and not self._queue and self._ws is None:
                raise deferred
            if not self._queue and not self._ack_is_fresh():
                raise TransientFailure("network: discord heartbeat not acknowledged")
            self._touch_session()
            return self._queued()
        except FetchFailed:
            raise
        except TransientFailure:
            raise
        except rest.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"discord deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            if exc.status in (401, 403):
                raise FetchFailed(f"discord refused: HTTP {exc.status}") from None
            raise TransientFailure(f"network: discord HTTP {exc.status}") from None
        except ValueError:
            raise TransientFailure("network: discord unreadable response") from None
        except (OSError, websocket.WebSocketError):
            self._close_socket(4000)
            raise TransientFailure("network: discord gateway") from None

    def ack(self, update_id):
        self._staged[update_id] = True

    def confirm(self):
        acked = list(self._staged)
        self._advance_markers(acked)
        self._persist_markers()
        self._staged.clear()
        self._marks.clear()
        self._dropped_marks.clear()
        self._file_urls.clear()
        self._inflight = []

    def close(self):
        self._close_socket(1000)

    def download_photo(self, file_id):
        """Bytes of one inbound image. The URL came with the message."""
        self._beat_if_due()
        url = self._file_urls.get(file_id)
        if not isinstance(url, str) or not url:
            raise FetchFailed("photo download refused")
        try:
            data = rest.get_bytes(url, media_inspect.MAX_BYTES)
        except rest.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"discord deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            raise FetchFailed("photo download refused") from None
        except OSError:
            raise TransientFailure("network: discord file") from None
        except ValueError:
            raise FetchFailed("photo download refused") from None
        self._beat_if_due()
        try:
            media_inspect.detect(data)
        except media_inspect.MediaError:
            raise FetchFailed("photo download refused") from None
        return data

    def _catch_up(self, full=True):
        if not self._bot_user:
            return
        # A live socket already has the gateway. Read history on connect, and
        # when a channel is still pending or mid-page. Not on every poll.
        self._load_markers()
        channels, threads = self._watched()
        unresolved = list((self._markers.get("unresolved") or {}).keys())
        if not full:
            wanted = set(self._pending) | set(self._resume_after) | set(unresolved)
            if not wanted:
                return
            channels = [channel for channel in channels if channel in wanted]
            threads = [item for item in threads if item[1] in wanted]
        # Pending before the first read. A 429 must not let a channel we
        # have not reached advance its marker from a live message.
        self._pending.update(channels)
        self._pending.update(thread for _parent, thread in threads)
        self._pending.update(unresolved)
        for channel in channels:
            self._read_history(channel, None)
        for parent, thread in threads:
            self._read_history(thread, parent)
        self._retry_unresolved()

    def _watched(self):
        channels = self._allowlist_channels()
        threads = []
        for parent, mapping in (self._markers.get("threads") or {}).items():
            if not isinstance(mapping, dict):
                continue
            for thread in mapping:
                if thread not in channels:
                    threads.append((str(parent), str(thread)))
        return channels, threads

    def _allowlist_channels(self):
        found = []
        for entry in self._allowlist_entries():
            channel = entry.split("/", 1)[0]
            if channel and channel not in found:
                found.append(channel)
        return found

    def _allowlist_entries(self):
        if not self._allowlist_path:
            return []
        try:
            data = json.loads(
                pathlib.Path(self._allowlist_path).read_text(encoding="utf-8"),
            )
        except (OSError, json.JSONDecodeError):
            return []
        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list):
            return []
        return [entry for entry in chats if isinstance(entry, str) and "/" in entry]

    def _read_history(self, channel, parent):
        # Stay pending until this page run finishes, so confirm cannot move
        # the marker across a read that did not succeed.
        self._pending.add(channel)
        if self._history_waiting(channel):
            # Discord named a time for this route. Ask again then, not on
            # every poll; the channel stays pending, so its marker holds.
            return
        record = self._channel_record(channel)
        if record is None:
            return
        if record == "skip":
            self._pending.discard(channel)
            return
        after = (
            self._resume_after.get(channel)
            or self._stored_after(channel, parent)
        )
        for _page in range(self.MAX_PAGES):
            self._beat_if_due()
            rows = self._history_call(channel, after)
            if rows is None:
                return
            if rows == "skip":
                self._pending.discard(channel)
                return
            if not rows:
                self._pending.discard(channel)
                self._resume_after.pop(channel, None)
                return
            ordered = [
                row for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
                and row["id"].isdigit()
            ]
            if not ordered:
                self._pending.discard(channel)
                return
            ordered.sort(key=lambda row: int(row["id"]))
            for row in ordered:
                self._ingest(self._with_guild(row, record), parent=parent)
            after = str(max(int(row["id"]) for row in ordered))
            self._resume_after[channel] = after
            if len(rows) < 100:
                self._pending.discard(channel)
                self._resume_after.pop(channel, None)
                return

    def _stored_after(self, channel, parent):
        self._load_markers()
        if parent:
            current = (
                (self._markers.get("threads") or {}).get(parent) or {}
            ).get(channel)
        else:
            current = (self._markers.get("channels") or {}).get(channel)
        if isinstance(current, str) and current:
            return current
        return snowflake_at(self._clock() - self.FIRST_RUN_WINDOW)

    def _rest(self, method, path):
        """One authenticated REST call. A global rate limit holds every route.

        Discord's global 429 is not about the route that drew it, so while one
        is in force nothing here asks again - not a lookup, not catch-up
        history, not the gateway URL. The gateway itself is not REST: a live
        socket keeps its heartbeats and a Resume needs no REST call.
        """
        blocked = self._lookup_blocked_until
        now = self._monotonic()
        if blocked is not None and now < blocked:
            failure = TransientFailure("discord deferred: global rate limit",
                                       blocked - now)
            failure.is_global = True
            raise failure
        # A route's own 429 holds that route, whichever caller asks next.
        route = path.split("?", 1)[0]
        until = self._route_after.get(route)
        if until is not None and now < until:
            raise TransientFailure("discord deferred: rate limit", until - now)
        try:
            return self._call(method, path, self._token)
        except rest.HttpError as exc:
            delay = exc.retry_after
            if (exc.status == 429 and isinstance(delay, (int, float))
                    and not isinstance(delay, bool) and delay >= 0):
                until = self._monotonic() + float(delay)
                if exc.is_global:
                    if self._lookup_blocked_until is None or until > self._lookup_blocked_until:
                        self._lookup_blocked_until = until
                else:
                    current = self._route_after.get(route)
                    if current is None or until > current:
                        self._route_after[route] = until
            raise

    def _channel_record(self, channel):
        """One GET /channels/{id}, reused. History rows do not carry a guild."""
        if channel in self._channel_cache:
            return self._channel_cache[channel]
        self._beat_if_due()
        try:
            body = self._rest("GET", f"/channels/{channel}")
        except rest.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                failure = TransientFailure(
                    f"discord deferred: HTTP {exc.status}", exc.retry_after,
                )
                failure.is_global = bool(exc.is_global)
                raise failure from None
            if exc.status in (403, 404):
                self._channel_cache[channel] = "skip"
                return "skip"
            raise FetchFailed(f"discord refused: HTTP {exc.status}") from None
        except OSError:
            return None
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        kind = body.get("type")
        guild = body.get("guild_id")
        parent = body.get("parent_id")
        record = {
            "type": kind if isinstance(kind, int) else None,
            "guild_id": guild if isinstance(guild, str) else "",
            "parent_id": parent if isinstance(parent, str) else "",
        }
        self._channel_cache[channel] = record
        return record

    def _with_guild(self, row, record):
        guild = record.get("guild_id") if isinstance(record, dict) else ""
        if not guild or row.get("guild_id"):
            return row
        stamped = dict(row)
        stamped["guild_id"] = guild
        return stamped

    def _history_call(self, channel, after):
        path = f"/channels/{channel}/messages?limit=100&after={after}"
        try:
            body = self._rest("GET", path)
        except rest.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"discord deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            if exc.status in (403, 404):
                return "skip"
            raise FetchFailed(f"discord refused: HTTP {exc.status}") from None
        except OSError:
            return None
        except ValueError:
            return None
        if not isinstance(body, list):
            return None
        return [row for row in body if isinstance(row, dict)]

    def _route_waiting(self, route):
        until = self._route_after.get(route)
        return until is not None and self._monotonic() < until

    def _history_waiting(self, channel):
        """A history read makes two calls: the channel, then its messages.
        Either one held by Discord holds the read."""
        return (self._route_waiting(f"/channels/{channel}")
                or self._route_waiting(f"/channels/{channel}/messages"))

    def _queued(self):
        items = list(self._queue)
        self._queue.clear()
        self._inflight = list(items)
        return items

    def _requeue_inflight(self):
        """A fetch whose cycle never reached confirm is still owed its batch.

        Dropping it is how a later idle cycle moved a marker past mail that
        never landed.
        """
        if not self._inflight:
            return
        carried, self._inflight = self._inflight, []
        for item in carried:
            if item["update_id"] not in self._seen:
                self._seen.add(item["update_id"])
                self._queue.append(item)

    def _ensure_connection(self):
        if self._ws is not None:
            return
        session = self._fresh_session()
        if session is not None:
            self._session_id = session["session_id"]
            self._resume_url = session["resume_gateway_url"]
            self._seq = session["seq"]
            saved = session.get("bot_user")
            if isinstance(saved, str) and saved.isdigit():
                self._bot_user = saved
            self._open_resume(session)
            return
        self._open_identify()

    def _open_identify(self):
        info = self._rest("GET", "/gateway/bot")
        self._refuse_session_start(info)
        self._spend_attempt()
        url = info.get("url") if isinstance(info, dict) else None
        if not isinstance(url, str) or not url.startswith("wss://"):
            raise TransientFailure("network: discord missing gateway")
        self._ws = self._connect(self._gateway_url(url))
        self._dispatch = None
        self._finish_hello()
        # The window is counted from the Identify itself: a slow connect or
        # Hello must not let two Identifies land closer than the limit.
        self._take_identify_slot(info)
        self._send({"op": 2, "d": {
            "token": self._token,
            "intents": self.INTENTS,
            "properties": {"os": "linux", "browser": "alb", "device": "alb"},
        }})
        self._until(("READY",))

    def _open_resume(self, session):
        self._spend_attempt()
        self._ws = self._connect(self._gateway_url(session["resume_gateway_url"]))
        self._dispatch = None
        self._finish_hello()
        self._send({"op": 6, "d": {
            "token": self._token,
            "session_id": session["session_id"],
            "seq": session.get("seq"),
        }})
        self._until(("READY", "RESUMED"))

    def _spend_attempt(self):
        """One Identify or Resume. The budget is shared by the whole fetch."""
        self._connect_attempts += 1
        if self._connect_attempts > self.CONNECT_ATTEMPTS:
            raise TransientFailure("network: discord gateway")

    def _refuse_session_start(self, info):
        """remaining 0 means do not Identify. Resume is decided elsewhere."""
        limit = info.get("session_start_limit") if isinstance(info, dict) else None
        if not isinstance(limit, dict):
            return
        remaining = limit.get("remaining")
        if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining != 0:
            return
        reset = limit.get("reset_after")
        retry = None
        if isinstance(reset, (int, float)) and not isinstance(reset, bool) and reset >= 0:
            retry = float(reset) / 1000.0
        raise TransientFailure("discord deferred: session start limit", retry)

    def _identify_spacing(self, info):
        """How long one Identify holds the shared start window."""
        limit = info.get("session_start_limit") if isinstance(info, dict) else None
        concurrency = 1
        if isinstance(limit, dict):
            raw = limit.get("max_concurrency")
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
                concurrency = raw
        return 5.0 / concurrency

    def _take_identify_slot(self, info):
        """Wait out a short gap. The next Identify time survives this fetch.

        Runs on an open socket after Hello. The gap is at most five seconds
        and the first heartbeat has just gone out; Discord's interval (about
        41 s) is far longer, so none falls due. ACKs are not read during the
        wait, so an interval shorter than the gap would end it as a transient.
        """
        now = self._monotonic()
        while self._next_identify is not None and now < self._next_identify:
            self._beat_if_due()
            self._pause(min(self._next_identify - now, 1.0))
            now = self._monotonic()
        self._next_identify = now + self._identify_spacing(info)

    def _gateway_url(self, base):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise TransientFailure("network: discord missing gateway")
        query = parsed.query
        if "v=" not in query:
            query = "v=10&encoding=json" if not query else query + "&v=10&encoding=json"
        elif "encoding=" not in query:
            query += "&encoding=json"
        return urllib.parse.urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path or "/", query, "",
        ))

    def _finish_hello(self):
        deadline = self._monotonic() + self.HELLO_TIMEOUT
        while self._monotonic() < deadline:
            try:
                kind, data = self._recv_until(deadline)
            except socket.timeout:
                break
            if kind == "close":
                self._raise_close(data)
            if kind != "text":
                continue
            frame = self._load(data)
            if frame.get("op") != 10:
                continue
            raw = frame.get("d") if isinstance(frame.get("d"), dict) else {}
            try:
                seconds = float(raw.get("heartbeat_interval")) / 1000.0
            except (TypeError, ValueError):
                break
            if seconds <= 0:
                break
            self._heartbeat_interval = seconds
            self._heartbeat_due = self._monotonic()
            self._awaiting_ack = False
            self._beat_if_due()
            return
        self._close_socket(4000)
        raise TransientFailure("network: discord gateway silent")

    def _until(self, kinds):
        deadline = self._monotonic() + self.HELLO_TIMEOUT
        controls = 0
        while self._monotonic() < deadline:
            if self._control:
                controls += 1
                if controls > 2:
                    raise TransientFailure("network: discord gateway")
                self._apply_control()
                deadline = self._monotonic() + self.HELLO_TIMEOUT
                continue
            if self._dispatch in kinds:
                return
            self._beat_if_due()
            if self._monotonic() >= deadline:
                break
            limit = deadline
            if self._heartbeat_due is not None:
                limit = min(deadline, self._heartbeat_due)
            try:
                kind, data = self._recv_until(limit)
            except socket.timeout:
                continue
            self._handle(kind, data)
            if self._dispatch in kinds:
                return
        if self._dispatch in kinds:
            return
        self._close_socket(4000)
        raise TransientFailure("network: discord gateway silent")

    def _apply_control(self):
        action = self._control
        self._control = None
        self._dispatch = None
        self._close_socket(4000)
        if action == "identify":
            self._pause(1 + int(self._clock()) % 5)
            self._open_identify()
            return
        session = self._fresh_session()
        if session is None and self._session_id and self._resume_url:
            session = {
                "session_id": self._session_id,
                "resume_gateway_url": self._resume_url,
                "seq": self._seq,
            }
        if session is None:
            self._open_identify()
            return
        self._open_resume(session)

    def _read_live(self):
        deadline = self._monotonic() + self._poll_timeout
        controls = 0
        while self._monotonic() < deadline:
            if self._ws is None:
                return
            if self._control:
                controls += 1
                if controls > 2:
                    raise TransientFailure("network: discord gateway")
                self._apply_control()
                continue
            self._beat_if_due()
            if self._ws is None or self._monotonic() >= deadline:
                return
            limit = deadline
            if self._heartbeat_due is not None:
                limit = min(limit, self._heartbeat_due)
            if self._queue:
                limit = min(limit, self._monotonic() + self.QUIET)
            try:
                kind, data = self._recv_until(limit)
            except socket.timeout:
                continue
            self._handle(kind, data)

    def _handle(self, kind, data):
        if kind == "close":
            self._raise_close(data)
        if kind != "text":
            return
        frame = self._load(data)
        op = frame.get("op")
        if op == 11:
            self._awaiting_ack = False
            self._last_ack = self._monotonic()
            return
        if op == 1:
            self._heartbeat_due = self._monotonic()
            return
        if op == 7:
            self._control = "resume"
            return
        if op == 9:
            self._control = "resume" if frame.get("d") is True else "identify"
            if frame.get("d") is not True:
                self._clear_session()
            return
        if op != 0:
            return
        seq = frame.get("s")
        if isinstance(seq, int):
            self._seq = seq
        event = frame.get("t")
        if event == "READY":
            self._take_ready(frame.get("d"))
            self._dispatch = "READY"
        elif event == "RESUMED":
            self._dispatch = "RESUMED"
            self._touch_session()
            self._heartbeat_lapsed = False
        elif event == "MESSAGE_CREATE":
            body = frame.get("d")
            if isinstance(body, dict):
                self._ingest(body)

    def _raise_close(self, data):
        code = 4000
        if isinstance(data, (bytes, bytearray)) and len(data) >= 2:
            code = struct.unpack("!H", data[:2])[0]
        self._ws = None
        reason = _FATAL.get(code)
        if reason:
            raise FetchFailed(f"discord refused: {reason}")
        raise TransientFailure(f"network: discord closed {code}")

    def _take_ready(self, data):
        if not isinstance(data, dict):
            raise TransientFailure("network: discord gateway silent")
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        user_id = user.get("id")
        session_id = data.get("session_id")
        url = data.get("resume_gateway_url")
        if not all(isinstance(value, str) and value
                   for value in (user_id, session_id, url)):
            raise TransientFailure("network: discord gateway silent")
        self._bot_user = user_id
        self._session_id = session_id
        self._resume_url = url
        self._touch_session()
        self._heartbeat_lapsed = False

    def _beat_if_due(self):
        if self._heartbeat_interval is None or self._ws is None:
            return
        now = self._monotonic()
        if self._heartbeat_due is None or now < self._heartbeat_due:
            return
        if self._awaiting_ack:
            self._close_socket(4000)
            raise TransientFailure("network: discord heartbeat not acknowledged")
        # A beat this late still goes out, but the cycle is no longer live.
        # Moving the deadline must not erase that.
        if now > self._heartbeat_due + self._heartbeat_interval:
            self._heartbeat_lapsed = True
        seq = self._seq if isinstance(self._seq, int) else None
        self._send({"op": 1, "d": seq})
        self._awaiting_ack = True
        self._heartbeat_due = now + self._heartbeat_interval

    def heartbeat_expired(self):
        """A beat late by more than one interval is not a live cycle."""
        if self._heartbeat_lapsed:
            return True
        if not self._heartbeat_interval or self._heartbeat_due is None:
            return False
        return self._monotonic() > self._heartbeat_due + self._heartbeat_interval

    def _ack_is_fresh(self):
        if self._last_ack is None or not self._heartbeat_interval:
            return False
        return (self._monotonic() - self._last_ack) <= self._heartbeat_interval

    def _recv_until(self, deadline):
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise socket.timeout("deadline")
        if isinstance(self._ws, websocket.WebSocket):
            return self._ws.recv(deadline, self._monotonic)
        self._ws.settimeout(remaining)
        return self._ws.recv()

    def _send(self, payload):
        if self._ws is None:
            raise TransientFailure("network: discord gateway")
        try:
            self._ws.send_text(json.dumps(payload))
        except (OSError, websocket.WebSocketError, TypeError):
            self._close_socket(4000)
            raise TransientFailure("network: discord gateway") from None

    def _load(self, data):
        try:
            parsed = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise TransientFailure("network: discord unreadable response") from None
        if not isinstance(parsed, dict):
            raise TransientFailure("network: discord unreadable response")
        return parsed

    def _pause(self, seconds):
        stepper = getattr(self._monotonic, "advance", None)
        if stepper is not None:
            stepper(seconds)
            if self._clock is not self._monotonic:
                other = getattr(self._clock, "advance", None)
                if other is not None:
                    other(seconds)
            return
        time.sleep(seconds)

    def _close_socket(self, code=4000):
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            ws.close(code)
        except TypeError:
            try:
                ws.close()
            except OSError:
                return
        except OSError:
            return

    def _ingest(self, message, parent=None):
        if not isinstance(message, dict) or not self._bot_user:
            return
        if parent is None:
            parent = self._thread_parent(message)
        if parent is _UNRESOLVED:
            # The gateway will not redeliver this. The floor in the markers
            # is what brings it back, after the lookup succeeds.
            return
        item = events.normalise(message, bot_user=self._bot_user, parent=parent)
        channel = message.get("channel_id")
        message_id = message.get("id")
        mark = None
        if isinstance(channel, str) and channel and isinstance(message_id, str) and message_id:
            mark = {"channel": channel, "message": message_id, "parent": parent}
        if item is None:
            if mark is not None:
                self._dropped_marks.append(mark)
            return
        update_id = item["update_id"]
        if update_id in self._seen:
            return
        self._seen.add(update_id)
        if mark is not None:
            self._marks[update_id] = mark
        self._stash_files(item)
        self._queue.append(item)

    def _thread_parent(self, message):
        guild = message.get("guild_id")
        if not isinstance(guild, str) or not guild:
            return None
        channel = message.get("channel_id")
        if not isinstance(channel, str) or not channel:
            return None
        if channel in self._allowlist_channels():
            return None
        self._load_markers()
        if events.normalise(message, bot_user=self._bot_user, parent=channel) is None:
            return None
        record = self._thread_record(channel, message.get("id"))
        if record is _UNRESOLVED:
            return _UNRESOLVED
        parent = self._resolved_parent(record)
        if parent is None:
            return None
        self._markers.setdefault("parents", {})[channel] = parent
        return parent

    def _thread_record(self, channel, message_id):
        """The channel object, or UNRESOLVED when the lookup itself failed.

        A lookup Discord asked us to wait on is refused by the REST gate
        without a call, so a new mention joins the unresolved floor instead.
        """
        try:
            record = self._channel_record(channel)
        except TransientFailure:
            record = None
        if record is None:
            if self._remember_unresolved(channel, message_id):
                return _UNRESOLVED
            return None
        return record

    def _resolved_parent(self, record):
        if not isinstance(record, dict):
            return None
        # 10 announcement thread, 11 public thread, 12 private thread.
        # A category is also a parent_id, and it is not a thread.
        if record.get("type") not in (10, 11, 12):
            return None
        parent = record.get("parent_id")
        if not isinstance(parent, str) or not parent:
            return None
        return parent

    def _remember_unresolved(self, channel, message_id):
        """Keep the message below a floor until its thread parent is known.

        History is `after` an id, exclusive. One below this message includes
        it. A newer mention must not move the floor forward.
        """
        if not isinstance(message_id, str) or not message_id.isdigit():
            return False
        if int(message_id) <= 0:
            return False
        floor = str(int(message_id) - 1)
        self._load_markers()
        unresolved = self._markers.setdefault("unresolved", {})
        current = unresolved.get(channel)
        if not (isinstance(current, str) and current.isdigit()
                and int(current) <= int(floor)):
            unresolved[channel] = floor
        self._pending.update((channel,))
        self._persist_markers()
        return True

    def _retry_unresolved(self):
        self._load_markers()
        found = self._markers.get("unresolved")
        if not isinstance(found, dict) or not found:
            return
        for thread, floor in list(found.items()):
            try:
                # Refused at the REST gate, with no call, while Discord's
                # wait for this route or a global one is still running.
                record = self._channel_record(thread)
            except TransientFailure:
                continue
            if record is None:
                continue
            parent = self._resolved_parent(record)
            if parent is None:
                found.pop(thread, None)
                self._pending.discard(thread)
                continue
            self._markers.setdefault("parents", {})[thread] = parent
            stored = self._markers.setdefault("threads", {}).setdefault(parent, {})
            if not (isinstance(stored.get(thread), str) and stored.get(thread)):
                stored[thread] = floor if isinstance(floor, str) and floor.isdigit() else "0"
            found.pop(thread, None)
            self._pending.add(thread)
            self._read_history(thread, parent)
        if not found:
            self._markers.pop("unresolved", None)

    def _stash_files(self, item):
        files = item.get("files")
        if not isinstance(files, list):
            return
        for entry in files:
            if not isinstance(entry, dict):
                continue
            file_id = entry.get("id")
            url = entry.get("url")
            if isinstance(file_id, str) and file_id and isinstance(url, str) and url:
                self._file_urls[file_id] = url

    def _fresh_session(self):
        if self._state is None:
            return None
        path = self._state / "discord-session.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        session_id = data.get("session_id")
        url = data.get("resume_gateway_url")
        if not isinstance(session_id, str) or not session_id:
            return None
        if not isinstance(url, str) or not url.startswith("wss://"):
            return None
        try:
            age = self._clock() - float(data.get("at"))
        except (TypeError, ValueError):
            return None
        if age < 0 or age >= self.SESSION_FRESH:
            return None
        seq = data.get("seq")
        if not isinstance(seq, int):
            seq = None
        saved = data.get("bot_user")
        return {
            "session_id": session_id,
            "resume_gateway_url": url,
            "seq": seq,
            "bot_user": saved if isinstance(saved, str) and saved.isdigit() else "",
        }

    def _touch_session(self):
        if self._state is None or not self._session_id or not self._resume_url:
            return
        payload = {
            "session_id": self._session_id,
            "resume_gateway_url": self._resume_url,
            "seq": self._seq,
            "at": self._clock(),
        }
        if isinstance(self._bot_user, str) and self._bot_user:
            payload["bot_user"] = self._bot_user
        _write_private(self._state / "discord-session.json", json.dumps(
            payload, sort_keys=True,
        ))

    def _clear_session(self):
        self._session_id = None
        self._resume_url = None
        self._seq = None
        if self._state is None:
            return
        try:
            (self._state / "discord-session.json").unlink()
        except OSError:
            pass

    def _advance_markers(self, acked):
        self._load_markers()
        fresh = []
        for update_id in acked:
            mark = self._marks.get(update_id)
            if mark is not None:
                fresh.append(mark)
        fresh.extend(self._dropped_marks)
        held = []
        for mark in self._held + fresh:
            if mark.get("channel") in self._pending:
                held.append(mark)
                continue
            self._bump(mark)
        self._held = held
        self._cap_threads()

    def _bump(self, mark):
        channel = mark.get("channel")
        message = mark.get("message")
        if not isinstance(channel, str) or not isinstance(message, str):
            return
        parent = mark.get("parent")
        if isinstance(parent, str) and parent:
            threads = self._markers.setdefault("threads", {}).setdefault(parent, {})
            threads[channel] = _max_snowflake(threads.get(channel), message)
            self._markers.setdefault("parents", {})[channel] = parent
            return
        channels = self._markers.setdefault("channels", {})
        channels[channel] = _max_snowflake(channels.get(channel), message)

    def _cap_threads(self):
        threads = self._markers.get("threads") or {}
        flat = []
        for parent, mapping in threads.items():
            if not isinstance(mapping, dict):
                continue
            for thread, message in mapping.items():
                if isinstance(message, str) and message.isdigit():
                    flat.append((int(message), parent, thread, message))
        if len(flat) <= self.MAX_THREADS:
            return
        flat.sort(reverse=True)
        kept = {}
        parents = {}
        for _value, parent, thread, message in flat[:self.MAX_THREADS]:
            kept.setdefault(parent, {})[thread] = message
            parents[thread] = parent
        self._markers["threads"] = kept
        self._markers["parents"] = parents

    def _load_markers(self):
        if self._markers is not None:
            return
        self._markers = {"channels": {}, "threads": {}, "parents": {}}
        if self._state is None:
            return
        path = self._state / "discord-markers.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        channels = data.get("channels")
        if isinstance(channels, dict):
            self._markers["channels"] = {
                str(key): str(value) for key, value in channels.items()
                if isinstance(value, str)
            }
        threads = data.get("threads")
        if isinstance(threads, dict):
            cleaned = {}
            for parent, mapping in threads.items():
                if isinstance(mapping, dict):
                    cleaned[str(parent)] = {
                        str(key): str(value) for key, value in mapping.items()
                        if isinstance(value, str)
                    }
            self._markers["threads"] = cleaned
        parents = data.get("parents")
        if isinstance(parents, dict):
            self._markers["parents"] = {
                str(key): str(value) for key, value in parents.items()
                if isinstance(value, str)
            }
        unresolved = data.get("unresolved")
        if isinstance(unresolved, dict):
            self._markers["unresolved"] = {
                str(key): str(value) for key, value in unresolved.items()
                if isinstance(value, str) and str(value).isdigit()
            }

    def _persist_markers(self):
        if self._state is None:
            return
        self._load_markers()
        _write_private(
            self._state / "discord-markers.json",
            json.dumps(self._markers, sort_keys=True),
        )


def bot_user_from_token(token):
    """A bot token's first segment is the user id. READY is not required."""
    if not isinstance(token, str):
        return None
    segment = token.split(".", 1)[0]
    if not segment:
        return None
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        text = raw.decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None
    if text.isdigit():
        return text
    return None


def snowflake_at(unix_seconds):
    """A Discord snowflake for an instant. The low bits stay zero."""
    ms = int(float(unix_seconds) * 1000) - _DISCORD_EPOCH_MS
    if ms < 0:
        ms = 0
    return str(ms << 22)


def _max_snowflake(current, new):
    if not isinstance(new, str) or not new.isdigit():
        return current if isinstance(current, str) else ""
    if not isinstance(current, str) or not current.isdigit():
        return new
    return new if int(new) > int(current) else current


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)
