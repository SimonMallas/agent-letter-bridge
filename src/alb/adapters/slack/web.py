"""Slack Web API, standard library only.

The token is a header. It is never placed in a URL, a body, or an error.
"""
import json
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    """Slack answered, and the answer was not a usable 200."""

    def __init__(self, method, status, retry_after=None):
        super().__init__(f"slack {method}: HTTP {status}")
        self.status = int(status)
        self.retry_after = retry_after


def _header(headers, name):
    for key, value in headers.items():
        if str(key).lower() == name:
            return value
    return None


def _retry_after(headers, body):
    raw = _header(headers, "retry-after")
    if raw is None and body:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed.get("retry_after")
    try:
        number = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request, timeout):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return exc.code, dict(exc.headers), payload


def _stream_opener(request, timeout):
    """Leave the body unread so the caller can stop at a size limit."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return exc.code, dict(exc.headers), payload
    return response.status, dict(response.headers), response


def _read_limited(body, max_bytes):
    """Read at most max_bytes + 1. One extra byte is enough to know it is over."""
    if isinstance(body, (bytes, bytearray)):
        if len(body) > max_bytes:
            raise ValueError("file too large")
        return bytes(body)
    chunks = []
    total = 0
    while total <= max_bytes:
        piece = body.read(min(64 * 1024, max_bytes + 1 - total))
        if not piece:
            break
        total += len(piece)
        if total > max_bytes:
            raise ValueError("file too large")
        chunks.append(piece)
    return b"".join(chunks)


def call(method, token, params=None, *, timeout=30, opener=None):
    """POST a form. `ok: false` is Slack's answer and is returned, not raised."""
    fields = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        fields[str(key)] = value if isinstance(value, str) else str(value)
    request = urllib.request.Request(
        "https://slack.com/api/" + method,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        method="POST",
    )
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    status, headers, body = (opener or _default_opener)(request, timeout)
    if status != 200:
        retry = _retry_after(headers, body) if status == 429 else None
        raise HttpError(method, status, retry)
    return json.loads(body.decode("utf-8"))


def _require_https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("refusing file url")
    return parsed


def get_bytes(url, token, max_bytes, *, opener=None):
    """Download one Slack file. Other hosts and redirects are refused."""
    parsed = _require_https(url)
    if parsed.hostname != "files.slack.com":
        raise ValueError("refusing file url")
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", "Bearer " + token)
    status, headers, body = (opener or _stream_opener)(request, 30)
    try:
        if status in (301, 302, 303, 307, 308):
            raise ValueError("redirect refused")
        if status != 200:
            raw = body if isinstance(body, (bytes, bytearray)) else _read_limited(
                body, 64 * 1024,
            )
            retry = _retry_after(headers, raw) if status == 429 else None
            raise HttpError("files", status, retry)
        length = _header(headers, "content-length")
        if length is not None:
            try:
                announced = int(str(length).strip())
            except ValueError:
                announced = None
            if announced is not None and announced > max_bytes:
                raise ValueError("file too large")
        return _read_limited(body, max_bytes)
    finally:
        close = getattr(body, "close", None)
        if callable(close) and not isinstance(body, (bytes, bytearray)):
            close()


def post_bytes(url, data, *, opener=None):
    """POST raw bytes somewhere https. No token is attached."""
    _require_https(url)
    request = urllib.request.Request(url, data=data, method="POST")
    status, _headers, _body = (opener or _default_opener)(request, 30)
    return status
