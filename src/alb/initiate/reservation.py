"""Private reservation records. Status is the budget source of truth."""
import pathlib

from alb.initiate import durable

STATUSES = {
    "reserved", "held", "settled-sent", "settled-ambiguous", "refunded",
}


class ReservationError(Exception):
    """Reservation missing, corrupt, or incomplete."""


def _dir(state):
    return pathlib.Path(state) / "reservations"


def _path(state, outbound_id):
    return _dir(state) / f"{outbound_id}.json"


def create(state, rec):
    if rec.get("status") not in STATUSES:
        raise ReservationError("reservation invalid")
    durable.write_json(_path(state, rec["outbound_id"]), rec, exclusive=True)
    return rec


def load(state, outbound_id):
    try:
        rec = durable.read_json(_path(state, outbound_id))
    except FileNotFoundError:
        raise ReservationError("reservation missing") from None
    except (OSError, ValueError):
        raise ReservationError("reservation corrupt") from None
    if rec.get("outbound_id") != outbound_id:
        raise ReservationError("reservation invalid")
    return rec


def load_optional(state, outbound_id):
    if not _path(state, outbound_id).is_file():
        return None
    return load(state, outbound_id)


def replace(state, rec):
    if rec.get("status") not in STATUSES:
        raise ReservationError("reservation invalid")
    durable.write_json(_path(state, rec["outbound_id"]), rec, exclusive=False)
    return rec


def iter_all(state):
    """Yield (path, rec-or-None). None means unreadable (quarantine)."""
    d = _dir(state)
    if not d.is_dir():
        return
    for path in d.iterdir():
        if path.name.startswith(".") or path.suffix != ".json":
            continue
        try:
            rec = durable.read_json(path)
        except (OSError, ValueError):
            yield path, None
            continue
        if not isinstance(rec, dict):
            yield path, None
            continue
        yield path, rec
