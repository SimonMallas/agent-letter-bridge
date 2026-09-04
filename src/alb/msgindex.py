"""The private message-id index: (platform, origin chat, message id) -> letter id.

Exact triple, exact match, both directions. Message ids are chat-scoped on
Telegram, so a bare id could resolve across two allowed chats to the wrong
letter - the same exact-destination class v0.1 protects everywhere else.
Private state under --root; letters never carry post-send platform facts.
"""
import json
import os
import pathlib

_NAME = "message-index.json"


def _key(platform, origin, message_id):
    return f"{platform}|{origin}|{message_id}"


def _load(state):
    try:
        return json.loads((pathlib.Path(state) / _NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(state, platform, origin, message_id, letter_id):
    """Idempotent exact write. First writer wins - an index entry is a fact
    about a message id the platform issued once; a second claim for the same
    triple is a bug upstream, kept out rather than overwritten."""
    if not message_id:
        return
    state = pathlib.Path(state)
    state.mkdir(parents=True, exist_ok=True)
    table = _load(state)
    key = _key(platform, origin, message_id)
    if key in table:
        return
    table[key] = letter_id
    _write_private(state / _NAME, json.dumps(table))


def _write_private(path, text):
    """Private state is born 0600 - never mode-fixed after the fact."""
    tmp = pathlib.Path(f"{path}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def lookup(state, platform, origin, message_id):
    """Exact match or None. Never fuzzy, never cross-origin."""
    return _load(state).get(_key(platform, origin, message_id))
