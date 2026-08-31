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
    """A network condition, not a verdict about the token or the bridge.

    Connection resets and timeouts are ordinary on a long poll. Treating one as
    fatal kills the bridge for a condition that resolves itself; treating it as
    a conflict would hand the token away for no reason. It is neither - it is a
    thing to wait out.
    """


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
        tmp.write_text(json.dumps({"acked": self._acked}), encoding="utf-8")
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
            raise FetchFailed(f"confirm failed: HTTP {exc.code}") from None
        except OSError as exc:
            # The offset stays unpersisted, so the next start re-reads. Safe:
            # the ledger prevents duplicate letters.
            raise TransientFailure(f"network: {exc}") from None

    # -- outbound --------------------------------------------------------

    def send(self, chat_id, text):
        try:
            return _request(self._base, "sendMessage",
                            {"chat_id": chat_id, "text": text}, self._token)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                # The server may have accepted it before failing. Unknown.
                raise reply.AmbiguousOutcome(f"server error HTTP {exc.code}") from None
            # 4xx, including 429: the platform definitely did not send it.
            raise reply.DefiniteRefusal(f"refused with HTTP {exc.code}") from None
        except OSError as exc:
            # The POST may have arrived and only the response been lost. This
            # is the textbook ambiguous case and must never be auto-retried.
            # OSError for the same reason as fetch: a bare timeout is not a
            # URLError, and treating it as fatal would be worse than treating
            # it as unknown.
            raise reply.AmbiguousOutcome(f"network failure: {exc}") from None
