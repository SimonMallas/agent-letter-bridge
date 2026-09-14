"""A1: durable private grant. Fail-closed. No send. No mail-root writes.

A grant is operator authority to originate. Missing, corrupt, expired,
disabled, or inconsistent policy is a refusal — never an invitation to
initialize. Diagnostics never carry a chat id or grant-id.

Publication: unique temp → fsync → exclusive dest claim → dir-fsync →
ready marker. The ready marker is the visibility commit point: a reader
may accept as soon as it exists. A PolicyError from create is not proof
that no reader observed authority in the final directory-sync window
(payload and both preceding directory syncs have already completed).
Strict failed-call-never-visible would need a separate protocol; this
one does not provide it. Json without a ready marker is unpublished.
A leftover ready without a dest is quarantined: same-id create refuses
and does not publish a new payload through the old marker.
"""
import errno
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import stat
import time

GRANT_ID_BYTES = 16  # 128 bits
GRANT_ID_HEX = GRANT_ID_BYTES * 2
_GRANT_ID_RE = re.compile(rf"^[0-9a-f]{{{GRANT_ID_HEX}}}$")
POLICY = {
    "per_day": 3,
    "per_hour": 2,
    "timezone": "Europe/London",
    "max_queued": 5,
    "max_body": 4000,
}


class PolicyError(Exception):
    """Fail-closed. The message is redacted: no chat id, no grant-id."""


def binding_key(platform, chat_id):
    """Budget scope: same binding shares one quota across grant renewals."""
    if not isinstance(platform, str) or not isinstance(chat_id, str):
        raise PolicyError("grant invalid")
    return hashlib.sha256(f"{platform}:{chat_id}".encode()).hexdigest()[:16]


def create(state, platform, chat_id, *, expiry=None, now=None, overrides=None):
    """Explicit operator init. Never called as a missing-on-use fallback."""
    if not isinstance(platform, str) or not platform:
        raise PolicyError("grant create refused")
    if not isinstance(chat_id, str) or not chat_id:
        raise PolicyError("grant create refused")
    grants_dir = _require_grants_dir(state, create=True)
    grant_id = secrets.token_hex(GRANT_ID_BYTES)
    rec = {
        "grant_id": grant_id,
        "platform": platform,
        "chat_id": chat_id,
        "enabled": True,
        "created": int(now if now is not None else time.time()),
        "expiry": expiry,
        "binding_key": binding_key(platform, chat_id),
    }
    if overrides:
        rec["overrides"] = _checked_overrides(overrides)
    _publish(grants_dir, grant_id, rec, exclusive=True)
    return rec


def load(state, grant_id):
    """Read one grant. Missing or corrupt → PolicyError. Never creates."""
    gid = _safe_id(grant_id)
    grants_dir = _require_grants_dir(state)
    return _read_published(grants_dir, gid)


def _checked_overrides(overrides):
    if not isinstance(overrides, dict):
        raise PolicyError("grant invalid")
    out = {}
    for key, value in overrides.items():
        if key not in POLICY:
            raise PolicyError("grant invalid")
        expected = POLICY[key]
        if type(value) is not type(expected):
            raise PolicyError("grant invalid")
        out[key] = value
    return out


def effective(grant, key):
    """Current POLICY, unless this grant named an override for `key`."""
    if key not in POLICY:
        raise PolicyError("grant invalid")
    overrides = grant.get("overrides") if isinstance(grant, dict) else None
    if isinstance(overrides, dict) and key in overrides:
        return overrides[key]
    return POLICY[key]


def validate(grant, *, now=None):
    """Schema, binding identity, enabled, expiry. Never POLICY equality.

    Limits follow current POLICY unless the grant carries `overrides`.
    Baked-in copies of old POLICY fields are ignored, so a limit change
    does not invalidate existing grants.
    """
    if not isinstance(grant, dict):
        raise PolicyError("grant invalid")
    gid = grant.get("grant_id")
    _safe_id(gid)
    platform = grant.get("platform")
    chat_id = grant.get("chat_id")
    if not isinstance(platform, str) or not platform:
        raise PolicyError("grant invalid")
    if not isinstance(chat_id, str) or not chat_id:
        raise PolicyError("grant invalid")
    created = grant.get("created")
    if type(created) is not int:
        raise PolicyError("grant invalid")
    if grant.get("binding_key") != binding_key(platform, chat_id):
        raise PolicyError("grant invalid")
    if "overrides" in grant:
        _checked_overrides(grant.get("overrides"))
    if "enabled" not in grant or grant["enabled"] is not True:
        raise PolicyError("grant disabled" if grant.get("enabled") is False
                          else "grant invalid")
    if "expiry" not in grant:
        raise PolicyError("grant invalid")
    expiry = grant["expiry"]
    if expiry is not None:
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float, str)):
            raise PolicyError("grant invalid")
        try:
            until = float(expiry)
        except (TypeError, ValueError, OverflowError):
            raise PolicyError("grant invalid") from None
        if not math.isfinite(until):
            raise PolicyError("grant invalid")
        when = now if now is not None else time.time()
        if when >= until:
            raise PolicyError("grant expired")


def disable(state, grant_id):
    rec = load(state, grant_id)
    rec["enabled"] = False
    grants_dir = pathlib.Path(state) / "grants"
    _publish(grants_dir, grant_id, rec, exclusive=False)
    return rec


def require_granted(state, platform, chat_id):
    """The grant predicate: allowlisted is not enough. Must have a valid grant."""
    if not isinstance(platform, str) or not isinstance(chat_id, str):
        raise PolicyError("grant invalid")
    grants_dir = _require_grants_dir(state)
    found = []
    try:
        names = list(grants_dir.iterdir())
    except OSError:
        raise PolicyError("grant corrupt") from None
    for path in names:
        if path.name.startswith("."):
            continue
        if not _GRANT_ID_RE.fullmatch(path.stem) or path.suffix != ".json":
            continue
        ready = grants_dir / f"{path.stem}.ready"
        try:
            rec = _read_published(grants_dir, path.stem)
        except PolicyError:
            if _confirmed_absent(ready):
                continue
            raise
        if rec.get("grant_id") != path.stem:
            raise PolicyError("grant invalid")
        if rec.get("platform") == platform and rec.get("chat_id") == chat_id:
            found.append(rec)
    if not found:
        raise PolicyError("grant missing")
    enabled = [g for g in found if g.get("enabled") is True]
    if len(enabled) > 1:
        raise PolicyError("grant invalid")
    if not enabled:
        raise PolicyError("grant disabled")
    validate(enabled[0])
    return enabled[0]


def _safe_id(grant_id):
    if not isinstance(grant_id, str) or not _GRANT_ID_RE.fullmatch(grant_id):
        raise PolicyError("grant invalid")
    return grant_id


def _require_grants_dir(state, *, create=False):
    """Same directory boundary for every API: real dir, not a symlink, owner-only."""
    state = pathlib.Path(state)
    grants_dir = state / "grants"
    if create:
        try:
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise PolicyError("grant persist failed") from None
        try:
            os.mkdir(grants_dir, 0o700)
        except FileExistsError:
            pass
        except OSError:
            raise PolicyError("grant persist failed") from None
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(grants_dir, flags)
    except OSError:
        if create:
            raise PolicyError("grant persist failed") from None
        raise PolicyError("grant missing") from None  # no grants directory
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise PolicyError("grant persist failed" if create else "grant missing")
        if st.st_mode & 0o077:
            if create:
                try:
                    os.fchmod(fd, 0o700)
                except OSError:
                    raise PolicyError("grant persist failed") from None
            else:
                raise PolicyError("grant invalid")
    finally:
        os.close(fd)
    if create:
        try:
            _fsync_dir(grants_dir.parent)
        except OSError:
            raise PolicyError("grant persist failed") from None
    return grants_dir


def _read_published(grants_dir, gid):
    """Authority only after the ready marker exists. Json without it is unpublished."""
    ready = grants_dir / f"{gid}.ready"
    try:
        rst = os.lstat(ready)  # completion marker, not the payload
    except OSError as exc:
        if exc.errno != errno.ENOENT:
            raise PolicyError("grant persist failed") from None
        raise PolicyError("grant missing") from None  # unpublished
    if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
        raise PolicyError("grant missing")
    if rst.st_mode & 0o077:
        raise PolicyError("grant invalid")
    rec = _read_regular(grants_dir / f"{gid}.json")
    if rec.get("grant_id") != gid:
        raise PolicyError("grant invalid")
    return rec


def _read_regular(path):
    try:
        st = os.lstat(path)
    except OSError:
        raise PolicyError("grant missing") from None  # no payload file
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise PolicyError("grant corrupt")
    if st.st_mode & 0o077:
        raise PolicyError("grant invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise PolicyError("grant missing") from None  # open payload
    try:
        raw = os.read(fd, st.st_size)
    except OSError:
        raise PolicyError("grant corrupt") from None
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
        rec = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise PolicyError("grant corrupt") from None
    if not isinstance(rec, dict) or not rec:
        raise PolicyError("grant corrupt")
    return rec


def _publish(grants_dir, grant_id, rec, *, exclusive):
    """Contents-first publish. Ready-marker creation is the visibility commit.

    After the marker exists, a concurrent reader may accept the grant even if
    this call later raises (final directory fsync). Failed-call-never-visible
    is not this protocol.
    """
    gid = _safe_id(grant_id)
    dest = grants_dir / f"{gid}.json"
    ready = grants_dir / f"{gid}.ready"
    nonce = secrets.token_hex(8)
    tmp = grants_dir / f".{gid}.{os.getpid()}.{nonce}.partial"
    payload = json.dumps(rec, indent=2, sort_keys=True)
    dest_claimed = False
    ready_claimed = False
    tmp_owned = False
    tmp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    claim_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        if exclusive:
            _refuse_leftover_ready(ready)
        _unlink_stale(tmp)
        fd = os.open(tmp, tmp_flags, 0o600)
        tmp_owned = True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            try:
                dfd = os.open(dest, claim_flags, 0o600)
            except FileExistsError:
                raise PolicyError("grant create refused") from None
            os.close(dfd)
            dest_claimed = True
            os.replace(tmp, dest)
        else:
            os.replace(tmp, dest)
            dest_claimed = True
        os.chmod(dest, 0o600)
        _fsync_dir(grants_dir)
        _fsync_dir(grants_dir.parent)
        if exclusive:
            rfd = os.open(ready, claim_flags, 0o600)
            ready_claimed = True
            try:
                os.fsync(rfd)
            finally:
                os.close(rfd)
        else:
            try:
                rst = os.lstat(ready)
            except OSError:
                raise PolicyError("grant persist failed") from None
            if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
                raise PolicyError("grant persist failed")
        _fsync_dir(grants_dir)
    except (PolicyError, OSError) as exc:
        _cleanup_failed(tmp if tmp_owned else None, dest if exclusive else None,
                        dest_claimed, ready if exclusive else None, ready_claimed)
        if isinstance(exc, PolicyError):
            raise
        raise PolicyError("grant persist failed") from None


def _confirmed_absent(path):
    """True only for ENOENT. Any other lookup error is fail-closed, not absence."""
    try:
        os.lstat(path)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return True
        raise PolicyError("grant persist failed") from None
    return False


def _refuse_leftover_ready(ready):
    """Same-id create must not publish a new payload through an old marker."""
    if _confirmed_absent(ready):
        return
    raise PolicyError("grant create refused")  # leftover marker


def _unlink_stale(path):
    try:
        st = os.lstat(path)
    except OSError:
        return
    try:
        os.unlink(path)
    except OSError:
        raise PolicyError("grant persist failed") from None
    del st


def _cleanup_failed(tmp, dest, dest_claimed, ready, ready_claimed):
    if tmp is not None:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if dest is not None and dest_claimed:
        try:
            os.unlink(dest)
        except OSError:
            pass
    if ready is not None and ready_claimed:
        try:
            os.unlink(ready)
        except OSError:
            pass


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
