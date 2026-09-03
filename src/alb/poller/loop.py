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
from alb import msgindex
from alb.outbound import store as outbound_store


class PlatformConflict(Exception):
    """Another consumer holds this token.

    A conflict is a YIELD, not an error: the losing consumer exits cleanly so
    the holder keeps running. Never fight for the token. Note that a clean exit
    under a restart-on-crash-only policy stays down by design - state that
    wherever this is used.
    """


def _thread_of(searched, letter_id):
    """The thread a stored letter belongs to - its own thread field, or
    itself when it roots one."""
    for directory in searched:
        try:
            stored = store.resolve(directory, letter_id)
            return stored.meta.get("thread") or letter_id
        except store.NoSuchLetter:
            continue
    return letter_id


def _current_thread(state, correspondent, letter_id, cut):
    """Per-correspondent thread pointer: this letter roots a new thread when
    the pointer is empty or /new cut it; otherwise the open thread continues."""
    path = pathlib.Path(state) / "threads.json"
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = {}
    if cut or correspondent not in table:
        table[correspondent] = letter_id
        tmp = pathlib.Path(str(path) + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(table))
        os.replace(tmp, path)
    return table[correspondent]


def _stamp_thread(inbox, letter_id, thread):
    """The one sanctioned rewrite: thread is stamped at PUBLISH time, before
    anyone has been told the letter exists - the letter is not yet anyone's
    record. After the doorbell, letters are never rewritten; this runs inside
    the same publish step, pre-ack, pre-ring."""
    path = pathlib.Path(inbox) / f"{letter_id}.md"
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line == "thread:" or line.startswith("thread: "):
            lines[i] = f"thread: {thread}"
            break
    else:
        return
    tmp = pathlib.Path(str(path) + ".tmp")
    # Born 0600 like every letter (grok's must-fix: a umask-governed write
    # here made the stamped letter 0644 - same class as the state files this
    # build already caught once).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, path)


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


class Cycle(list):
    """The published letter ids, plus what happened to everything else.

    A list subclass rather than a new return type: every caller already treats
    this as the letters published, and a report is not a reason to change what
    a cycle returns.

    The counts exist for the OPERATOR, and only the operator. A denied sender
    still gets silence - that is the security property and it is untouched.
    What changes is that the person at the terminal can tell a working deny
    from a dead bridge, because those look identical today and the only lever
    that looks relevant is the allowlist. An operator who cannot see the gate
    working eventually removes it.

    Counts are NUMBERS, never identities. A denied chat id would be a record of
    everyone who messaged the bot: a log the operator never asked for, about
    people who never consented to it.
    """

    fetched = 0
    published = 0
    denied = 0
    duplicate = 0


def poll_once(platform, inbox, ledger, allowlist_path, health_path=None,
              processed=None, sender="telegram-bridge", recipient="agent",
              state=None):
    """Fetch pending updates and durably record the permitted ones.

    Returns the ids of letters published, which may be fewer than the updates
    fetched: a denied sender produces silence, and a redelivered update
    produces nothing.
    """
    # Letters travel: an inbox is swept. The durable dedup lookup must cover
    # everywhere they land, or a late redelivery republishes a letter that was
    # already handled.
    searched = [inbox] + ([processed] if processed else [])
    # W2: correspondent identity, threading, and the message-id index all
    # live in private state. Derived from the ledger's home when not given,
    # so existing callers keep working and everything private stays together.
    state = pathlib.Path(state) if state else pathlib.Path(ledger).parent

    result = Cycle()
    for item in platform.fetch(offset=None):
        result.fetched += 1
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
            text = item.get("text", "")
            message_id = str(item.get("message_id", "") or "")
            extra = {
                "telegram_chat_id": chat_id,
                "telegram_update_id": item["update_id"],
            }
            if message_id:
                extra["telegram_message_id"] = message_id
            # Gate 0: the external principal is provenance, never a routable
            # participant. Stable opaque key, derived once, store-authoritative.
            extra["correspondent"] = outbound_store.correspondent_key(
                state, chat_id)

            # Platform reply-to resolves through the exact-triple index only.
            # A hit fills re: and joins THAT letter's thread; a miss changes
            # nothing - exact match or nothing, never fuzzy.
            reply_target = ""
            rt_mid = str(item.get("reply_to_message_id", "") or "")
            if rt_mid:
                reply_target = msgindex.lookup(state, "telegram", chat_id,
                                               rt_mid) or ""
            if reply_target:
                extra["re"] = reply_target
                extra["thread"] = _thread_of(searched, reply_target)

            letter_id = store.publish_once(
                inbox,
                ledger,
                str(item["update_id"]),
                text,
                store.envelope(
                    sender=sender,
                    recipient=recipient,
                    extra=extra,
                ),
                searched=searched,
            )
            if letter_id is not None:
                result.append(letter_id)
                result.published += 1
                if message_id:
                    msgindex.record(state, "telegram", chat_id, message_id,
                                    letter_id)
                if not reply_target:
                    # One open thread per correspondent, cut ONLY by /new at
                    # position zero (structure, never interpretation - the
                    # token stays in the stored body). A platform reply into
                    # an old thread deliberately does not move this pointer.
                    thread = _current_thread(
                        state, extra["correspondent"], letter_id,
                        cut=text.split(" ", 1)[0] == "/new" if text else False)
                    _stamp_thread(inbox, letter_id, thread)
                else:
                    _stamp_thread(inbox, letter_id, extra["thread"])
            else:
                # Already published, and dedup said so. Counted apart from a
                # deny: both publish nothing, but only one of them is the
                # allowlist, and an operator sent to the allowlist to explain a
                # number the allowlist did not cause will widen it for nothing.
                result.duplicate += 1
        else:
            result.denied += 1

        # ONLY NOW, and for denied updates too. For a permitted update the
        # letter is on disk, so the platform may forget it. For a denied one
        # there is deliberately nothing to keep.
        platform.ack(item["update_id"])

    # Only after a poll actually completed. A yielding or crashing poller must
    # not claim liveness on its way out - that would hide the handover.
    if health_path is not None:
        _write_heartbeat(health_path)

    return result
