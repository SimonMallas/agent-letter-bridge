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

from allowlist import gate
from letter import store


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


def _reply_id(letter_id, text):
    """Deterministic, so a replay or restart claims the same id and refuses."""
    digest = hashlib.sha256(f"{letter_id}\x00{text}".encode("utf-8")).hexdigest()
    return digest[:24]


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
    return path


def _write_atomic(path, data):
    """Every durable write in this project is tmp + replace. A torn record is
    a record nobody can trust at 3am."""
    tmp = pathlib.Path(f"{path}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
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


def send_reply(sender, inbox, state, allowlist_path, letter_id, text):
    """Reply to the chat named by a stored inbound letter. Nothing else."""
    # The destination is read from disk - never remembered, configured or inferred.
    stored = store.resolve(inbox, letter_id)
    chat_id = stored.meta.get("chat_id")

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
