"""Stdlib WebSocket client. Frames are reassembled locally; the URL is never logged."""
import socket
import struct
import sys
import pathlib
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.slack import websocket  # noqa: E402


def server_frame(opcode, payload, fin=True, length_form=None):
    """An unmasked server frame. length_form forces the 126 or 127 header."""
    first = (0x80 if fin else 0x00) | opcode
    size = len(payload)
    if length_form is None:
        length_form = 0 if size < 126 else 126 if size < 65536 else 127
    if length_form == 0:
        header = bytes((first, size))
    elif length_form == 126:
        header = struct.pack("!BBH", first, 126, size)
    else:
        header = struct.pack("!BBQ", first, 127, size)
    return header + payload


def unmask(frame):
    second = frame[1]
    if not second & 0x80:
        raise AssertionError("client frame was not masked")
    size = second & 0x7F
    offset = 2
    if size == 126:
        size = struct.unpack("!H", frame[2:4])[0]
        offset = 4
    elif size == 127:
        size = struct.unpack("!Q", frame[2:10])[0]
        offset = 10
    mask = frame[offset:offset + 4]
    data = frame[offset + 4:offset + 4 + size]
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))


class ScriptSocket:
    """Returns scripted bytes, honouring the size argument, then times out."""

    def __init__(self, data, chunks):
        self.data = data
        self.chunks = list(chunks)
        self.pos = 0
        self.sent = bytearray()

    def recv(self, size):
        if self.pos >= len(self.data):
            raise socket.timeout("quiet")
        take = self.chunks[0] if self.chunks else size
        if self.chunks:
            self.chunks.append(self.chunks.pop(0))
        take = min(take, size, len(self.data) - self.pos)
        out = self.data[self.pos:self.pos + take]
        self.pos += len(out)
        return out

    def sendall(self, data):
        self.sent += data

    def settimeout(self, seconds):
        self.timeout = seconds

    def close(self):
        self.closed = True


class WebSocketClient(unittest.TestCase):
    def _handshake_against(self, status, accept):
        client, server = socket.socketpair()
        client.settimeout(2)
        server.settimeout(2)
        seen = {}

        def respond():
            data = b""
            while b"\r\n\r\n" not in data:
                data += server.recv(4096)
            seen["request"] = data
            server.sendall(
                f"HTTP/1.1 {status}\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            websocket.WebSocket(client).handshake("socket.example", "/link")
        finally:
            thread.join(timeout=2)
            server.close()
            client.close()
        return seen

    def test_handshake_rejects_a_wrong_accept_key(self):
        with self.assertRaises(websocket.WebSocketError):
            self._handshake_against("101 Switching Protocols", "not-the-accept")

    def test_handshake_rejects_a_non_101_status(self):
        with self.assertRaises(websocket.WebSocketError):
            self._handshake_against("400 Bad Request", "anything")

    def test_frames_survive_split_reads_and_timeouts(self):
        small = b"hi"
        medium = b"A" * 200
        extended = b"B" * 180
        wide = b"C" * 2100
        blob = b"".join((
            server_frame(0x1, small),
            server_frame(0x1, medium, length_form=126),
            server_frame(0x1, extended, length_form=127),
            server_frame(0x1, b"hello ", fin=False),
            server_frame(0x0, b"world", fin=True),
            server_frame(0x1, wide, length_form=126),
        ))
        sock = ScriptSocket(blob, [1, 2, 7, 2000])
        client = websocket.WebSocket(sock)
        got = [client.recv() for _ in range(5)]
        self.assertEqual(got, [
            ("text", "hi"),
            ("text", medium.decode()),
            ("text", extended.decode()),
            ("text", "hello world"),
            ("text", wide.decode()),
        ])

    def test_server_ping_is_answered_with_a_masked_pong(self):
        payload = b"ping-body"
        sock = ScriptSocket(server_frame(0x9, payload), [3, 1, 8])
        client = websocket.WebSocket(sock)
        # Returned, not swallowed: the caller owns the deadline. Slack pings
        # every ten seconds, so a recv that answered pings and kept waiting
        # never reached a timeout shorter than the fetch window - the live
        # bridge sat in one recv for as long as Slack kept pinging.
        self.assertEqual(client.recv(), ("ping", payload))
        sent = bytes(sock.sent)
        self.assertEqual(sent[0], 0x8A)
        self.assertTrue(sent[1] & 0x80)
        self.assertEqual(unmask(sent), payload)

    def test_each_server_ping_returns_control_to_the_caller(self):
        stream = b"".join(server_frame(0x9, b"p%d" % n) for n in range(3))
        sock = ScriptSocket(stream, [len(stream)])
        client = websocket.WebSocket(sock)
        for n in range(3):
            self.assertEqual(client.recv(), ("ping", b"p%d" % n))

    def test_client_frames_are_masked(self):
        left, right = socket.socketpair()
        left.settimeout(2)
        right.settimeout(2)
        try:
            websocket.WebSocket(left).send_text("hello")
            raw = right.recv(64)
        finally:
            left.close()
            right.close()
        self.assertTrue(raw[1] & 0x80)
        self.assertEqual(unmask(raw), b"hello")

    def test_oversized_frame_is_refused(self):
        header = struct.pack("!BBQ", 0x81, 127, websocket.MAX_FRAME + 1)
        sock = ScriptSocket(header + b"BODY-NOT-FOR-US", [2, 8, 64])
        client = websocket.WebSocket(sock)
        with self.assertRaises(websocket.WebSocketError):
            client.recv()
        self.assertLessEqual(sock.pos, len(header))

    def test_connect_refuses_non_wss_urls(self):
        with mock.patch(
            "socket.create_connection", side_effect=AssertionError("opened")
        ):
            with self.assertRaises(websocket.WebSocketError):
                websocket.connect("ws://socket.example/link")
            with self.assertRaises(websocket.WebSocketError):
                websocket.connect("https://socket.example/link")

    def test_close_frame_is_reported(self):
        sock = ScriptSocket(server_frame(0x8, b"\x03\xe8"), [4])
        client = websocket.WebSocket(sock)
        self.assertEqual(client.recv(), ("close", b"\x03\xe8"))

    def test_partial_frame_timeout_propagates_and_resumes(self):
        """A half-open socket must not spin inside one recv.

        The bytes already read stay buffered, so the next recv finishes the
        same frame.
        """
        frame = server_frame(0x1, b"hello")

        class HalfOpen:
            def __init__(self):
                self.pos = 0
                self.timeouts = 0
                self.release = False
                self.sent = bytearray()

            def recv(self, size):
                if self.release:
                    out = frame[self.pos:self.pos + size]
                    self.pos += len(out)
                    return out
                if self.pos == 0:
                    self.pos = 1
                    return frame[:1]
                self.timeouts += 1
                if self.timeouts > 2:
                    raise AssertionError("partial frame retried a timeout")
                raise socket.timeout("half open")

            def sendall(self, data):
                self.sent += data

            def settimeout(self, seconds):
                pass

        sock = HalfOpen()
        client = websocket.WebSocket(sock)
        with self.assertRaises(socket.timeout):
            client.recv()
        self.assertEqual(sock.timeouts, 1)
        sock.release = True
        self.assertEqual(client.recv(), ("text", "hello"))
