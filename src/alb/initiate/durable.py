"""Contents-first private writes and flock. No mail-root writes."""
import contextlib
import fcntl
import json
import os
import pathlib
import secrets


class Busy(Exception):
    """Another process holds this lock."""


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, obj, *, exclusive=False):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, sort_keys=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.partial"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            try:
                claimed = os.open(path, flags, 0o600)
            except FileExistsError:
                os.unlink(tmp)
                raise
            os.close(claimed)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    path = pathlib.Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, os.fstat(fd).st_size)
    finally:
        os.close(fd)
    rec = json.loads(raw.decode("utf-8"))
    if not isinstance(rec, dict) or not rec:
        raise ValueError("empty record")
    return rec


@contextlib.contextmanager
def flock(path, *, blocking=True):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    lock = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, lock)
    except OSError:
        os.close(fd)
        raise Busy() from None
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
