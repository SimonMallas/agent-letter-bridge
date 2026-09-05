"""Telegram Bot API adapter — the only place that talks to the platform.

Every platform-specific decision lives here so the core stays transport
neutral. In particular this is where an HTTP outcome is CLASSIFIED, and that
classification is the whole safety property of the send path:

  - a definite refusal means the message was not sent, so new text is safe
  - an ambiguous outcome means it may have been sent, so a retry could
    double-post - a human decides, never the code

Telegram's sendMessage has no idempotency key. That is why ambiguity cannot be
resolved by trying again.
"""
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

from alb.poller import loop
from alb.send import reply

class FetchFailed(Exception):
    """The fetch failed for a reason that is NOT a single-consumer conflict.

    Kept distinct deliberately. A conflict means another consumer holds the
    token and this process should yield; an auth or transport failure means
    something else entirely. Collapsing them sends an operator hunting a
    phantom second poller instead of reading the real error.
    """


class TransientFailure(Exception):
    """A condition to wait out, not a verdict about the token or the bridge.

    Connection resets and timeouts are ordinary on a long poll. Treating one as
    fatal kills the bridge for a condition that resolves itself; treating it as
    a conflict would hand the token away for no reason. It is neither - it is a
    thing to wait out.

    Rate limits and gateway errors belong here for the same reason, and did not
    used to: every non-409 status was fatal, so "you are polling too fast" and
    "your token is revoked" ended the process identically. One clears in
    seconds. A bridge died for thirty-six hours on the first.

    `retry_after` is the platform's own number when it sends one. It is a
    FLOOR, not the whole wait: backoff still applies on top, because a server
    telling us when to return does not tell us how many others it told the
    same thing.
    """

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(exc):
    """Seconds the platform asked us to wait, if it named a number.

    Read defensively: a missing, malformed or absurd header must degrade to
    plain backoff rather than raise inside the error path, because a crash
    while classifying an error loses the classification entirely.
    """
    try:
        raw = (exc.headers or {}).get("Retry-After")
    except Exception:  # noqa: BLE001 - never fail while handling a failure
        return None
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 < seconds <= 3600 else None


BASE = "https://api.telegram.org"
TIMEOUT = 30


def _request(base, method, params, token, timeout=TIMEOUT):
    """Perform one API call. Separated so tests never touch the network."""
    url = f"{base}/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class Telegram:
    """A single-consumer inbound reader and a bounded outbound sender."""

    def __init__(self, token, base=BASE, poll_timeout=25, offset_path=None):
        self._token = token
        self._base = base
        self._poll_timeout = poll_timeout
        self._offset_path = pathlib.Path(offset_path) if offset_path else None
        self._acked = self._load_offset()

    def _load_offset(self):
        """The high-water mark must OUTLIVE THE PROCESS.

        Kept only in memory, a restart re-reads everything the platform still
        retains: old messages are re-delivered, and a denied sender wedges the
        queue again on every start.
        """
        if not self._offset_path or not self._offset_path.is_file():
            return None
        try:
            return int(json.loads(self._offset_path.read_text(encoding="utf-8"))["acked"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            # An unreadable mark is not evidence of consumption. Start from
            # what the platform still holds: the delivered-ids ledger prevents
            # duplicate letters, so re-reading is safe, while wrongly assuming
            # consumption would lose messages.
            return None

    def _save_offset(self):
        if not self._offset_path or self._acked is None:
            return
        tmp = pathlib.Path(f"{self._offset_path}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"acked": self._acked}))
        os.replace(tmp, self._offset_path)

    def __repr__(self):
        # The token must never reach a log line, an error message or a repr.
        return "<Telegram bot>"

    # -- inbound ---------------------------------------------------------

    def fetch(self, offset=None):
        # The platform consumes everything at or below the mark, so the value
        # sent is the NEXT wanted id, not the last one seen.
        params = {"timeout": self._poll_timeout}
        if self._acked is not None:
            params["offset"] = self._acked + 1

        try:
            payload = _request(self._base, "getUpdates", params, self._token)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                # Another consumer holds this token. Yield; never fight for it.
                raise loop.PlatformConflict("another consumer holds this token") from None
            if exc.code == 429 or exc.code >= 500:
                # The platform asking for time, or failing at its own gateway.
                # Neither is a verdict about us and both clear on their own.
                raise TransientFailure(f"getUpdates deferred: HTTP {exc.code}",
                                       _retry_after(exc)) from None
            raise FetchFailed(f"getUpdates failed: HTTP {exc.code}") from None
        except OSError as exc:
            # OSError, not just URLError. A read timeout raised deep in the SSL
            # layer arrives as a bare TimeoutError and is NOT wrapped, so
            # classifying only URLError let it escape as a traceback and kill
            # the bridge - found by running the installed binary on a slow
            # network. URLError, TimeoutError and ConnectionError are all
            # OSError, and every one of them means the same thing here: the
            # network did not cooperate, wait and try again.
            raise TransientFailure(f"network: {exc}") from None

        updates = []
        for item in payload.get("result", []):
            message = item.get("message") or {}
            # Edits, reactions and channel posts arrive here too. NOT publishing
            # them is correct; DROPPING them is not. A dropped update is never
            # acked, so the high-water mark never passes it and it re-arrives on
            # every poll indefinitely - the same wedge a denied sender caused.
            #
            # They are surfaced with no chat, so the fail-closed allowlist
            # denies them and the poller consumes them without writing a letter.
            # Consuming is the other half of skipping.
            updates.append({
                "update_id": item["update_id"],
                "chat_id": str(message.get("chat", {}).get("id", "")) if message else "",
                "text": message.get("text", ""),
                # W2: the platform's chat-scoped message id, and the id of the
                # message this one replies to. Both feed the private
                # exact-triple index; neither is identity.
                "message_id": str(message.get("message_id", "") or ""),
                "reply_to_message_id": str(
                    (message.get("reply_to_message") or {}).get("message_id", "")
                    or ""),
            })
        return updates

    def ack(self, update_id):
        """Stage the high-water mark IN MEMORY ONLY.

        Deliberately not persisted here. Persisting a mark the platform has not
        accepted is silent loss: the file says consumed, the platform still
        holds the updates, and the next start transmits an offset that makes
        the platform skip messages nobody ever received.

        Recording, transmitting and persisting are three different things and
        must happen in that order. See confirm().
        """
        if self._acked is None or update_id > self._acked:
            self._acked = update_id

    def confirm(self):
        """Tell the platform what has been handled.

        A run that ends without this has consumed NOTHING, however many letters
        it wrote - the updates return on the next poll. Only safe to call once
        every letter in the batch is durably on disk, which is why the caller
        does it at the end of a cycle rather than the adapter doing it inline.
        """
        if self._acked is None:
            return
        try:
            _request(self._base, "getUpdates",
                     {"offset": self._acked + 1, "timeout": 0, "limit": 1}, self._token)
            # Persist ONLY what the platform has now accepted. A crash between
            # the platform accepting and this write is safe: the letters are
            # durable, and a stale local mark only re-reads, which the
            # delivered-ids ledger makes harmless.
            self._save_offset()
        except urllib.error.HTTPError as exc:
            # Classified exactly as in fetch(). Unclassified, a conflict here
            # escapes as a raw HTTPError and the process dies with a traceback
            # instead of the clean yield the conflict path exists to provide -
            # and the operator is told the wrong thing.
            if exc.code == 409:
                raise loop.PlatformConflict("another consumer holds this token") from None
            if exc.code == 429 or exc.code >= 500:
                raise TransientFailure(f"confirm deferred: HTTP {exc.code}",
                                       _retry_after(exc)) from None
            raise FetchFailed(f"confirm failed: HTTP {exc.code}") from None
        except OSError as exc:
            # The offset stays unpersisted, so the next start re-reads. Safe:
            # the ledger prevents duplicate letters.
            raise TransientFailure(f"network: {exc}") from None

    # -- outbound --------------------------------------------------------

    def send(self, chat_id, text):
        try:
            payload = _request(self._base, "sendMessage",
                               {"chat_id": chat_id, "text": text}, self._token)
            # The platform's message id, previously discarded. It feeds the
            # outbound delivery events and (W2) the reply-linkage index -
            # never the letter, which was durable before this call returned.
            return str(((payload or {}).get("result") or {}).get("message_id", ""))
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                # The server may have accepted it before failing. Unknown.
                raise reply.AmbiguousOutcome(f"server error HTTP {exc.code}") from None
            if exc.code == 429:
                # NOT a refusal. Throttling happens before the platform reads
                # the request, so the message provably was not sent - the one
                # case where a retry cannot double-post. Closing the letter
                # here recorded a temporary state as a permanent verdict.
                raise reply.Throttled(f"throttled with HTTP {exc.code}",
                                      _retry_after(exc)) from None
            # Other 4xx: the platform definitely did not send it, and would
            # not if asked again.
            raise reply.DefiniteRefusal(f"refused with HTTP {exc.code}") from None
        except OSError as exc:
            # The POST may have arrived and only the response been lost. This
            # is the textbook ambiguous case and must never be auto-retried.
            # OSError for the same reason as fetch: a bare timeout is not a
            # URLError, and treating it as fatal would be worse than treating
            # it as unknown.
            raise reply.AmbiguousOutcome(f"network failure: {exc}") from None
