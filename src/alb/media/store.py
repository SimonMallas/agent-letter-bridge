"""Private asset store: state/media/<letter-id>/<asset-id>/content.

Asset ids are 32 hex. Resolution is id → this root only. No path in letters.
v0.2 cannot forget media: referenced assets stay until a later lifecycle.
"""
import hashlib
import os
import pathlib
import re
import secrets
import shutil
import stat

from alb.initiate import durable
from alb.media import inspect

_ASSET_RE = re.compile(r"^[0-9a-f]{32}$")
_LETTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MediaError(inspect.MediaError):
    pass


def _safe_asset(asset_id):
    if not isinstance(asset_id, str) or not _ASSET_RE.fullmatch(asset_id):
        raise MediaError("asset id refused")
    return asset_id


def _safe_letter(letter_id):
    if not isinstance(letter_id, str) or not _LETTER_RE.fullmatch(letter_id):
        raise MediaError("letter id refused")
    return letter_id


def _safe_update_id(update_id):
    key = str(update_id)
    if not re.fullmatch(r"[0-9]+", key):
        raise MediaError("update id refused")
    return key


def media_root(state):
    return pathlib.Path(state) / "media"


def stage_inbound(state, update_id, data):
    """Write staged bytes under media/staging/<update-id>/<asset-id>/."""
    media_type = inspect.detect(data)
    asset_id = secrets.token_hex(16)
    key = _safe_update_id(update_id)
    dest_dir = media_root(state) / "staging" / key / asset_id
    _write_content(dest_dir, data)
    return {
        "asset_id": asset_id,
        "kind": "image",
        "media_type": media_type,
        "bytes": len(data),
        "name": inspect.display_name(media_type),
        "sha256": hashlib.sha256(data).hexdigest(),
        "update_id": key,
    }


def promote(state, update_id, letter_id, asset_id=None):
    """Place the advertised asset at media/<letter-id>/<asset-id>/. Idempotent."""
    letter_id = _safe_letter(letter_id)
    src_root = media_root(state) / "staging" / _safe_update_id(update_id)
    dest = media_root(state) / letter_id
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    if asset_id:
        asset_id = _safe_asset(asset_id)
        dest.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(dest, 0o700)
        placed = dest / asset_id
        if not placed.is_dir():
            src = src_root / asset_id
            if not _has_content(src / "content"):
                raise MediaError("asset missing")
            os.rename(src, placed)
        if not _has_content(placed / "content"):
            raise MediaError("asset missing")
        durable.fsync_dir(dest)
        durable.fsync_dir(dest.parent)
        _mark_ready(placed)
        return
    if dest.is_dir():
        raise MediaError("asset missing")
    if not src_root.is_dir():
        raise MediaError("asset missing")
    os.rename(src_root, dest)
    durable.fsync_dir(dest.parent)
    for child in dest.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            _mark_ready(child)


def discard_staging(state, update_id):
    """Drop leftover staging/<update-id>/ after a duplicate redelivery."""
    key = _safe_update_id(update_id)
    src = media_root(state) / "staging" / key
    if src.is_dir():
        shutil.rmtree(src, ignore_errors=True)


def stage_outbound(state, outbound_id, data):
    """Bind staged bytes to an outbound id. Retries reread these, not the source."""
    outbound_id = _safe_letter(outbound_id)
    media_type, _w, _h = inspect.preflight(data, "")
    asset_id = secrets.token_hex(16)
    dest_dir = media_root(state) / outbound_id / asset_id
    _write_content(dest_dir, data)
    _mark_ready(dest_dir)
    return {
        "asset_id": asset_id,
        "kind": "image",
        "media_type": media_type,
        "bytes": len(data),
        "name": inspect.display_name(media_type),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _has_content(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (not stat.S_ISLNK(st.st_mode) and stat.S_ISREG(st.st_mode)
            and st.st_size > 0)


def load_content(state, owner_id, asset_id):
    """Operator-local bytes. Never a path for authority."""
    owner_id = _safe_letter(owner_id)
    asset_id = _safe_asset(asset_id)
    path = media_root(state) / owner_id / asset_id / "content"
    try:
        st = os.lstat(path)
    except OSError:
        raise MediaError("asset missing") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise MediaError("asset missing")
    if st.st_size == 0:
        raise MediaError("asset missing")
    ready = path.parent / ".ready"
    try:
        rst = os.lstat(ready)
    except OSError:
        raise MediaError("asset missing") from None
    if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
        raise MediaError("asset missing")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return os.read(fd, st.st_size)
    finally:
        os.close(fd)


def load_outbound(state, outbound_id):
    root = media_root(state) / _safe_letter(outbound_id)
    if not root.is_dir():
        return None
    for child in root.iterdir():
        if child.name.startswith(".") or not child.is_dir():
            continue
        try:
            data = load_content(state, outbound_id, child.name)
        except MediaError:
            continue
        return data
    return None


def _write_content(dest_dir, data):
    dest_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    os.chmod(dest_dir.parent, 0o700)
    path = dest_dir / "content"
    tmp = dest_dir / f".content.{os.getpid()}.{secrets.token_hex(4)}.partial"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    durable.fsync_dir(dest_dir)


def _mark_ready(dest_dir):
    dest_dir = pathlib.Path(dest_dir)
    ready = dest_dir / ".ready"
    try:
        fd = os.open(ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        pass
    durable.fsync_dir(dest_dir)


def _contained_under(root, path):
    """lstat: real directory, not a symlink, path stays under root."""
    try:
        st = os.lstat(path)
    except OSError:
        raise MediaError("asset refused") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise MediaError("asset refused")
    root_real = pathlib.Path(os.path.realpath(root))
    path_real = pathlib.Path(os.path.realpath(path))
    try:
        path_real.relative_to(root_real)
    except ValueError:
        raise MediaError("asset refused") from None
    return path


def count_pre_marker(state):
    """Read-only: how many promoted assets have content but no .ready.

    Returns an int when the media root could be enumerated. Returns None when
    the root exists but cannot be inspected — that is not the same as 0.
    """
    root = media_root(state)
    try:
        present = root.is_dir()
    except OSError:
        return None
    if not present:
        return 0
    n = 0
    try:
        letters = list(root.iterdir())
    except OSError:
        return None
    for letter_dir in letters:
        if letter_dir.name.startswith(".") or letter_dir.name == "staging":
            continue
        try:
            st = os.lstat(letter_dir)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            continue
        try:
            assets = list(letter_dir.iterdir())
        except OSError:
            continue
        for asset_dir in assets:
            if asset_dir.name.startswith(".") or not asset_dir.is_dir():
                continue
            if not _has_content(asset_dir / "content"):
                continue
            ready = asset_dir / ".ready"
            try:
                rst = os.lstat(ready)
            except OSError:
                n += 1
                continue
            if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
                n += 1
    return n


def grandfather_ready(state):
    """Operator-run cutover: mark pre-marker assets with the full promote barrier.

    Returns (marked, skipped, failed). Never raises past an individual asset.
    """
    root = media_root(state)
    marked = skipped = failed = 0
    try:
        present = root.is_dir()
    except OSError:
        return marked, skipped, 1
    if not present:
        return marked, skipped, failed
    try:
        letters = list(root.iterdir())
    except OSError:
        return marked, skipped, 1
    for letter_dir in letters:
        if letter_dir.name.startswith(".") or letter_dir.name == "staging":
            skipped += 1
            continue
        try:
            _contained_under(root, letter_dir)
        except MediaError:
            failed += 1
            continue
        try:
            assets = list(letter_dir.iterdir())
        except OSError:
            failed += 1
            continue
        for asset_dir in assets:
            if asset_dir.name.startswith("."):
                continue
            try:
                _contained_under(letter_dir, asset_dir)
                if not _has_content(asset_dir / "content"):
                    skipped += 1
                    continue
                durable.fsync_dir(asset_dir)
                durable.fsync_dir(letter_dir)
                durable.fsync_dir(root)
                _mark_ready(asset_dir)
                marked += 1
            except Exception:  # noqa: BLE001 - isolate one asset
                failed += 1
    return marked, skipped, failed
