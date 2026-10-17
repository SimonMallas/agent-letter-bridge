"""Discord REST, standard library only.

The token is a header. It is never placed in a URL, a body, or an error.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://discord.com/api/v10"
_CDN_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_URL = "https://github.com/SimonMallas/agent-letter-bridge"
_VERSION = "0.5.0"
USER_AGENT = f"DiscordBot ({_URL}, {_VERSION})"


class HttpError(Exception):
    """Discord answered, and the answer was not a usable 200."""

    def __init__(self, status, retry_after=None, is_global=False, code=None):
        super().__init__(f"discord: HTTP {int(status)}")
        self.status = int(status)
        self.retry_after = retry_after
        self.is_global = bool(is_global)
        self.code = code


def _header(headers, name):
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return value
    return None


def _retry_after(headers, parsed):
    raw = None
    if isinstance(parsed, dict) and "retry_after" in parsed:
        raw = parsed.get("retry_after")
    if raw is None:
        raw = _header(headers, "retry-after")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _error_from(status, headers, body):
    parsed = None
    if body:
        try:
            parsed = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            parsed = None
    retry = _retry_after(headers, parsed) if status == 429 else None
    is_global = bool(isinstance(parsed, dict) and parsed.get("global") is True)
    code = parsed.get("code") if isinstance(parsed, dict) else None
    if not isinstance(code, int):
        code = None
    return HttpError(status, retry, is_global, code)


def _refuse_redirect(status):
    if status in (301, 302, 303, 307, 308):
        raise ValueError("redirect refused")


def _json_body(payload):
    try:
        return json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        raise ValueError("discord response is not json") from None


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
    except urllib.error.URLError as exc:
        raise OSError("discord network") from exc


def _stream_opener(request, timeout):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return exc.code, dict(exc.headers), payload
    except urllib.error.URLError as exc:
        raise OSError("discord network") from exc
    return response.status, dict(response.headers), response


def _read_limited(body, max_bytes):
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


def _api_url(path):
    if (not isinstance(path, str) or not path.startswith("/")
            or path.startswith("//") or "://" in path):
        raise ValueError("refusing discord path")
    return API + path


def _auth(request, token):
    request.add_header("Authorization", "Bot " + token)
    request.add_header("User-Agent", USER_AGENT)


def call(method, path, token, body=None, *, timeout=30, opener=None):
    """One Discord REST call. A 200 is JSON; anything else is HttpError."""
    request = urllib.request.Request(_api_url(path), method=method)
    _auth(request, token)
    if body is not None:
        request.data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    status, headers, payload = (opener or _default_opener)(request, timeout)
    _refuse_redirect(status)
    if status != 200:
        raw = payload if isinstance(payload, (bytes, bytearray)) else b""
        raise _error_from(status, headers, raw)
    if not payload:
        return {}
    parsed = _json_body(payload)
    if not isinstance(parsed, (dict, list)):
        raise ValueError("discord response is not json")
    return parsed


def get_bytes(url, max_bytes, *, opener=None):
    """Download one CDN object. No token is attached. Other hosts are refused."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.hostname not in _CDN_HOSTS):
        raise ValueError("refusing file url")
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    status, headers, body = (opener or _stream_opener)(request, 30)
    try:
        _refuse_redirect(status)
        if status != 200:
            raw = body if isinstance(body, (bytes, bytearray)) else _read_limited(
                body, 64 * 1024,
            )
            raise _error_from(status, headers, raw)
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


def post_multipart(path, token, payload, files, *, timeout=60, opener=None):
    """POST payload_json plus files[0], files[1], ..."""
    boundary = "alb" + os.urandom(8).hex()
    chunks = [_field(boundary, "payload_json", json.dumps(payload).encode("utf-8"),
                     "application/json")]
    for index, item in enumerate(files or ()):
        filename, content_type, data = item
        chunks.append(_file_field(
            boundary, f"files[{index}]", filename, content_type, data,
        ))
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(
        _api_url(path), data=b"".join(chunks), method="POST",
    )
    _auth(request, token)
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    status, headers, raw = (opener or _default_opener)(request, timeout)
    _refuse_redirect(status)
    if status != 200:
        body = raw if isinstance(raw, (bytes, bytearray)) else b""
        raise _error_from(status, headers, body)
    parsed = _json_body(raw)
    if not isinstance(parsed, dict):
        raise ValueError("discord response is not json")
    return parsed


def _field(boundary, name, data, content_type):
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("ascii")
    return head + data + b"\r\n"


def _file_field(boundary, name, filename, content_type, data):
    safe = str(filename).replace('"', "").replace("\r", "").replace("\n", "")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    raw = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    return head + raw + b"\r\n"
