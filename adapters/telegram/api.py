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
import urllib.error
import urllib.parse
import urllib.request

from poller import loop
from send import reply

class FetchFailed(Exception):
    """The fetch failed for a reason that is NOT a single-consumer conflict.

    Kept distinct deliberately. A conflict means another consumer holds the
    token and this process should yield; an auth or transport failure means
    something else entirely. Collapsing them sends an operator hunting a
    phantom second poller instead of reading the real error.
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

    def __init__(self, token, base=BASE, poll_timeout=25):
        self._token = token
        self._base = base
        self._poll_timeout = poll_timeout
        self._acked = None

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

        updates = []
        for item in payload.get("result", []):
            message = item.get("message")
            if not message:
                # Edits, reactions and channel posts arrive here too. Skipping
                # them is correct; crashing on them would wedge the queue.
                continue
            updates.append({
                "update_id": item["update_id"],
                "chat_id": str(message.get("chat", {}).get("id", "")),
                "text": message.get("text", ""),
            })
        return updates

    def ack(self, update_id):
        """Advance the local high-water mark. Sent on the next fetch."""
        if self._acked is None or update_id > self._acked:
            self._acked = update_id

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
        except urllib.error.URLError as exc:
            # The POST may have arrived and only the response been lost. This
            # is the textbook ambiguous case and must never be auto-retried.
            raise reply.AmbiguousOutcome(f"network failure: {exc.reason}") from None
