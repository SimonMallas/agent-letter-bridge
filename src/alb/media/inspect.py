"""Magic + bounded geometry. No decode, no render."""
import struct

MAX_BYTES = 10 * 1024 * 1024
MAX_EDGE_SUM = 10000
MAX_RATIO = 20
MAX_CAPTION = 1024

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"

_TYPES = {
    b"\xff\xd8\xff": JPEG,
    b"\x89PNG\r\n\x1a\n": PNG,
}


class MediaError(Exception):
    """Preflight or inbound inspection refused. Message is redacted."""


def detect(data):
    if not data:
        raise MediaError("empty image")
    if len(data) > MAX_BYTES:
        raise MediaError("image too large")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    raise MediaError("unsupported image type")


def dimensions(data, media_type):
    if media_type == PNG:
        return _png_size(data)
    if media_type == JPEG:
        return _jpeg_size(data)
    if media_type == WEBP:
        return _webp_size(data)
    raise MediaError("unsupported image type")


def preflight(data, caption=""):
    """Outbound checks. No claim/quota on failure."""
    if caption is None:
        caption = ""
    if not isinstance(caption, str):
        raise MediaError("caption refused")
    if len(caption) > MAX_CAPTION:
        raise MediaError("caption too long")
    media_type = detect(data)
    width, height = dimensions(data, media_type)
    if width <= 0 or height <= 0:
        raise MediaError("image geometry refused")
    if width + height > MAX_EDGE_SUM:
        raise MediaError("image geometry refused")
    ratio = max(width, height) / min(width, height)
    if ratio > MAX_RATIO:
        raise MediaError("image geometry refused")
    return media_type, width, height


def display_name(media_type):
    return {JPEG: "image.jpg", PNG: "image.png", WEBP: "image.webp"}[media_type]


def _png_size(data):
    if len(data) < 24:
        raise MediaError("image truncated")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data):
    i = 2
    n = len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            raise MediaError("image truncated")
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
            i += 2
            continue
        if i + 4 > n:
            break
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if seglen < 2:
            raise MediaError("image truncated")
        i += 2 + seglen
    raise MediaError("image truncated")


def _webp_size(data):
    if len(data) < 30:
        raise MediaError("image truncated")
    chunk = data[12:16]
    if chunk == b"VP8X":
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    if chunk == b"VP8 ":
        # lossy: 3-byte frame tag, then 0x9d 0x01 0x2a, then 16-bit w/h
        i = 20
        if data[i:i + 3] != b"\x9d\x01\x2a":
            i = data.find(b"\x9d\x01\x2a")
            if i < 0 or i + 7 > len(data):
                raise MediaError("image truncated")
        else:
            i = 20
        w = int.from_bytes(data[i + 3:i + 5], "little") & 0x3FFF
        h = int.from_bytes(data[i + 5:i + 7], "little") & 0x3FFF
        return w, h
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return w, h
    raise MediaError("image truncated")
