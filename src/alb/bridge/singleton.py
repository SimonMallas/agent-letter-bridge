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
        yield
    finally:
        os.close(fd)
