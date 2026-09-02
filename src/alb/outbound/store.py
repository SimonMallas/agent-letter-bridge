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
    tmp.write_text(json.dumps(table, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return key


def compose(outbox, state, source_id, origin_chat, sender, body,
            platform="telegram"):
    """Write the outbound letter; its O_EXCL create is the claim.

    Returns the letter id. Raises AlreadyClaimed if a letter for this source
    already exists - the loser of a concurrent race fails HERE, before any
    orphan letter exists anywhere.
    """
    outbox = pathlib.Path(outbox)
    state = pathlib.Path(state)
    letter_id = f"reply-{source_id}"

    key = correspondent_key(state, origin_chat, platform)
    meta = letters.envelope(
        sender=sender, recipient="telegram-bridge",
        extra={"correspondent": key},
    )
    meta["id"] = letter_id
    meta["re"] = source_id
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


def _event_path(d, event):
    """Next sequence number for this letter's event dir. Split out so the
    collision path is testable: whatever computes the name, O_EXCL at the
    open is what makes history append-only under a race."""
    seq = len(list(d.iterdir())) + 1
    return d / f"{seq}-{event}.json"


TERMINAL = {"sent", "refused", "ambiguous", "dead"}


def reconcile(state):
    """Startup pass: classify every outbound letter's delivery state.

    in-flight (sending, no terminal) -> ambiguous: code cannot prove whether
    the syscall reached the platform. composed-only -> unsent (safe to
    compose the send again; the letter already exists and is the claim).
    Terminal -> clean, not reported.
    """
    receipts = pathlib.Path(state) / "receipts"
    verdicts = {}
    if not receipts.is_dir():
        return verdicts
    for d in receipts.iterdir():
        events = [p.name.split("-", 1)[1].removesuffix(".json")
                  for p in sorted(d.iterdir())]
        if any(e in TERMINAL for e in events):
            continue
        verdicts[d.name] = "ambiguous" if "sending" in events else "unsent"
    return verdicts


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
