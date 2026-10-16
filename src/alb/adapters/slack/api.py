"""Slack inbound: Socket Mode in, letters out.

An envelope is acknowledged from confirm(), after the poller has decided the
event is durable or deliberately dropped. Acknowledging inside fetch() would
tell Slack the event is safe before the letter exists.
"""
import json
import os
import pathlib
import socket
import time

from alb.adapters.errors import FetchFailed, TransientFailure
from alb.adapters.slack import events, web, websocket
from alb.media import inspect as media_inspect

# ok:false values that mean this token will not start working if we wait.
_REFUSED = frozenset({
    "invalid_auth", "not_authed", "token_revoked",
    "account_inactive", "not_allowed_token_type",
})


class Slack:
    """The platform object the poller already knows: fetch, ack, confirm."""

    name = "slack"
    HELLO_TIMEOUT = 30
    QUIET = 0.2
    PONG_TIMEOUT = 10
    FIRST_RUN_WINDOW = 86400
    MAX_PAGES = 10
    MAX_THREADS = 200

    def __init__(self, bot_token, app_token=None, *, state=None,
                 allowlist_path=None, poll_timeout=25, call=web.call,
                 connect=websocket.connect, clock=time.time):
        self._bot = bot_token
        self._app = app_token
        self._state = pathlib.Path(state) if state is not None else None
        self._allowlist_path = allowlist_path
        self._poll_timeout = poll_timeout
        self._call = call
        self._connect = connect
        self._clock = clock
        self._ws = None
        self._team = None
        self._bot_user = None
        self._queue = []
        self._seen = set()
        self._envelopes = {}
        self._staged = {}
        self._dropped = []
        self._marks = {}
        self._dropped_marks = []
        self._pending = set()
        self._markers = None
        self._saw_frame = False
        self._file_urls = {}
        # Returned by fetch and not yet confirmed. See _requeue_inflight.
        self._inflight = []
        # Marks for a conversation still being caught up, held until it is
        # finished so the marker moves once, to the true high-water mark.
        self._held = []
        # (method, conversation, thread) -> (oldest, cursor) for a history
        # read that stopped at the page cap and continues next cycle.
        self._continuations = {}

    def __repr__(self):
        return "<Slack app>"

    def fetch(self, offset=None):
        self._seen.clear()
        self._requeue_inflight()
        # Catch-up can raise while a live frame is already on the socket.
        # Hold that error, read the socket, and re-raise only when nothing
        # arrived. A queued live event still has to be confirmed, with the
        # unread conversations left pending so their markers stay put.
        deferred = None
        try:
            try:
                self._retry_pending()
                self._ensure_connection()
            except TransientFailure as exc:
                deferred = exc
            self._read_live()
            if deferred is not None and not self._queue:
                raise deferred
            if not self._queue and not self._saw_frame:
                self._require_pong()
            return self._queued()
        except FetchFailed:
            raise
        except TransientFailure:
            raise
        except ValueError:
            raise TransientFailure("network: slack unreadable response") from None
        except web.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"slack deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            raise FetchFailed(f"slack refused: HTTP {exc.status}") from None
        except (OSError, websocket.WebSocketError) as exc:
            self._close_socket()
            raise TransientFailure(f"network: {exc}") from None

    def ack(self, update_id):
        # Catch-up has no envelope. Staging the id anyway is what lets confirm
        # move the marker after the poller has accepted the item.
        found = self._envelopes.pop(update_id, None)
        staged = self._staged.setdefault(update_id, [])
        if found:
            staged.extend(found)

    def confirm(self):
        acked = list(self._staged)
        self._send_envelope_acks()
        self._advance_markers(acked)
        self._persist_markers()
        for update_id in acked:
            self._marks.pop(update_id, None)
        self._dropped_marks.clear()
        self._file_urls.clear()
        # Every item the last fetch returned was handled: the cycle confirmed.
        self._inflight = []

    def close(self):
        self._close_socket()

    def download_photo(self, file_id):
        """Bytes of one inbound image, or a failure the poller already handles.

        The URL came with the event. files.info is only the fallback. A
        rate limit or a dead network is worth retrying; anything else becomes
        the letter's "[photo unavailable]" marker.
        """
        url = self._file_urls.get(file_id) or self._lookup_file_url(file_id)
        try:
            data = web.get_bytes(url, self._bot, media_inspect.MAX_BYTES)
        except web.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"slack deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            raise FetchFailed("photo download refused") from None
        except OSError:
            raise TransientFailure("network: slack file") from None
        except ValueError:
            raise FetchFailed("photo download refused") from None
        try:
            media_inspect.detect(data)
        except media_inspect.MediaError:
            raise FetchFailed("photo download refused") from None
        return data

    def _lookup_file_url(self, file_id):
        try:
            body = self._call("files.info", self._bot, {"file": file_id})
        except web.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                raise TransientFailure(
                    f"slack deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            raise FetchFailed("photo download refused") from None
        except OSError:
            raise TransientFailure("network: slack file") from None
        except ValueError:
            raise TransientFailure("network: slack unreadable response") from None
        if not isinstance(body, dict) or body.get("ok") is False:
            error = str(body.get("error") or "") if isinstance(body, dict) else ""
            if error in _REFUSED:
                raise FetchFailed(f"slack refused: {error}")
            raise FetchFailed("photo download refused")
        info = body.get("file")
        if isinstance(info, dict):
            for key in ("url_private_download", "url_private", "url"):
                candidate = info.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
        raise FetchFailed("photo download refused")

    def _queued(self):
        items = list(self._queue)
        self._queue.clear()
        self._inflight = list(items)
        return items

    def _requeue_inflight(self):
        """Items a fetch returned whose cycle never reached confirm go again.

        The poller raised part-way through them - a photo download waiting
        out a 429, say - so some may have letters and some not. The ledger
        lands the written ones once; the rest must not be forgotten. Dropping
        them is how a later idle cycle confirmed a mark past mail that never
        landed, and the reconnect after excluded it for good.
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
        self._ensure_identity()
        opened = self._invoke("apps.connections.open", self._app)
        url = opened.get("url") if isinstance(opened, dict) else None
        if not isinstance(url, str) or not url:
            raise TransientFailure("network: slack missing socket url")
        self._ws = self._connect(url)
        self._await_hello()
        self._catch_up()

    def _ensure_identity(self):
        if self._team is not None:
            return
        auth = self._invoke("auth.test", self._bot)
        self._team = auth.get("team_id") or ""
        self._bot_user = auth.get("user_id") or ""

    def _invoke(self, method, token, params=None):
        body = self._call(method, token, params)
        if isinstance(body, dict) and body.get("ok") is False:
            error = str(body.get("error") or "unknown")
            if error in _REFUSED:
                raise FetchFailed(f"slack refused: {error}")
            # Anything else can clear on its own. Killing the bridge on an
            # unknown Slack error would turn a blip into an outage.
            raise TransientFailure(f"network: slack {error}")
        return body if isinstance(body, dict) else {}

    def _recv_until(self, deadline):
        # The test doubles speak frames already. A real socket has to hear
        # the same absolute deadline _fill rechecks between bytes.
        if isinstance(self._ws, websocket.WebSocket):
            return self._ws.recv(deadline, self._clock)
        return self._ws.recv()

    def _await_hello(self):
        deadline = self._clock() + self.HELLO_TIMEOUT
        while self._clock() < deadline:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._ws.settimeout(remaining)
            try:
                kind, data = self._recv_until(self._clock() + remaining)
            except socket.timeout:
                break
            if kind != "text":
                continue
            message = _json(data)
            if message.get("type") == "hello":
                self._write_connection(message.get("num_connections"))
                return
        self._close_socket()
        raise TransientFailure("network: slack socket silent")

    def _read_live(self):
        self._saw_frame = False
        deadline = self._clock() + self._poll_timeout
        while self._clock() < deadline:
            if self._ws is None:
                return
            remaining = deadline - self._clock()
            if self._queue:
                remaining = min(remaining, self.QUIET)
            if remaining <= 0:
                return
            self._ws.settimeout(remaining)
            try:
                kind, data = self._recv_until(self._clock() + remaining)
            except socket.timeout:
                # Bytes already in hand mean the peer is slow, not gone.
                # Leave them buffered and do not spend the pong wait finishing
                # a frame that already missed this poll.
                incomplete = getattr(self._ws, "_incomplete", None)
                if incomplete is not None and incomplete():
                    self._saw_frame = True
                return
            self._saw_frame = True
            if kind in ("pong", "ping"):
                continue
            if kind == "close" or self._take_text(kind, data) == "disconnect":
                self._close_socket()
                return

    def _require_pong(self):
        if self._ws is None:
            raise TransientFailure("network: slack socket silent")
        self._ws.ping(b"")
        deadline = self._clock() + self.PONG_TIMEOUT
        while self._clock() < deadline:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._ws.settimeout(remaining)
            try:
                kind, data = self._recv_until(self._clock() + remaining)
            except socket.timeout:
                continue
            if kind in ("pong", "ping"):
                # Either way the connection is alive in both directions.
                return
            if kind == "close" or self._take_text(kind, data) == "disconnect":
                self._close_socket()
                return
            return
        self._close_socket()
        raise TransientFailure("network: slack socket silent")

    def _take_text(self, kind, data):
        if kind != "text":
            return ""
        message = _json(data)
        frame_type = message.get("type")
        if frame_type == "hello":
            self._write_connection(message.get("num_connections"))
            return "hello"
        if frame_type == "disconnect":
            return "disconnect"
        envelope = message.get("envelope_id")
        if not isinstance(envelope, str):
            envelope = ""
        if frame_type == "events_api":
            payload = message.get("payload")
            event = payload.get("event") if isinstance(payload, dict) else None
            self._remember(events.normalise(
                event, team=self._team, bot_user=self._bot_user,
            ), envelope, raw=event if isinstance(event, dict) else None)
            return "event"
        if envelope:
            self._dropped.append(envelope)
        return "other"

    def _remember(self, item, envelope="", raw=None, conv=""):
        mark = _mark(item, raw, conv)
        if item is None:
            if envelope:
                self._dropped.append(envelope)
            if mark is not None:
                self._dropped_marks.append(mark)
            return
        update_id = item["update_id"]
        self._stash_files(item)
        if mark is not None:
            self._marks[update_id] = mark
        if envelope:
            self._envelopes.setdefault(update_id, []).append(envelope)
        if update_id not in self._seen:
            self._seen.add(update_id)
            self._queue.append(item)

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

    def _retry_pending(self):
        """A conversation that failed last time is read again on the next fetch."""
        if self._team is None:
            return
        for conv in list(self._pending):
            self._read_conversation(conv)

    def _catch_up(self):
        self._load_markers()
        watched = self._watched_conversations()
        # Mark every conversation before reading. A raise never reaches the
        # rest, and confirm must not move a marker we did not catch up.
        for conv in watched:
            self._pending.add(conv)
        for conv in watched:
            self._read_conversation(conv)

    def _read_conversation(self, conv):
        """History and known threads. Pending until both have been read."""
        self._pending.add(conv)
        status, history = self._all_pages(
            "conversations.history", conv, self._oldest_conversation(conv),
        )
        if status == "skip":
            self._pending.discard(conv)
            return
        if status == "failed":
            return
        self._queue_history(conv, history)
        if status == "partial":
            # More history than one cycle reads. Publish this much, stay
            # pending so the marker cannot pass the unread tail, continue next
            # cycle from the saved cursor.
            return
        threads = ((self._markers or {}).get("threads") or {}).get(conv) or {}
        if not isinstance(threads, dict):
            threads = {}
        for thread_ts, marker in list(threads.items()):
            status, replies = self._all_pages(
                "conversations.replies", conv, marker, {"ts": thread_ts},
            )
            if status == "skip":
                continue
            if status == "failed":
                return
            self._queue_history(conv, replies)
            if status == "partial":
                return
        self._pending.discard(conv)

    def _watched_conversations(self):
        found = []
        for entry in self._allowlist_entries():
            conv = entry.split("/", 1)[0]
            if conv and conv not in found:
                found.append(conv)
        for conv in ((self._markers or {}).get("threads") or {}):
            if conv not in found:
                found.append(conv)
        return found

    def _allowlist_entries(self):
        if not self._allowlist_path:
            return []
        try:
            data = json.loads(pathlib.Path(self._allowlist_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list):
            return []
        return [entry for entry in chats if isinstance(entry, str) and "/" in entry]

    def _queue_history(self, conv, messages):
        rows = []
        for message in messages:
            item = self._normalise_history(conv, message)
            ts = message.get("ts") if isinstance(message.get("ts"), str) else ""
            rows.append((ts, item, message))
        rows.sort(key=lambda row: _ts_value(row[0]))
        for _ts, item, message in rows:
            if item is None:
                self._remember(None, raw=message, conv=conv)
            else:
                self._remember(item)

    def _normalise_history(self, conv, message):
        event = dict(message)
        event.setdefault("type", "message")
        if not isinstance(event.get("channel"), str) or not event.get("channel"):
            event["channel"] = conv
        channel_type = "im" if str(conv).startswith("D") else "channel"
        return events.normalise(
            event, team=self._team, bot_user=self._bot_user,
            channel_type=channel_type,
        )

    def _oldest_conversation(self, conv):
        self._load_markers()
        marker = (self._markers.get("conversations") or {}).get(conv)
        if marker:
            return marker
        # Six decimals, as Slack writes a ts. A float's own repr can carry
        # seven, and Slack answers that with ok:true and no messages.
        return f"{self._clock() - self.FIRST_RUN_WINDOW:.6f}"

    def _all_pages(self, method, conv, oldest, extra=None):
        """("done" | "partial" | "skip" | "failed", messages).

        At most MAX_PAGES pages a cycle. Stopping there is "partial", never
        "done": the cursor and the window it belongs to are kept, and the
        next cycle continues from them. Treating a cap as completion let the
        marker pass an unread tail that no later read would reach.
        """
        key = (method, conv, (extra or {}).get("ts", ""))
        saved = self._continuations.get(key)
        cursor = ""
        if saved:
            oldest, cursor = saved
        found = []
        for _page in range(self.MAX_PAGES):
            params = {"channel": conv, "oldest": oldest, "inclusive": False}
            if extra:
                params.update(extra)
            if cursor:
                params["cursor"] = cursor
            body = self._history_call(method, params, conv)
            if body is None:
                return "failed", []
            if body == "skip":
                self._continuations.pop(key, None)
                return "skip", []
            if body == "restart":
                # The cursor is no longer valid. Start this read again from
                # the frozen marker next cycle; nothing read so far is lost,
                # because the ledger lands a re-read message once.
                self._continuations.pop(key, None)
                self._pending.add(conv)
                return "failed", []
            messages = body.get("messages")
            if isinstance(messages, list):
                found.extend(message for message in messages if isinstance(message, dict))
            meta = body.get("response_metadata")
            nxt = meta.get("next_cursor") if isinstance(meta, dict) else ""
            if not isinstance(nxt, str) or not nxt:
                self._continuations.pop(key, None)
                return "done", found
            cursor = nxt
        self._continuations[key] = (oldest, cursor)
        return "partial", found

    def _history_call(self, method, params, conv):
        try:
            body = self._call(method, self._bot, params)
        except web.HttpError as exc:
            if exc.status == 429 or exc.status >= 500:
                self._pending.add(conv)
            if exc.status == 429:
                raise TransientFailure(
                    f"slack deferred: HTTP {exc.status}", exc.retry_after,
                ) from None
            if exc.status >= 500:
                return None
            raise FetchFailed(f"slack refused: HTTP {exc.status}") from None
        except OSError:
            self._pending.add(conv)
            return None
        if not isinstance(body, dict):
            self._pending.add(conv)
            return None
        if body.get("ok") is False:
            error = str(body.get("error") or "unknown")
            if error in ("not_in_channel", "channel_not_found"):
                return "skip"
            if error == "invalid_cursor":
                return "restart"
            if error in _REFUSED:
                raise FetchFailed(f"slack refused: {error}")
            self._pending.add(conv)
            raise TransientFailure(f"network: slack {error}")
        return body

    def _load_markers(self):
        if self._markers is not None:
            return
        self._markers = {"conversations": {}, "threads": {}}
        if self._state is None:
            return
        path = self._state / "slack-markers.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        conversations = data.get("conversations")
        threads = data.get("threads")
        if isinstance(conversations, dict):
            self._markers["conversations"] = {
                str(key): str(value) for key, value in conversations.items()
            }
        if isinstance(threads, dict):
            cleaned = {}
            for conv, mapping in threads.items():
                if isinstance(mapping, dict):
                    cleaned[str(conv)] = {
                        str(key): str(value) for key, value in mapping.items()
                    }
            self._markers["threads"] = cleaned

    def _advance_markers(self, acked):
        """Move markers for what this cycle handled - except in a conversation
        still being caught up. Those marks are held, and applied together once
        the conversation is finished, so its marker moves to the true
        high-water mark and never across an unread stretch."""
        self._load_markers()
        held = []
        for mark in self._held + self._marks_to_apply(acked):
            if mark["conv"] in self._pending:
                held.append(mark)
                continue
            self._bump(mark)
        self._held = held
        self._cap_threads()

    def _marks_to_apply(self, acked):
        found = []
        for update_id in acked:
            mark = self._marks.get(update_id)
            if mark is not None:
                found.append(mark)
        found.extend(self._dropped_marks)
        return found

    def _bump(self, mark):
        conv = mark["conv"]
        ts = mark["ts"]
        conversations = self._markers.setdefault("conversations", {})
        conversations[conv] = _max_ts(conversations.get(conv), ts)
        thread = mark.get("thread") or ""
        if not thread:
            return
        threads = self._markers.setdefault("threads", {}).setdefault(conv, {})
        threads[thread] = _max_ts(threads.get(thread), ts)

    def _cap_threads(self):
        threads = self._markers.get("threads") or {}
        flat = []
        for conv, mapping in threads.items():
            if not isinstance(mapping, dict):
                continue
            for thread, ts in mapping.items():
                flat.append((_ts_value(ts), conv, thread, ts))
        if len(flat) <= self.MAX_THREADS:
            return
        flat.sort(reverse=True)
        kept = {}
        for _value, conv, thread, ts in flat[:self.MAX_THREADS]:
            kept.setdefault(conv, {})[thread] = ts
        self._markers["threads"] = kept

    def _persist_markers(self):
        if self._state is None:
            return
        self._load_markers()
        _write_private(
            self._state / "slack-markers.json",
            json.dumps(self._markers, sort_keys=True),
        )

    def _send_envelope_acks(self):
        if self._ws is None:
            raise TransientFailure("network: slack socket closed")
        envelopes = [envelope for found in self._staged.values() for envelope in found]
        envelopes.extend(self._dropped)
        try:
            for envelope in envelopes:
                if envelope:
                    self._ws.send_text(json.dumps({"envelope_id": envelope}))
        except (OSError, websocket.WebSocketError) as exc:
            raise TransientFailure(f"network: {exc}") from None
        self._staged.clear()
        self._dropped.clear()

    def _write_connection(self, connections):
        if self._state is None:
            return
        try:
            count = int(connections)
        except (TypeError, ValueError):
            count = 0
        text = json.dumps({"connections": count, "at": self._clock()})
        _write_private(self._state / "slack-connection.json", text)

    def _close_socket(self):
        socket_ = self._ws
        self._ws = None
        if socket_ is None:
            return
        try:
            socket_.close()
        except OSError:
            pass


def _json(data):
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ts_value(ts):
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _max_ts(current, candidate):
    if not candidate:
        return current
    if current is None or _ts_value(candidate) >= _ts_value(current):
        return str(candidate)
    return current


def _mark(item, raw, conv=""):
    raw = raw if isinstance(raw, dict) else {}
    thread = ""
    if item is not None:
        provenance = item.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        conv = provenance.get("slack_channel") or conv
        ts = item.get("message_id") or ""
        thread = provenance.get("slack_thread_ts") or ""
        if not thread and not str(conv).startswith("D"):
            thread = item.get("reply_thread") or ""
    else:
        if isinstance(raw.get("channel"), str) and raw.get("channel"):
            conv = raw["channel"]
        ts = raw.get("ts") if isinstance(raw.get("ts"), str) else ""
        if isinstance(raw.get("thread_ts"), str):
            thread = raw["thread_ts"]
    if not conv or not ts:
        return None
    return {"conv": str(conv), "ts": str(ts), "thread": str(thread or "")}


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)
