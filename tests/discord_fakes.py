"""Stand-ins for the Discord gateway. No network, no real ids."""
import json
import socket
import struct

TOKEN = "Bot." + "fixture"
BOT = "100000000000000009"
USER = "100000000000000003"
CHANNEL = "100000000000000001"
THREAD = "100000000000000002"
GUILD = "100000000000000010"
MESSAGE = "100000000000000021"


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeSocket:
    def __init__(self, clock, frames, ack=True):
        self.clock = clock
        self.frames = list(frames)
        self.sent = []
        self.timeout = 30
        self.closed = None
        self.ack = ack

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        self.clock.advance(self.timeout)
        raise socket.timeout("quiet")

    def send_text(self, text):
        payload = json.loads(text)
        self.sent.append({"at": self.clock.now, "payload": payload})
        if self.ack and payload.get("op") == 1:
            self.frames.insert(0, text_frame({"op": 11}))

    def settimeout(self, seconds):
        self.timeout = float(seconds)

    def close(self, code=1000):
        self.closed = code


def text_frame(obj):
    return ("text", json.dumps(obj))


def close_frame(code):
    return ("close", struct.pack("!H", int(code)))


def hello(interval_ms=60000):
    return text_frame({"op": 10, "d": {"heartbeat_interval": interval_ms}})


def ready(session="session-1", url="wss://gateway.example", user=BOT, seq=1):
    return text_frame({
        "op": 0, "t": "READY", "s": seq,
        "d": {
            "session_id": session,
            "resume_gateway_url": url,
            "user": {"id": user},
        },
    })


def resumed(seq=2):
    return text_frame({"op": 0, "t": "RESUMED", "s": seq, "d": {}})


def message_create(message_id, content="hello", user=USER, channel=CHANNEL,
                   guild=None, mentions=None, attachments=None, kind=0):
    body = {
        "id": message_id,
        "channel_id": channel,
        "type": kind,
        "content": content,
        "author": {"id": user},
    }
    if guild is not None:
        body["guild_id"] = guild
    if mentions is not None:
        body["mentions"] = mentions
    if attachments is not None:
        body["attachments"] = attachments
    return text_frame({"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": body})
