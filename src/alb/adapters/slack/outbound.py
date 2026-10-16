"""Slack outbound: the only place a Slack send outcome is classified.

The doctrine is the Telegram adapter's, unchanged for a second platform:

  - a definite refusal means nothing was posted, so new text is safe
  - an ambiguous outcome means it may have been posted, so a retry could
    double-post - a human decides, never the code
  - a 429 is the one outcome that provably posted nothing, so it is the one
    that may be resumed

Slack's chat.postMessage has no idempotency key either. That is why
ambiguity is never resolved by trying again.

`call(method, token, params) -> dict` performs one Web API request. For a
non-200 response it raises an exception carrying `.status` (and
`.retry_after` when Slack sent one); when the request may not have
completed it raises OSError. The token travels only as `call`'s argument,
never inside params.
"""
import json
import re

from alb.send import reply

# chat.postMessage truncates anything longer, silently. A truncated message is
# a message the operator did not send, so refuse rather than let Slack cut it.
MAX_TEXT = 40000

# Slack's own failures, reported after it may already have acted. Every other
# ok:false is a validation refusal made before anything was posted.
AMBIGUOUS_ERRORS = frozenset({
    "internal_error", "fatal_error", "service_unavailable", "request_timeout",
})

# Public channel, DM, private channel. Nothing else is a place to post.
_CONVERSATION = re.compile(r"[CDG][A-Z0-9]+")
# An error code is snake_case. Anything else is not echoed.
_ERROR_CODE = re.compile(r"[a-z_]{1,64}")
_UPLOAD_PREFIX = "https://files.slack.com/"


def conversation_of(chat_id):
    """The conversation half of a `<conversation>/<member>` destination.

    The member half is who the allowlist admitted; Slack posts to a
    conversation. Anything not shaped like a Slack pair is refused before a
    request exists, so a Telegram chat id can never reach Slack.
    """
    conversation, sep, member = str(chat_id or "").partition("/")
    if not sep or not _CONVERSATION.fullmatch(conversation) or not member:
        raise reply.DefiniteRefusal("not a Slack conversation")
    return conversation


def escape(text):
    """Slack's three control characters, escaped so agent text is literal.

    `&` first, or the entities made for `<` and `>` would be escaped again.
    Escaped, `<!channel>` and `<@U…>` arrive as text rather than as a mass
    mention or a ping.
    """
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _error_code(payload):
    error = payload.get("error")
    if isinstance(error, str) and _ERROR_CODE.fullmatch(error):
        return error
    return "unknown"


def _post(call, method, token, params):
    """One request, its outcome classified. Returns the payload on ok:true."""
    try:
        payload = call(method, token, params)
    except Exception as exc:
        # The HTTP status first: an HTTP error may also be an OSError, and a
        # 429 classified as a network failure would dead-letter a message that
        # provably was not sent.
        status = getattr(exc, "status", None)
        if not isinstance(status, int):
            if isinstance(exc, OSError):
                # The request may have arrived and only the response been
                # lost. The textbook ambiguous case, and never retried.
                raise reply.AmbiguousOutcome(f"network failure: {exc}") from None
            if isinstance(exc, ValueError):
                # A 200 we could not read. Slack may have acted on it.
                raise reply.AmbiguousOutcome("unreadable response") from None
            # Unclassified: the caller's safety net records it as ambiguous.
            raise
        if status == 429:
            # Throttling happens before Slack reads the request, so nothing
            # was posted - the one outcome a resume cannot double.
            raise reply.Throttled("throttled with HTTP 429",
                                  getattr(exc, "retry_after", None)) from None
        if status >= 500:
            raise reply.AmbiguousOutcome(f"server error HTTP {status}") from None
        raise reply.DefiniteRefusal(f"refused with HTTP {status}") from None
    if not isinstance(payload, dict):
        raise reply.AmbiguousOutcome("unreadable response")
    if payload.get("ok") is True:
        return payload
    error = _error_code(payload)
    if error in AMBIGUOUS_ERRORS:
        raise reply.AmbiguousOutcome(f"slack reported {error}")
    if error == "ratelimited":
        raise reply.Throttled("throttled: ratelimited")
    raise reply.DefiniteRefusal(f"refused: {error}")


def send(call, bot_token, chat_id, text, thread=None):
    """Post plain text. Returns the message ts, which is Slack's message id."""
    conversation = conversation_of(chat_id)
    body = escape(text or "")
    # Measured after escaping: the escaped form is what Slack counts.
    if len(body) > MAX_TEXT:
        raise reply.DefiniteRefusal("text longer than Slack accepts")
    params = {
        "channel": conversation,
        "text": body,
        # Literal text: no *bold* or _italic_ from an agent's punctuation, and
        # no link previews it did not ask for.
        "mrkdwn": "false",
        "unfurl_links": "false",
        "unfurl_media": "false",
    }
    if thread:
        params["thread_ts"] = thread
    payload = _post(call, "chat.postMessage", bot_token, params)
    return str(payload.get("ts") or "")


def send_photo(call, post_bytes, bot_token, chat_id, data, caption="",
               thread=None):
    """Upload and share one image. Returns the Slack file id.

    Three steps, and only the last can post anything: an upload URL is
    requested, the bytes are sent to it, and only completing the upload shares
    the file into the conversation. Every failure before completion is
    therefore definite - nothing is visible - while completion is classified
    exactly like a text send.
    """
    from alb.media import inspect as media_inspect

    conversation = conversation_of(chat_id)
    media_type, _width, _height = media_inspect.preflight(data, caption or "")
    filename = media_inspect.display_name(media_type)

    try:
        ticket = _post(call, "files.getUploadURLExternal", bot_token,
                       {"filename": filename, "length": str(len(data))})
    except reply.AmbiguousOutcome as exc:
        raise reply.DefiniteRefusal(f"upload not started: {exc}") from None
    upload_url = ticket.get("upload_url")
    file_id = ticket.get("file_id")
    if (not isinstance(upload_url, str)
            or not upload_url.startswith(_UPLOAD_PREFIX)
            or not isinstance(file_id, str) or not file_id):
        # Bytes go only to Slack's own file host.
        raise reply.DefiniteRefusal("upload not offered")

    try:
        status = post_bytes(upload_url, data)
    except OSError as exc:
        raise reply.DefiniteRefusal(f"upload failed: {exc}") from None
    if status != 200:
        raise reply.DefiniteRefusal(f"upload refused with HTTP {status}")

    params = {
        "files": json.dumps([{"id": file_id, "title": filename}]),
        "channel_id": conversation,
    }
    if caption:
        params["initial_comment"] = escape(caption)
    if thread:
        params["thread_ts"] = thread
    _post(call, "files.completeUploadExternal", bot_token, params)
    return file_id
