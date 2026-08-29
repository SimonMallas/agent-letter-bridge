"""Untrusted inbound poller.

Least privilege in the system. It may fetch from the platform, write a letter,
and acknowledge - nothing else. It may not ring, notify, execute or send, and
that incapability is proved against this source by test, not merely intended.

ORDER IS THE INVARIANT: letter to disk, THEN acknowledge. Acking an update
whose letter never landed loses the message permanently once the platform's
retention window passes. That is the single defect this project exists to fix.
"""
from allowlist import gate
from letter import store


class PlatformConflict(Exception):
    """Another consumer holds this token.

    A conflict is a YIELD, not an error: the losing consumer exits cleanly so
    the holder keeps running. Never fight for the token. Note that a clean exit
    under a restart-on-crash-only policy stays down by design - state that
    wherever this is used.
    """


def poll_once(platform, inbox, ledger, allowlist_path):
    """Fetch pending updates and durably record the permitted ones.

    Returns the ids of letters published, which may be fewer than the updates
    fetched: a denied sender produces silence, and a redelivered update
    produces nothing.
    """
    published = []
    for item in platform.fetch(offset=None):
        chat_id = item.get("chat_id")

        # Fail-closed. A denied sender produces no letter, no error and no
        # trace. Silence is the deny path succeeding.
        if not gate.allows(allowlist_path, chat_id):
            continue

        letter_id = store.publish_once(
            inbox,
            ledger,
            str(item["update_id"]),
            item.get("text", ""),
            {"chat_id": chat_id, "update_id": item["update_id"]},
        )
        if letter_id is not None:
            published.append(letter_id)

        # ONLY NOW. The letter exists on disk, so the platform may forget it.
        platform.ack(item["update_id"])

    return published
