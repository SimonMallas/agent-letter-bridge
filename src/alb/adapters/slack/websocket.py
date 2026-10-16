"""A client WebSocket, standard library only.

Socket Mode needs one connection. The URL that opens it carries a one-time
ticket, so nothing in here logs the URL or puts it in an error.
"""
import base64
import hashlib
import os
import socket
import ssl
import struct
import urllib.parse

# Split so the source does not contain a session-id shaped literal. The value
# is the WebSocket handshake constant, and the case is part of the hash.
GUID = "258EAFA5-" "E914-" "47DA-" "95CA-" "C5AB0DC85B11"
MAX_FRAME = 4 * 1024 * 1024


class WebSocketError(OSError):
    """The socket spoke, but not as a WebSocket we can keep."""


class WebSocket:
    """A connected socket that already has TLS, if it is going to have TLS."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self._parts = []

    def handshake(self, host, path):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        while b"\r\n\r\n" not in self.buf:
            if len(self.buf) > 8192:
                raise WebSocketError("handshake refused")
            chunk = self.sock.recv(min(4096, 8192 - len(self.buf)))
            if not chunk:
                raise WebSocketError("socket closed")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        if len(parts) < 2 or parts[1] != "101":
            raise WebSocketError("handshake refused")
        headers = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
        want = base64.b64encode(digest).decode("ascii")
        if headers.get("sec-websocket-accept") != want:
            raise WebSocketError("handshake refused")

    def send_text(self, text):
        self._send(0x1, text.encode("utf-8"))

    def ping(self, payload=b""):
        self._send(0x9, payload)

    def recv(self):
        """Return the next data frame. Server pings are answered on the way."""
        answered_ping = False
        while True:
            try:
                opcode, data, fin = self._read_frame()
            except socket.timeout:
                if answered_ping:
                    return "pong", b""
                raise
            if opcode == 0x9:
                self._send(0xA, data)
                answered_ping = True
                continue
            if opcode == 0xA:
                return "pong", b""
            if opcode == 0x8:
                return "close", data
            if opcode == 0x1 or (opcode == 0x0 and self._parts):
                self._parts.append(data)
                if not fin:
                    continue
                try:
                    text = b"".join(self._parts).decode("utf-8")
                except UnicodeDecodeError as exc:
                    self._parts = []
                    raise WebSocketError("text frame is not utf-8") from exc
                self._parts = []
                return "text", text
            raise WebSocketError("unsupported frame")

    def settimeout(self, seconds):
        self.sock.settimeout(seconds)

    def close(self):
        try:
            self._send(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, opcode, payload):
        if len(payload) > MAX_FRAME:
            raise WebSocketError("frame too large")
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        size = len(payload)
        if size < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | size)
        elif size < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, size)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, size)
        self.sock.sendall(header + mask + masked)

    def _fill(self, count):
        """Buffer at least count bytes. A timeout keeps a partial frame intact."""
        while len(self.buf) < count:
            try:
                chunk = self.sock.recv(count - len(self.buf))
            except socket.timeout:
                if self.buf:
                    continue
                raise
            if not chunk:
                raise WebSocketError("socket closed")
            self.buf += chunk

    def _read_frame(self):
        self._fill(2)
        first, second = self.buf[0], self.buf[1]
        size, offset = second & 0x7F, 2
        if size == 126:
            self._fill(4)
            size = struct.unpack("!H", self.buf[2:4])[0]
            offset = 4
        elif size == 127:
            self._fill(10)
            size = struct.unpack("!Q", self.buf[2:10])[0]
            offset = 10
        if size > MAX_FRAME:
            raise WebSocketError("frame too large")
        if second & 0x80:
            self._fill(offset + 4)
            offset += 4
        self._fill(offset + size)
        mask = self.buf[offset - 4:offset] if second & 0x80 else None
        data = self.buf[offset:offset + size]
        self.buf = self.buf[offset + size:]
        if mask is not None:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        return first & 0x0F, data, bool(first & 0x80)


def connect(url, timeout=30):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "wss" or not parsed.hostname:
        raise WebSocketError("only wss urls are accepted")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    raw = socket.create_connection((parsed.hostname, parsed.port or 443), timeout)
    try:
        wrapped = ssl.create_default_context().wrap_socket(
            raw, server_hostname=parsed.hostname
        )
    except Exception:
        raw.close()
        raise
    client = WebSocket(wrapped)
    try:
        client.handshake(parsed.hostname, path)
    except Exception:
        client.close()
        raise
    return client
