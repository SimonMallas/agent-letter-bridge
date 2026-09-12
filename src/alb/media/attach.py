"""Allowlisted local files for outbound photos. Default deny. Open-once, no-follow."""
import json
import os
import pathlib
import stat

from alb.media import inspect, store


def roots(state):
    path = pathlib.Path(state) / "attach-roots.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = rec.get("roots") if isinstance(rec, dict) else None
    if not isinstance(listed, list):
        return []
    out = []
    for item in listed:
        if isinstance(item, str) and item:
            out.append(pathlib.Path(item))
    return out


def read_allowed(state, source):
    """Return file bytes if source is a regular file inside an allowlisted root."""
    if not isinstance(source, str) or not source:
        raise store.MediaError("photo path refused")
    given = pathlib.Path(source)
    try:
        st = os.lstat(given)
    except OSError:
        raise store.MediaError("photo path refused") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise store.MediaError("photo path refused")
    try:
        real = given.resolve(strict=True)
    except OSError:
        raise store.MediaError("photo path refused") from None
    allowed = False
    for root in roots(state):
        try:
            root_real = root.resolve(strict=True)
        except OSError:
            continue
        try:
            real.relative_to(root_real)
        except ValueError:
            continue
        allowed = True
        break
    if not allowed:
        raise store.MediaError("photo path refused")
    fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st2 = os.fstat(fd)
        if not stat.S_ISREG(st2.st_mode) or st2.st_size > inspect.MAX_BYTES:
            raise store.MediaError("photo path refused")
        data = os.read(fd, st2.st_size)
    finally:
        os.close(fd)
    inspect.preflight(data, "")
    return data
