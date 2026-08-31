"""Untrusted inbound poller.

Least privilege in the system. It may fetch from the platform, write a letter,
and acknowledge - nothing else. It may not ring, notify, execute or send, and
that incapability is proved against this source by test, not merely intended.

ORDER IS THE INVARIANT: letter to disk, THEN acknowledge. Acking an update
whose letter never landed loses the message permanently once the platform's
retention window passes. That is the single defect this project exists to fix.
"""
import json
import os
import pathlib
import time

from alb.allowlist import gate
from alb.letter import store


class PlatformConflict(Exception):
    """Another consumer holds this token.

    A conflict is a YIELD, not an error: the losing consumer exits cleanly so
    the holder keeps running. Never fight for the token. Note that a clean exit
    under a restart-on-crash-only policy stays down by design - state that
    wherever this is used.
    """


def _write_heartbeat(path):
    """Written after EVERY completed poll, busy or quiet.

    Freshness equals liveness: a supervisor judges this process from outside,
    without its cooperation. If only busy polls wrote it, a quiet bridge would
    read as a dead one.
    """
    path = pathlib.Path(path)
    tmp = path.with_suffix(".tmp")
    # Created 0600 rather than chmod'd afterwards, so the contents are never
    # briefly readable by anyone else.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"heartbeat": time.time()}))
    os.replace(tmp, path)


def poll_once(platform, inbox, ledger, allowlist_path, health_path=None,
              processed=None, sender="telegram-bridge", recipient="agent"):
    """Fetch pending updates and durably record the permitted ones.

    Returns the ids of letters published, which may be fewer than the updates
    fetched: a denied sender produces silence, and a redelivered update
    produces nothing.
    """
    # Letters travel: an inbox is swept. The durable dedup lookup must cover
    # everywhere they land, or a late redelivery republishes a letter that was
    # already handled.
    searched = [inbox] + ([processed] if processed else [])

    published = []
    for item in platform.fetch(offset=None):
        chat_id = item.get("chat_id")

        # Fail-closed. A denied sender produces no letter, no error and no
        # trace. Silence is the deny path succeeding.
        #
        # But a deny MUST still consume the update. The platform offset is a
        # single high-water mark, not a per-message acknowledgement: an update
        # left unacked stays at the head of the queue forever, so one stranger's
        # message would wedge the bridge and no permitted mail behind it would
        # ever arrive. Silence must not mean stuck.
        if gate.allows(allowlist_path, chat_id):
            letter_id = store.publish_once(
                inbox,
                ledger,
                str(item["update_id"]),
                item.get("text", ""),
                store.envelope(
                    sender=sender,
                    recipient=recipient,
                    extra={
                        "telegram_chat_id": chat_id,
                        "telegram_update_id": item["update_id"],
                    },
                ),
                searched=searched,
            )
            if letter_id is not None:
                published.append(letter_id)

        # ONLY NOW, and for denied updates too. For a permitted update the
        # letter is on disk, so the platform may forget it. For a denied one
        # there is deliberately nothing to keep.
        platform.ack(item["update_id"])

    # Only after a poll actually completed. A yielding or crashing poller must
    # not claim liveness on its way out - that would hide the handover.
    if health_path is not None:
        _write_heartbeat(health_path)

    return published
