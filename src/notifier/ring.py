"""Notifier: rings a uniquely identified surface after a letter exists.

The ring is an accelerator, never the message. It carries no letter content and
no letter identifier - only the fact that mail is waiting. The recipient reads
the letter from disk, which is the authoritative copy.

Injected text arrives at an agent as authoritative user input, so the payload is
a fixed, innocuous, operator-authored line. Anything richer is keyboard
prompt-injection.
"""
from letter import store

# Fixed and content-free. Never interpolate anything into this.
# Says what to do and names nothing the operator may not have. The earlier
# wording told a standalone user to check a "letterbox" - a different product
# ALB does not depend on and they may never have installed.
DOORBELL_LINE = "you have new mail: check your inbox directory"


class NoTargetSurface(Exception):
    """Identity uncertainty fails closed: no surface, no ring.

    Never guess at which pane looks right - that is how the wrong surface gets
    a knock, and it is the failure the identity work exists to prevent.
    """


def notify(transport, surface, inbox, letter_id):
    """Ring `surface` once a unique letter is confirmed on disk."""
    if not surface:
        raise NoTargetSurface("no registered surface; refusing to guess")

    # Confirm the letter exists BEFORE ringing. Rings accelerate; they never
    # announce something that does not exist. resolve() also refuses
    # path-shaped identifiers and inexact matches.
    store.resolve(inbox, letter_id)

    transport.deliver(surface, DOORBELL_LINE)
