"""Bounded outbound: replies only, never originates.

This process holds the token, so it is a privilege boundary. Every route out is
a reply to a letter that already exists on disk, to the chat that letter names.
There is deliberately no function here that can start a conversation.
"""
import fcntl
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
    """The platform definitely did not send it, and will not if asked again.

    Not ambiguous, so it does not dead-letter: the operator may fix the cause
    and send new text. A rate limit is NOT one of these - see Throttled.
    """


class TextChanged(Exception):
    """Asked to resume with different text than the letter carries.

    Retyping is the resume gesture, so an operator may retype something else -
    and the letter that exists is the one composed on the first attempt.
    Sending the old body under a new instruction is safe and dishonest: the
    operator reads success and the recipient gets something they were not
    shown. Refusing is the only honest answer, because the composed letter is
    immutable by design and cannot be quietly rewritten to match.
    """


class NotDeferred(Exception):
    """Asked to resume a letter that is not waiting on a throttle.

    Resume exists to finish ONE interrupted attempt, not to re-send whatever
    it is pointed at. A letter that already reached a terminal state, or that
    was never throttled, must refuse here - a resume that re-sends a delivered
    message is the double-send this whole design is built to prevent.
    """


class Throttled(Exception):
    """The platform refused to look at it yet. Retryable, uniquely.

    A 429 is pre-processing: the message provably was not sent, so a retry
    cannot double-post. That makes it the one send outcome the never-retry
    doctrine does not cover - the doctrine refuses AmbiguousOutcome because a
    retry might duplicate something already accepted, and nothing was accepted
    here.

    Recording this as a DefiniteRefusal wrote a temporary state into a durable
    record as a final verdict, which is wrong in a way that looks right: the
    message genuinely was not sent, so the outcome reads as correct while the
    letter is closed against a condition that would have cleared itself.
    """

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


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
               searched=None, outbox=None, agent="agent"):
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

    # v0.2 W1: the outbound LETTER is written first and its O_EXCL create IS
    # the claim - one logical reply per source letter, established in the same
    # syscall that makes the reply durable. The legacy body-hash claim held a
    # weaker promise (it deduplicated identical text, not logical replies).
    # Function-level import: outbound imports this module for the exception
    # types, so a module-level import here would be a cycle.
    from alb.outbound import store as outbound

    if outbox is None:
        # Callers that predate slice 2 (and the tests that pin them) keep the
        # legacy claim path until the wiring lands everywhere; new callers
        # pass outbox and get the letter-first path.
        return _send_legacy(sender, state, letter_id, chat_id, text)

    out_id = outbound.compose(pathlib.Path(outbox), pathlib.Path(state),
                              source_id=letter_id, origin_chat=chat_id,
                              sender=agent, body=text,
                              thread=stored.meta.get("thread", ""))
    outbound.record_event(state, out_id, "sending")
    try:
        platform_id = sender.send(chat_id, text)
    except AmbiguousOutcome as exc:
        outbound.record_event(state, out_id, "ambiguous", detail=str(exc))
        _dead_letter(state, out_id, letter_id, str(exc))
        raise
    except DefiniteRefusal as exc:
        outbound.record_event(state, out_id, "refused", detail=str(exc))
        raise
    except Throttled as exc:
        # NOT terminal and NOT ambiguous - the only failure whose outcome we
        # know. The platform never read the request, so nothing was delivered
        # and there is nothing to dead-letter. The outbound letter stays where
        # it is: it IS the claim, and releasing it here would let a second
        # composer pick up the same source mid-wait and post twice.
        outbound.record_event(state, out_id, "throttled", detail=str(exc))
        raise
    except Exception as exc:
        # THE SAFETY NET, unchanged in spirit: an outcome that escaped
        # classification is unknown, and unknown means ambiguous - record it
        # and leave it for a human. Never auto-retry.
        outbound.record_event(state, out_id, "ambiguous",
                              detail=f"unclassified {type(exc).__name__}: {exc}")
        _dead_letter(state, out_id, letter_id,
                     f"unclassified {type(exc).__name__}: {exc}")
        raise AmbiguousOutcome(f"unclassified sender failure: {exc}") from exc
    # Platform acceptance is never described as human receipt. The message id
    # the platform returns lives HERE, in the events - never on the letter,
    # which was written before the platform knew anything.
    outbound.record_event(state, out_id, "sent",
                          platform_message_id=str(platform_id))
    # Both directions in the index: a phone reply to the bot's own message
    # must resolve to the outbound letter it answers.
    from alb import msgindex
    msgindex.record(state, "telegram", chat_id, str(platform_id), out_id)
    return out_id


def resume_throttled(sender, inbox, state, allowlist_path, out_id, outbox,
                     searched=None, text=None):
    """Make ONE further attempt on an outbound letter left waiting by a 429.

    The letter is not re-composed and the claim is not released: this reuses
    the immutable letter that already exists, which is the only way a retry
    can be safe. Composing again would raise AlreadyClaimed - correctly - and
    releasing the claim first would open the window where a second composer
    posts the duplicate the never-retry doctrine exists to prevent.

    Refuses anything not actually deferred. The state is read from the events,
    not from the caller's belief about them.
    """
    # BEFORE the filesystem is touched. The lock path was built from raw
    # caller text and opened before anything validated it, so "../escaped"
    # created a file outside the locks directory - a public library call able
    # to write wherever the process can. A refusal raised after the write is
    # not a refusal, it is a report of something that already happened.
    store._check_id(out_id)
    state = pathlib.Path(state)
    # The check and the send are two steps, so the deferred state has to be
    # CONSUMED exclusively rather than merely observed: two resumers can both
    # read "throttled" before either writes "sending", and both then send. An
    # advisory lock held across the whole transition is what makes crossing
    # the send boundary singular; O_EXCL on each event file makes history
    # append-only, which is a different property and does not help here. The
    # lock is flock, so a crash releases it rather than stranding the letter.
    locks = state / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with open(locks / f"{out_id}.resume", "a+") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise NotDeferred(f"{out_id}: another resumer holds this letter") from None
        return _resume_locked(sender, inbox, state, allowlist_path, out_id,
                              outbox, searched, text)


def _resume_locked(sender, inbox, state, allowlist_path, out_id, outbox,
                   searched, text):
    # Function-level import for the same cycle reason as send_reply.
    from alb.outbound import store as outbound

    if outbound.reconcile(state).get(out_id) != "throttled":
        raise NotDeferred(f"{out_id}: not waiting on a throttle")
    letter = store.resolve(pathlib.Path(outbox), out_id)
    # The outbound letter stores a HASHED correspondent, never the raw chat -
    # that is Gate 0, and it is why the destination cannot be recovered from
    # the outbound letter alone. So resume reads it the same way the first
    # attempt did: from the stored SOURCE letter this reply answers, named by
    # the envelope's `re`. A resume therefore cannot reach a chat the original
    # letter did not name.
    source_id = letter.meta.get("re", "")
    if not source_id:
        raise NotDeferred(f"{out_id}: the letter names no source to answer")
    # Searched the same way the first attempt searched, and for the same
    # reason: a sweep files the inbound letter to processed, and a resume that
    # only knows the inbox stops working the moment anybody tidies their mail.
    source = None
    for directory in (searched or [inbox]):
        try:
            source = store.resolve(directory, source_id)
            break
        except store.NoSuchLetter:
            continue
    if source is None:
        raise store.NoSuchLetter(f"{source_id}: no letter with this exact id")
    if text is not None and text != letter.body:
        raise TextChanged(
            f"{out_id}: the composed reply says something else. The letter is "
            f"immutable, so this would send the original text under your new "
            f"instruction. Resume with the original words, or wait for this "
            f"one to reach a terminal state before composing another.")
    chat_id = destination(source.meta)
    if not chat_id:
        raise NotPermitted(f"{source_id}: the letter names no destination")
    # Gated again, deliberately: an allowlist can change between the first
    # attempt and the resume, and the later send is a NEW reach for the
    # platform however old the claim is.
    if not gate.allows(allowlist_path, chat_id):
        raise NotPermitted("destination not permitted at resume time")
    outbound.record_event(state, out_id, "sending")
    try:
        platform_id = sender.send(chat_id, letter.body)
    except AmbiguousOutcome as exc:
        outbound.record_event(state, out_id, "ambiguous", detail=str(exc))
        # The SOURCE id, not the outbound id twice: an operator chasing a
        # dead-letter needs the letter it answers, which is the one they can
        # actually read.
        _dead_letter(state, out_id, source_id, str(exc))
        raise
    except DefiniteRefusal as exc:
        outbound.record_event(state, out_id, "refused", detail=str(exc))
        raise
    except Throttled as exc:
        outbound.record_event(state, out_id, "throttled", detail=str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 - same safety net as the first attempt
        outbound.record_event(state, out_id, "ambiguous",
                              detail=f"unclassified {type(exc).__name__}: {exc}")
        _dead_letter(state, out_id, source_id,
                     f"unclassified {type(exc).__name__}: {exc}")
        raise AmbiguousOutcome(f"unclassified sender failure: {exc}") from exc
    outbound.record_event(state, out_id, "sent",
                          platform_message_id=str(platform_id))
    from alb import msgindex
    msgindex.record(state, "telegram", chat_id, str(platform_id), out_id)
    return out_id


def _send_legacy(sender, state, letter_id, chat_id, text):
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
    except Throttled:
        # Same reasoning as the letter-first path: provably undelivered, so
        # the claim holds and nothing is dead-lettered.
        _record(claim, "throttled")
        raise
    except Exception as exc:
        _record(claim, "ambiguous")
        _dead_letter(state, claim.stem, letter_id,
                     f"unclassified {type(exc).__name__}: {exc}")
        raise AmbiguousOutcome(f"unclassified sender failure: {exc}") from exc
    _record(claim, "sent")
    return claim.stem
