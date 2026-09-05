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
import secrets


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
        # A fresh opaque generation per RUN, minted only after the lock is
        # held. Pi's second block: a stop request that names only a root can
        # outlive the bridge it was meant for - holder A is asked to stop,
        # crashes before reading it, and holder B starts and stands down for
        # a request that was never about it. The generation binds a request to
        # one specific run, so a stale one is recognisable rather than obeyed.
        generation = secrets.token_hex(8)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(
            {"pid": os.getpid(), "generation": generation}).encode("utf-8"))
        os.fsync(fd)
        yield generation
    finally:
        os.close(fd)


STOP_REQUEST = "stop-requested"


def current_generation(root):
    """The generation of the bridge running NOW, or None if none is.

    Read through the lock: if the lock can be taken nobody is running, and
    whatever the file says belongs to a run that has ended.
    """
    if running_pid(root) is None:
        return None
    try:
        data = json.loads(
            (pathlib.Path(root) / "bridge.lock").read_text(encoding="utf-8"))
        generation = data["generation"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return generation if isinstance(generation, str) and generation else None


def request_stop(root):
    """Ask the bridge running RIGHT NOW to stand down. Signals nothing.

    Returns the generation asked to stop, or None if nothing was running -
    in which case no request is left at all, because a request with nobody to
    honour it is a trap for the next bridge to start.
    """
    generation = current_generation(root)
    if generation is None:
        return None
    path = pathlib.Path(root) / "state"
    path.mkdir(parents=True, exist_ok=True)
    request = path / STOP_REQUEST
    tmp = path / f".{STOP_REQUEST}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"generation": generation}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, request)
    return generation


def stop_requested(root, generation):
    """True only if a stop was asked of THIS run.

    Fails closed in every uncertain direction: an unreadable, malformed or
    mismatched request is not a reason to stop handling mail, and a stop that
    cannot be read is not a stop.
    """
    try:
        data = json.loads(
            (pathlib.Path(root) / "state" / STOP_REQUEST)
            .read_text(encoding="utf-8"))
        # Valid JSON of the wrong SHAPE is the case that slips through: a list
        # has no .get and would raise where an unreadable file returns cleanly.
        if not isinstance(data, dict):
            return False
        return data.get("generation") == generation
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def clear_stop_request(root):
    """Cleared by the holder as it stands down, so an honoured request cannot
    stop the next bridge as well."""
    with contextlib.suppress(OSError):
        (pathlib.Path(root) / "state" / STOP_REQUEST).unlink()


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
