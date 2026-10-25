"""Which bridge answers which letter, for `alb --reply-to <id>` without flags.

Agents that were handed their bridge's --config and --root at setup answer a
person with `alb --reply-to`. An agent that was never handed them tried
`letterbox reply`, which cannot reach a phone, and the answer stayed in its
pane (live test, 25 Oct). So each running bridge records where it lives here,
and --reply-to finds the one whose mailbox holds the letter.

Trust boundary. This file is per-user configuration, written only by a bridge
that has loaded its own config. A letter never contributes a path or a
command: its id is a lookup key, the letter must name this bridge's own
sender and recipient, and the bridge's private message index must show it
published that letter. The reply then goes only to the chat that index
recorded. That does not authenticate who wrote the words. A
same-user agent that elects to run --reply-to can send one answer to the
correspondent of a validated, registered letter - exactly what it could do
with explicit flags.
"""
import errno
import fcntl
import json
import os
import pathlib
import re
import secrets
import stat

from alb import msgindex
from alb.letter import store

FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
MAX_LETTER = 1 << 20
ENTRY_KEYS = ("root", "config", "mail_root", "platform", "from", "to")


class RegistryError(Exception):
    """Refused. Messages name labels, never a root or config path: an agent
    can run --reply-to with any id it read, and must not get a map of where
    tokens live in return."""


def directory(environ=None):
    """XDG_CONFIG_HOME when it is absolute, else ~/.config.

    The bridge and the agent must agree on it. A bridge run as a service under
    another HOME registers somewhere the agent never looks; that agent passes
    --config and --root instead.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(base):
        base = os.path.join(env.get("HOME") or str(pathlib.Path.home()), ".config")
    return pathlib.Path(base) / "agent-letter-bridge"


def _check_private(st, what):
    if st.st_uid != os.getuid():
        raise RegistryError(f"{what} is not owned by this user")
    if st.st_mode & 0o077:
        raise RegistryError(f"{what} is open to other users; chmod it to owner-only")


def _private_dir(create):
    path = directory()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        raise RegistryError(f"the reply registry folder {path} is not a plain folder")
    _check_private(st, f"the reply registry folder {path}")
    return path


def _read(folder):
    try:
        fd = os.open(folder / "bridges.json", os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RegistryError("the reply registry is a link; refusing") from None
        raise
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise RegistryError("the reply registry is not a regular file")
        _check_private(st, "the reply registry")
        try:
            table = json.loads(fh.read().decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise RegistryError("the reply registry is malformed") from None
    if not isinstance(table, dict):
        raise RegistryError("the reply registry is malformed")
    return table


def register(root, config, mail_root, platform, sender, recipient):
    """Record this bridge. Called only once its config has loaded.

    Locked as well as atomic: two bridges starting together would each read
    the old table, and the later replace would drop the other's entry.
    """
    entry = {"root": os.path.realpath(root), "config": os.path.realpath(config),
             "mail_root": os.path.realpath(mail_root), "platform": platform,
             "from": sender, "to": recipient}
    if not (os.path.isdir(entry["root"]) and os.path.isfile(entry["config"])
            and os.path.isdir(entry["mail_root"])):
        raise RegistryError("root, config and mail root must exist to register")
    folder = _private_dir(create=True)
    try:
        lock = os.open(folder / "bridges.lock",
                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RegistryError("the reply registry lock is a link; refusing") from None
        raise
    try:
        st = os.fstat(lock)
        if not stat.S_ISREG(st.st_mode):
            raise RegistryError("the reply registry lock is not a regular file")
        _check_private(st, "the reply registry lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        table = _read(folder)
        table[entry["root"]] = entry
        tmp = folder / f".bridges.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(table, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, folder / "bridges.json")
    finally:
        os.close(lock)


def _envelope(folder, letter_id):
    """The envelope of <folder>/<id>.md, or None when there is no such file.

    Stricter than store.resolve on purpose: no links at the leaf or the
    folder, no FIFOs, and a repeated field is refused rather than last-wins.
    """
    try:
        st = os.lstat(folder)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(st.st_mode) or os.path.realpath(folder) != folder:
        raise RegistryError("a registered mailbox folder is a link; refusing")
    try:
        fd = os.open(os.path.join(folder, f"{letter_id}.md"),
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RegistryError(f"letter {letter_id} is a link, not a letter") from None
        raise
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise RegistryError(f"letter {letter_id} is not a regular file")
        raw = fh.read(MAX_LETTER + 1)
    if len(raw) > MAX_LETTER:
        raise RegistryError(f"letter {letter_id} is too large")
    try:
        lines = raw.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        raise RegistryError(f"letter {letter_id} is not text") from None
    if lines[0].rstrip("\r") != "---":
        raise RegistryError(f"letter {letter_id} has no envelope")
    fields = {}
    for line in lines[1:]:
        line = line.rstrip("\r")
        if line == "---":
            return fields
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not FIELD.match(key):
            raise RegistryError(f"letter {letter_id} has a malformed envelope")
        if key in fields:
            raise RegistryError(f"letter {letter_id} repeats the field {key}")
        fields[key] = value.strip()
    raise RegistryError(f"letter {letter_id} has no closing fence")


def resolve(letter_id):
    """The one registered bridge whose mailbox holds this letter, from that
    bridge's sender to its recipient. Zero or several is a refusal, never a
    guess - a stale entry that still matches makes it several."""
    store._check_id(letter_id)
    folder = _private_dir(create=False)
    table = _read(folder) if folder is not None else {}
    owners = []
    for entry in table.values():
        if not (isinstance(entry, dict)
                and all(isinstance(entry.get(k), str) for k in ENTRY_KEYS)
                and os.path.isdir(entry["root"])):
            continue  # malformed, or a bridge that no longer exists
        for name in ("inbox", "processed"):
            fields = _envelope(os.path.join(entry["mail_root"], name), letter_id)
            if fields is None:
                continue
            # And this bridge must have published it: a letter that only
            # looks like one of its letters has no owner.
            if (fields.get("id") == letter_id
                    and fields.get("from") == entry["from"]
                    and fields.get("to") == entry["to"]
                    and msgindex.origins_of(os.path.join(entry["root"], "state"),
                                            entry["platform"], letter_id)):
                owners.append(entry)
            break
    if len(owners) == 1:
        return owners[0]
    if not owners:
        raise RegistryError(
            f"no registered Agent Letter Bridge holds letter {letter_id}. A "
            f"bridge registers when it starts. If yours runs under another "
            f"HOME, or this letter did not come through a bridge, pass "
            f"--config and --root.")
    labels = ", ".join(sorted(f"{e['platform']} as {e['from']}" for e in owners))
    raise RegistryError(
        f"{len(owners)} registered bridges claim letter {letter_id} "
        f"({labels}); refusing to guess. Pass --config and --root.")
