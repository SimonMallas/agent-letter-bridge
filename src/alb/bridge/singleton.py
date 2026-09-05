"""One running bridge per state directory.

A platform conflict is the BACKSTOP, not the control: it only fires once two
processes are already polling, costs a round trip, and hands one of them a
yield it should never have needed. A local lock refuses earlier and does not
depend on the platform noticing.

flock is released by the kernel when the process exits, so a crash cannot leave
a stale lock that blocks the next start.
"""
import contextlib
import fcntl
import json
import os
import pathlib


class AlreadyRunning(Exception):
    """Another bridge already holds this state directory."""


@contextlib.contextmanager
def hold(root):
    path = pathlib.Path(root) / "bridge.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning(
                f"another bridge is already running on {root}"
            ) from None
        # The pid goes IN the lock, so a stop path can find the holder without
        # guessing. Safe to trust precisely because flock is held: the kernel
        # releases it when this process dies, so anyone who cannot take this
        # lock is reading a pid that is provably still alive. A stale record
        # from a crashed bridge is unreachable by construction, which is what
        # keeps a stop from signalling a stranger who inherited the number.
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": os.getpid()}).encode("utf-8"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)


def running_pid(root):
    """The pid of the live bridge on this root, or None if none is running.

    The lock IS the proof, not the file. If this call can take it, no bridge
    holds it and whatever pid the file names is history - the kernel released
    the lock when that process died, and the number may since belong to a
    stranger. So a record we cannot corroborate with a held lock is treated as
    absent rather than as a target.
    """
    path = pathlib.Path(root) / "bridge.lock"
    if not path.is_file():
        return None
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Held: someone is alive. Read who.
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = int(data["pid"])
            except (OSError, ValueError, KeyError, TypeError):
                # Held by something that did not record itself. Real, but not
                # ours to signal - say so rather than guess at a pid.
                return -1
            return pid
        # We took it, so nothing was running. Release without disturbing the
        # recorded contents: a future holder overwrites them anyway.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)
