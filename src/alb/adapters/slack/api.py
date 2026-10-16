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
        self._pending = {}
        self._staged = {}
        self._dropped = []
        self._saw_frame = False

    def __repr__(self):
        return "<Slack app>"

    def fetch(self, offset=None):
        self._seen.clear()
        try:
            self._ensure_connection()
            self._read_live()
            if not self._queue and not self._saw_frame:
                self._require_pong()
            return self._queued()
        except FetchFailed:
            raise
        except TransientFailure:
            raise
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
        found = self._pending.pop(update_id, None)
        if found:
            self._staged.setdefault(update_id, []).extend(found)

    def confirm(self):
        self._send_envelope_acks()
        self._persist_markers()

    def close(self):
        self._close_socket()

    def _queued(self):
        items = list(self._queue)
        self._queue.clear()
        return items

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
        for item in self._catch_up():
            self._remember(item)

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

    def _await_hello(self):
        deadline = self._clock() + self.HELLO_TIMEOUT
        while self._clock() < deadline:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._ws.settimeout(remaining)
            try:
                kind, data = self._ws.recv()
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
                kind, data = self._ws.recv()
            except socket.timeout:
                return
            self._saw_frame = True
            if kind == "pong":
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
                kind, data = self._ws.recv()
            except socket.timeout:
                continue
            if kind == "pong":
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
            ), envelope)
            return "event"
        if envelope:
            self._dropped.append(envelope)
        return "other"

    def _remember(self, item, envelope=""):
        if item is None:
            if envelope:
                self._dropped.append(envelope)
            return
        update_id = item["update_id"]
        if envelope:
            self._pending.setdefault(update_id, []).append(envelope)
        if update_id not in self._seen:
            self._seen.add(update_id)
            self._queue.append(item)

    def _catch_up(self):
        return []

    def _persist_markers(self):
        return None

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


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)
