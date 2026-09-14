"""The private message-id index: (platform, origin chat, message id) -> letter id.

Exact triple, exact match, both directions. Message ids are chat-scoped on
Telegram, so a bare id could resolve across two allowed chats to the wrong
letter - the same exact-destination class v0.1 protects everywhere else.
Private state under --root; letters never carry post-send platform facts.

Writers take an exclusive flock (same shape as correspondents.lock) and
publish through a unique temp name. Two processes recording different
triples must not drop an entry; a shared `.tmp` name made that a race.
"""
import contextlib
import fcntl
import json
import os
import pathlib
import secrets

_NAME = "message-index.json"
_LOCK = "message-index.lock"


def _key(platform, origin, message_id):
    return f"{platform}|{origin}|{message_id}"


def _load(state):
    try:
        return json.loads((pathlib.Path(state) / _NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _write_lock(state):
    path = pathlib.Path(state) / _LOCK
    pathlib.Path(state).mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def record(state, platform, origin, message_id, letter_id):
    """Idempotent exact write. First writer wins - an index entry is a fact
    about a message id the platform issued once; a second claim for the same
    triple is a bug upstream, kept out rather than overwritten."""
    if not message_id:
        return
    state = pathlib.Path(state)
    state.mkdir(parents=True, exist_ok=True)
    with _write_lock(state):
        table = _load(state)
        key = _key(platform, origin, message_id)
        if key in table:
            return
        table[key] = letter_id
        _write_private(state / _NAME, json.dumps(table))


def _write_private(path, text):
    """Private state is born 0600 - never mode-fixed after the fact.

    Unique temp so two writers cannot share a `.tmp` inode. The flock is
    what serialises the read-modify-write; the unique name is what keeps
    a crash from leaving a second writer pointing at a half-replaced file.
    """
    path = pathlib.Path(path)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.partial"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lookup(state, platform, origin, message_id):
    """Exact match or None. Never fuzzy, never cross-origin."""
    return _load(state).get(_key(platform, origin, message_id))
