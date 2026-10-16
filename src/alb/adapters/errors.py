"""Failures shared by every platform adapter.

Telegram defined these first. Slack raises the same two types so the poller
can tell a wait from a refusal without importing a platform to do it.
"""


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
