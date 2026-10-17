"""Discord outbound: the only place a Discord send outcome is classified.

The doctrine is the same on every platform:

  - a definite refusal means nothing was posted, so new text is safe
  - an ambiguous outcome means it may have been posted, so a retry could
    double-post - a human decides, never the code
  - a 429 is the one outcome that provably posted nothing, so it is the one
    that may be resumed

Discord does offer a nonce that suppresses a duplicate post. It is not used:
this module is not given the claim, and a nonce made from the text would
make Discord answer a second identical reply ("ok" to two letters) with the
first message - reported as sent, never posted.

`call(method, path, token, body) -> dict` performs one REST request and
`post_multipart(path, token, payload, files) -> dict` one upload. For a
non-2xx response either raises an exception carrying `.status` (and
`.retry_after` when Discord sent one); when the request may not have
completed it raises OSError; a 2xx it cannot read raises ValueError. The
token travels only as their argument, never inside a path or a body.
"""
import re

from alb.send import reply

# Discord refuses a bot's message over 2,000 characters. Counted in UTF-16
# units, which is never fewer than Discord's own count, so text this accepts
# is never refused for length after the claim.
MAX_TEXT = 2000

# Message flag: no link previews the agent did not ask for.
SUPPRESS_EMBEDS = 1 << 2

# Discord IDs are snowflakes: decimal, 17 to 20 digits.
_SNOWFLAKE = re.compile(r"[0-9]{17,20}")


def conversation_of(chat_id):
    """The channel half of a `<channel>/<user>` destination.

    The user half is who the allowlist admitted; Discord posts to a channel.
    Anything not shaped like a Discord pair is refused before a request
    exists, so a Telegram or Slack chat id can never reach Discord.
    """
    channel, sep, user = str(chat_id or "").partition("/")
    if (not sep or not _SNOWFLAKE.fullmatch(channel)
            or not _SNOWFLAKE.fullmatch(user)):
        raise reply.DefiniteRefusal("not a Discord conversation")
    return channel


def _place(chat_id, thread):
    """(channel to post in, message to reply to or None).

    `thread` is the poller's `<channel>:<message>`: where the letter was
    written - a thread under the allowlisted channel, or the channel itself -
    and the message it answers. It comes from the stored letter, never from
    the agent.
    """
    channel = conversation_of(chat_id)
    if not thread:
        return channel, None
    post_in, sep, message = str(thread).partition(":")
    if (not sep or not _SNOWFLAKE.fullmatch(post_in)
            or not _SNOWFLAKE.fullmatch(message)):
        raise reply.DefiniteRefusal("not a Discord reply place")
    return post_in, message


def _body(content, post_in, message):
    body = {
        "content": content,
        # Nothing the agent writes pings anyone: @everyone, @here, users and
        # roles all arrive as text. Discord enforces this, not an escaper.
        "allowed_mentions": {"parse": []},
        "flags": SUPPRESS_EMBEDS,
    }
    if message:
        body["message_reference"] = {"message_id": message,
                                     "channel_id": post_in,
                                     "fail_if_not_exists": False}
        # The person who wrote to the agent is told it answered.
        body["allowed_mentions"]["replied_user"] = True
    return body


def _check_length(text):
    if len(text.encode("utf-16-le")) // 2 > MAX_TEXT:
        raise reply.DefiniteRefusal("text longer than Discord accepts")


def _classified(post, *args):
    """One request, its outcome classified. Returns the posted message id."""
    try:
        payload = post(*args)
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
                raise reply.AmbiguousOutcome("unreadable response") from None
            # Unclassified: the caller's safety net records it as ambiguous.
            raise
        if status == 429:
            # Rate limits are applied before Discord acts on the request, so
            # nothing was posted - the one outcome a resume cannot double.
            raise reply.Throttled("throttled with HTTP 429",
                                  getattr(exc, "retry_after", None)) from None
        if status >= 500:
            raise reply.AmbiguousOutcome(f"server error HTTP {status}") from None
        code = getattr(exc, "code", None)
        detail = f" (code {code})" if isinstance(code, int) else ""
        raise reply.DefiniteRefusal(f"refused with HTTP {status}{detail}") from None
    message_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(message_id, str) or not _SNOWFLAKE.fullmatch(message_id):
        raise reply.AmbiguousOutcome("unreadable response")
    return message_id


def send(call, token, chat_id, text, thread=None):
    """Post plain text. Returns the message id."""
    post_in, message = _place(chat_id, thread)
    content = text or ""
    _check_length(content)
    return _classified(call, "POST", f"/channels/{post_in}/messages", token,
                       _body(content, post_in, message))


def send_photo(post_multipart, token, chat_id, data, caption="", thread=None):
    """Upload and post one image in a single request. Returns the message id.

    Unlike Slack's three steps, the upload is the post: there is no stage
    before which a failure is known to be invisible, so every outcome is
    classified exactly like a text send.
    """
    from alb.media import inspect as media_inspect

    post_in, message = _place(chat_id, thread)
    media_type, _width, _height = media_inspect.preflight(data, caption or "")
    filename = media_inspect.display_name(media_type)
    content = caption or ""
    _check_length(content)
    payload = _body(content, post_in, message)
    payload["attachments"] = [{"id": 0, "filename": filename}]
    return _classified(post_multipart, f"/channels/{post_in}/messages", token,
                       payload, [(filename, media_type, data)])


def _rest_call():
    # Resolved when a send happens, so importing this module needs no network
    # code - and so a test can stand in for the network at one seam.
    from alb.adapters.discord import rest
    return rest.call


def _rest_post_multipart():
    from alb.adapters.discord import rest
    return rest.post_multipart


class Sender:
    """Discord's sending half: what --reply-to, --send and --canary are given.

    Discord has one bot token, so unlike Slack there is no second credential
    to keep away from sending. The split is kept anyway: the receiving
    adapter has no send method, so the inbound process has no send path.
    """

    name = "discord"

    def __init__(self, token, *, call=None, post_multipart=None):
        self._token = token
        self._call = call
        self._post_multipart = post_multipart

    def __repr__(self):
        # The token must never reach a log line, an error message or a repr.
        return "<Discord sender>"

    def send(self, chat_id, text, thread=None):
        return send(self._call or _rest_call(), self._token, chat_id, text,
                    thread)

    def send_photo(self, chat_id, data, caption="", thread=None):
        return send_photo(self._post_multipart or _rest_post_multipart(),
                          self._token, chat_id, data, caption, thread)
