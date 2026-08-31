"""Bounded outbound: replies only, never originates.

This process holds the token, so it is a privilege boundary. Every route out is
a reply to a letter that already exists on disk, to the chat that letter names.
There is deliberately no function here that can start a conversation.
"""
import hashlib
import json
import os
import pathlib
import time

from alb.allowlist import gate
from alb.letter import store


class NotPermitted(Exception):
    """The destination is not on the allowlist at send time."""


class AlreadyClaimed(Exception):
    """This exact reply was already attempted. Refuse rather than double-post."""


class AmbiguousOutcome(Exception):
    """The send may or may not have arrived. NEVER auto-retried.

    The platform send has no idempotency key, so a retry cannot be made safe:
    if the first attempt succeeded and only the response was lost, retrying
    double-posts. A human decides.
    """


class DefiniteRefusal(Exception):
    """The platform definitely did not send it (for example a rate limit).

    Not ambiguous, so it does not dead-letter: the operator may fix the cause
    and send new text.
    """


# Where a letter names its destination, newest first. This list is the
# compatibility surface between the letter format and the send path, and it
# exists because they drifted: the routing envelope moved the field to
# telegram_chat_id while this module still read chat_id, so every reply to a
# real letter resolved to None and was denied by the allowlist. The tests did
# not catch it because they built letters by hand in the shape the poller had
# stopped writing.
DESTINATION_KEYS = ("telegram_chat_id", "chat_id")


def destination(meta):
    """The chat a reply goes to, read from the stored letter and nowhere else."""
    for key in DESTINATION_KEYS:
        value = meta.get(key)
        if value:
            return value
    return None


def _reply_id(letter_id, text):
    """Deterministic, so a replay or restart claims the same id and refuses."""
    digest = hashlib.sha256(f"{letter_id}\x00{text}".encode("utf-8")).hexdigest()
    return digest[:24]


def _fsync_dir(path):
    """Make a directory entry durable. Best-effort: a filesystem that refuses
    is a lost guarantee, not a lost send."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _claim(state, reply_id):
    """O_EXCL claim BEFORE the send. Exclusive creation is the whole mechanism:
    a second attempt cannot create the same file and so cannot send."""
    attempts = pathlib.Path(state) / "reply-attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    path = attempts / f"{reply_id}.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise AlreadyClaimed(f"{reply_id}: already attempted")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"reply_id": reply_id, "outcome": "in_flight",
                   "attempted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
        fh.flush()
        os.fsync(fh.fileno())

    # The claim's NAME must survive a crash, not just its contents. This claim
    # is the only thing standing between a replay and a double-post: if it is
    # lost while the send it recorded already happened, the retry posts again -
    # which is precisely what claiming before sending exists to prevent.
    _fsync_dir(path.parent)
    return path


def _write_atomic(path, data):
    """Every durable write in this project is tmp + replace. A torn record is
    a record nobody can trust at 3am."""
    tmp = pathlib.Path(f"{path}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data))
    os.replace(tmp, path)


def _record(path, outcome):
    data = json.loads(path.read_text(encoding="utf-8"))
    data["outcome"] = outcome
    _write_atomic(path, data)


def _dead_letter(state, reply_id, letter_id, detail):
    dead = pathlib.Path(state) / "dead-letters"
    dead.mkdir(parents=True, exist_ok=True)
    _write_atomic(dead / f"{reply_id}.json", {
        "reply_id": reply_id,
        "letter_id": letter_id,
        "outcome": "ambiguous",
        "detail": detail,
        "action_required": (
            "Open the chat. If the message is there: STOP, do not resend, leave "
            "these records. If it is absent: a human decides whether to send new "
            "text. Never delete this file."
        ),
    })


def send_reply(sender, inbox, state, allowlist_path, letter_id, text,
               searched=None):
    """Reply to the chat named by a stored inbound letter. Nothing else.

    `searched` must cover everywhere letters travel. An inbox is swept, and
    searching only the inbox means a reply to a filed letter fails as though it
    never existed - which an operator reads as the send path being broken.
    """
    # The destination is read from disk - never remembered, configured or inferred.
    stored = None
    for directory in (searched or [inbox]):
        try:
            stored = store.resolve(directory, letter_id)
            break
        except store.NoSuchLetter:
            continue
    if stored is None:
        raise store.NoSuchLetter(f"{letter_id}: no letter with this exact id")
    chat_id = destination(stored.meta)

    # Re-checked at send: the allowlist is enforced at BOTH ends.
    if not gate.allows(allowlist_path, chat_id):
        raise NotPermitted(f"destination not permitted at send time")

    claim = _claim(state, _reply_id(letter_id, text))
    try:
        sender.send(chat_id, text)
    except AmbiguousOutcome as exc:
        _record(claim, "ambiguous")
        _dead_letter(state, claim.stem, letter_id, str(exc))
        raise
    except DefiniteRefusal:
        _record(claim, "refused")
        raise
    except Exception as exc:
        # THE SAFETY NET. An adapter is contracted to raise AmbiguousOutcome or
        # DefiniteRefusal, but a bug in one is exactly when this matters: an
        # unclassified failure would otherwise escape with the claim burned
        # in_flight forever and nobody told. If an outcome escaped
        # classification then it is unknown by definition, and unknown means
        # ambiguous: record it and leave it for a human.
        _record(claim, "ambiguous")
        _dead_letter(state, claim.stem, letter_id,
                     f"unclassified {type(exc).__name__}: {exc}")
        raise AmbiguousOutcome(f"unclassified sender failure: {exc}") from exc
    _record(claim, "sent")
    return claim.stem
