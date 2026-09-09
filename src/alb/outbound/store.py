"""The outbound letter, whose creation IS the claim.

v0.2 W1, first slice, per the approved spec. One logical reply per source
letter: the outbound letter id is deterministic from the source id, and its
O_EXCL create in outbox/ is the claim - a concurrent second composer fails at
create, before any orphan exists. Delivery outcomes are immutable event FILES
under the private state root; the letter is never rewritten after creation,
because a pre-send letter cannot record a post-send fact.
"""
import hashlib
import json
import os
import pathlib
import time

from alb.letter import store as letters
from alb.send.reply import AlreadyClaimed


def correspondent_key(state, origin_chat, platform="telegram"):
    """Stable opaque origin key: derived once, stored, store authoritative.

    SHA-256 of "<platform>:<chat id>" truncated to 16 hex. The stored value
    wins forever after, so the key survives any later change of derivation
    scheme; cross-install stability comes from the rule, ongoing identity
    from the store. Aliases, when they exist, are presentation - never this.
    """
    state = pathlib.Path(state)
    path = state / "correspondents.json"
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = {}
    origin = f"{platform}:{origin_chat}"
    if origin in table:
        return table[origin]
    key = hashlib.sha256(origin.encode()).hexdigest()[:16]
    table[origin] = key
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(table, indent=2))
    os.replace(tmp, path)
    return key


def compose(outbox, state, source_id, origin_chat, sender, body,
            platform="telegram", thread=""):
    """Write the outbound letter; its O_EXCL create is the claim.

    Returns the letter id. Raises AlreadyClaimed if a letter for this source
    already exists - the loser of a concurrent race fails HERE, before any
    orphan letter exists anywhere.
    """
    outbox = pathlib.Path(outbox)
    # outbox/ is one of the three directories that are ours to add inside a
    # mailbox (inbox, processed, outbox - same rule as prepare_mail_root, same
    # no-chmod respect for a directory that may not be ours). The reply path
    # can run before any poll cycle has prepared anything.
    outbox.mkdir(parents=True, exist_ok=True)
    state = pathlib.Path(state)
    letter_id = f"reply-{source_id}"

    key = correspondent_key(state, origin_chat, platform)
    meta = letters.envelope(
        sender=sender, recipient="telegram-bridge",
        extra={"correspondent": key},
    )
    meta["id"] = letter_id
    meta["re"] = source_id
    # The reply lives in the source's thread (or the source roots one).
    meta["thread"] = thread or source_id
    text = letters._serialise(meta, body)

    path = outbox / f"{letter_id}.md"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise AlreadyClaimed(f"{letter_id}: already composed for {source_id}")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(outbox)

    record_event(state, letter_id, "composed")
    return letter_id


def record_event(state, letter_id, event, **fields):
    """One immutable file per transition; history is append-only even when
    the caller stutters - a repeated transition is a new numbered file, never
    a rewrite."""
    d = pathlib.Path(state) / "receipts" / letter_id
    d.mkdir(parents=True, exist_ok=True)
    payload = {"event": event, "at": time.time(), **fields}
    path = _event_path(d, event)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_dir(d)
    return path


def _seq(path):
    """The numeric sequence a receipt file was written with.

    Read as a NUMBER, never as text: names are written 1.. with no padding,
    so "10-sending" sorts before "2-sending" alphabetically and the tail of a
    sorted listing stops being the latest event exactly when a letter has been
    retried enough times to matter. Anything unparsable sorts first, so a
    stray file cannot masquerade as the current state.
    """
    try:
        return int(path.name.split("-", 1)[0])
    except (ValueError, IndexError):
        return -1


def _history(d):
    """This letter's events, oldest first, in the order they happened.

    Malformed entries are SKIPPED rather than split blindly: the sort key
    already tolerates them, and splitting the same names unconditionally
    raised IndexError on anything without a dash. A stray file in a receipts
    directory is a thing that happens - a half-written copy, an editor's
    backup - and it must not take down the startup pass that every letter's
    fate depends on.
    """
    events = []
    for path in sorted(d.iterdir(), key=_seq):
        _, _, rest = path.name.partition("-")
        event = rest.removesuffix(".json")
        # An empty event name is malformed too: "3-.json" carries a dash and a
        # number, so it passed a filter that only asked those two questions,
        # and an empty string then sat in the history as the current state -
        # masking a deferred letter, because "" is in no state set at all.
        if not event or _seq(path) < 0:
            continue
        events.append(event)
    return events


def _event_path(d, event):
    """Next sequence number for this letter's event dir. Split out so the
    collision path is testable: whatever computes the name, O_EXCL at the
    open is what makes history append-only under a race."""
    seq = len(list(d.iterdir())) + 1
    return d / f"{seq}-{event}.json"


TERMINAL = {"sent", "refused", "ambiguous", "dead"}

# Not terminal, and deliberately not ambiguous either: a throttle is the one
# in-flight state whose outcome is KNOWN. The platform declined to read the
# request, so nothing was delivered and there is nothing to be uncertain
# about. Treating it as ambiguous - which is what "sending with no terminal
# event" means everywhere else - would dead-letter it on the next restart and
# manufacture the very outcome the throttle path exists to prevent.
DEFERRED = {"throttled"}


def reconcile(state, outbox=None):
    """Startup pass: classify every outbound letter's delivery state.

    in-flight (sending, no terminal) -> ambiguous: code cannot prove whether
    the syscall reached the platform. composed-only -> unsent: the letter
    already exists and is the claim, but the CLI cannot finish it (a retype
    meets AlreadyClaimed) - recovering an unsent claim is Piece A's work.
    Terminal -> clean, not reported.

    When ``outbox`` is given, an orphan claim - an outbound letter with no
    receipts yet, left by a crash between its O_EXCL create and its first
    recorded event - is also reported as unsent, so that window is not
    invisible to the classifier. A stem is an orphan only when it has NO
    receipts at all: a terminal letter is kept out of `verdicts` on purpose,
    so its absence must NOT be read as "no receipts" (that would stamp a
    delivered letter unsent).

    This is a library primitive - reporting a claim unsent makes it VISIBLE to
    a caller, not sendable: the CLI still cannot finish an unsent claim (a
    retype meets AlreadyClaimed). Surfacing orphans to the operator at startup,
    and completing one, are Piece A's work; the startup pass does NOT pass an
    outbox here.
    """
    receipts = pathlib.Path(state) / "receipts"
    verdicts = {}
    # An absent receipts dir is not an early return any more: the outbox walk
    # below still has to run, so the first-ever letter that crashed before its
    # first receipt is not missed. The loop body is unchanged.
    for d in (receipts.iterdir() if receipts.is_dir() else []):
        events = _history(d)
        if any(e in TERMINAL for e in events):
            continue
        if events and events[-1] in DEFERRED:
            # Ordering is by the event files' numbered names, so the LAST
            # event is the current state: a throttle followed by another
            # attempt is no longer deferred, and only the tail can say so.
            verdicts[d.name] = "throttled"
            continue
        verdicts[d.name] = "ambiguous" if "sending" in events else "unsent"
    # The outbound letter is the claim,
    # created and fsynced BEFORE its first receipt. A crash in that window
    # leaves a letter with no receipts dir - invisible to the walk above. No
    # receipt means record_event never ran, so nothing was sent: the claim is
    # unsent, the same verdict a composed-only letter earns. Only a stem with
    # NO receipts at all is added (see the guard below): a terminal letter's
    # deliberate absence from the verdict is not read as missing receipts.
    if outbox is not None:
        outbox = pathlib.Path(outbox)
        if outbox.is_dir():
            for f in outbox.iterdir():
                # An ordinary .md file only: a directory or a symlink named
                # "*.md" is not a claim.
                if f.suffix != ".md" or f.is_symlink() or not f.is_file():
                    continue
                # A stem with ANY receipts dir is already represented - by its
                # verdict, or by the deliberate terminal EXCLUSION that keeps a
                # sent/refused/ambiguous/dead letter OUT of `verdicts`. Only a
                # stem with no receipts at all is a pre-receipt orphan:
                # absence from `verdicts` is not absence of receipts.
                if (receipts / f.stem).exists():
                    continue
                verdicts[f.stem] = "unsent"
    return verdicts


def reconcile_at_startup(state):
    """The bridge's first act on rising (grok's flag: reconcile existed and
    was never called). Every in-flight outbound letter dead-letters for a
    human and gains a terminal 'dead' event, making the pass idempotent -
    the next restart has nothing to re-flag. Composed-only (unsent) letters are
    left alone: the claim is preserved but the CLI cannot finish it (a retype
    meets AlreadyClaimed) - recovering one is Piece A's work, nobody's
    emergency here."""
    state = pathlib.Path(state)
    flagged = []
    for letter_id, verdict in reconcile(state).items():
        if verdict != "ambiguous":
            continue
        dead = state / "dead-letters"
        dead.mkdir(parents=True, exist_ok=True)
        payload = {
            "reply_id": letter_id, "letter_id": letter_id,
            "outcome": "ambiguous",
            "detail": ("in-flight at restart: a send was started and no outcome "
                       "was recorded before the process died. The platform may "
                       "or may not have delivered it."),
            "action_required": (
                "Open the chat. If the message is there: STOP, do not resend, "
                "leave these records. If it is absent: a human decides whether "
                "to send new text. Never delete this file."),
        }
        tmp = dead / f"{letter_id}.json.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, dead / f"{letter_id}.json")
        record_event(state, letter_id, "dead", detail="reconciled at restart")
        flagged.append(letter_id)
    return flagged


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
