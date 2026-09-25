"""A0 §4: derived counts under a per-binding flock. No stored counter."""
import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from alb.grant import store as grants
from alb.initiate import durable, ids, reservation
from alb.outbound import store as outbound

CHARGED = {"reserved", "held", "settled-sent", "settled-ambiguous"}
LIVE = {"reserved", "held"}


class CapacityRefused(Exception):
    """No new admission. Nothing durable was created."""


def _when(now, tz_name):
    tz = ZoneInfo(tz_name)
    if now is None:
        return datetime.now(tz)
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(now, tz=timezone.utc).astimezone(tz)
    return now.astimezone(tz)


def refusal(kind, limit, used, *, now=None, tz_name=None):
    """Name the limit. Hour and day say when that window next opens."""
    tz_name = tz_name or grants.POLICY["timezone"]
    if kind == "hourly":
        nxt = _when(now, tz_name).replace(minute=0, second=0, microsecond=0)
        nxt += timedelta(hours=1)
        return (f"capacity refused: hourly limit {limit} reached "
                f"({used} sent this hour); clears at {nxt.strftime('%H:%M')} {tz_name}")
    if kind == "daily":
        nxt = _when(now, tz_name).replace(hour=0, minute=0, second=0, microsecond=0)
        nxt += timedelta(days=1)
        return (f"capacity refused: daily limit {limit} reached "
                f"({used} sent today); clears at {nxt.strftime('%H:%M')} {tz_name}")
    if kind == "queued":
        return (f"capacity refused: queued limit {limit} reached "
                f"({used} waiting)")
    raise CapacityRefused("capacity refused")


def _refusals(hits, *, now, tz_name):
    parts = [refusal(kind, limit, used, now=now, tz_name=tz_name).split(": ", 1)[1]
             for kind, limit, used in hits]
    return "capacity refused: " + "; ".join(parts)


def _which(used_day, used_hour, active_q, per_day, per_hour, max_queued):
    """Every exhausted limit. Hour and day are both named when both are full,
    because the day stays closed after the hour opens."""
    hits = []
    if used_hour >= per_hour:
        hits.append(("hourly", per_hour, used_hour))
    if used_day >= per_day:
        hits.append(("daily", per_day, used_day))
    if active_q >= max_queued:
        hits.append(("queued", max_queued, active_q))
    return hits


class Quarantined(Exception):
    """An unreadable reservation blocks admission fail-closed."""


def _lock_path(state, binding_key):
    return pathlib.Path(state) / "locks" / f"budget-{binding_key}.lock"


def _txn_path(state, outbound_id):
    return pathlib.Path(state) / "locks" / f"{outbound_id}.txn"


def _has_terminal_receipt(state, outbound_id):
    receipts = pathlib.Path(state) / "receipts" / outbound_id
    if not receipts.is_dir():
        return False
    return any(event in outbound.TERMINAL for event in outbound._history(receipts))


def counts(state, binding_key, *, day_key, hour_key, exclude=None):
    """Return (used_day, used_hour, active_q). Raises Quarantined."""
    used_day = used_hour = active_q = 0
    unknown_scope = False
    scoped_unreadable = False
    for path, rec in reservation.iter_all(state):
        if rec is None:
            unknown_scope = True
            continue
        bkey = rec.get("binding_key")
        if not isinstance(bkey, str):
            unknown_scope = True
            continue
        status = rec.get("status")
        gid = rec.get("grant_id")
        effective_key = bkey
        if isinstance(gid, str):
            try:
                g = grants.load(state, gid)
            except grants.PolicyError:
                unknown_scope = True
                continue
            if g.get("binding_key") != bkey:
                if status in ("settled-sent", "settled-ambiguous"):
                    effective_key = g.get("binding_key")
                else:
                    continue
        if effective_key != binding_key:
            continue
        oid = rec.get("outbound_id")
        if exclude is not None and oid == exclude:
            continue
        if status in CHARGED:
            if not isinstance(rec.get("day_key"), str) or not rec.get("day_key"):
                scoped_unreadable = True
                continue
            if not isinstance(rec.get("hour_key"), str) or not rec.get("hour_key"):
                scoped_unreadable = True
                continue
            if rec.get("day_key") == day_key:
                used_day += 1
            if rec.get("hour_key") == hour_key:
                used_hour += 1
        elif status not in reservation.STATUSES:
            scoped_unreadable = True
            continue
        if status in LIVE and not _has_terminal_receipt(state, oid):
            active_q += 1
    if unknown_scope:
        raise Quarantined("unreadable reservation blocks all bindings")
    if scoped_unreadable:
        raise Quarantined("unreadable reservation blocks this binding")
    return used_day, used_hour, active_q


def admit_new(state, binding_key, *, outbound_id, grant_id, intent_id, seat,
              payload_digest, now=None, grant=None):
    grant = grant if grant is not None else grants.load(state, grant_id)
    per_day = grants.effective(grant, "per_day")
    per_hour = grants.effective(grant, "per_hour")
    max_queued = grants.effective(grant, "max_queued")
    tz_name = grants.effective(grant, "timezone")
    with durable.flock(_lock_path(state, binding_key), blocking=True):
        day_key, hour_key = ids.window_keys(now, tz_name=tz_name)
        used_day, used_hour, active_q = counts(
            state, binding_key, day_key=day_key, hour_key=hour_key)
        hits = _which(used_day, used_hour, active_q, per_day, per_hour, max_queued)
        if hits:
            raise CapacityRefused(_refusals(hits, now=now, tz_name=tz_name))
        rec = {
            "outbound_id": outbound_id,
            "binding_key": binding_key,
            "grant_id": grant_id,
            "intent_id": intent_id,
            "seat": seat,
            "payload_digest": payload_digest,
            "day_key": day_key,
            "hour_key": hour_key,
            "status": "reserved",
        }
        return reservation.create(state, rec)


def readmit_existing(state, rec, *, now=None, grant=None):
    """Crossing predicate: an existing reservation is not a new admission."""
    binding_key = rec["binding_key"]
    grant = grant if grant is not None else grants.load(state, rec["grant_id"])
    per_day = grants.effective(grant, "per_day")
    per_hour = grants.effective(grant, "per_hour")
    tz_name = grants.effective(grant, "timezone")
    with durable.flock(_lock_path(state, binding_key), blocking=True):
        day_key, hour_key = ids.window_keys(now, tz_name=tz_name)
        used_day, used_hour, _active = counts(
            state, binding_key, day_key=day_key, hour_key=hour_key,
            exclude=rec["outbound_id"])
        if rec.get("day_key") == day_key and rec.get("hour_key") == hour_key:
            return rec
        if used_hour + 1 > per_hour or used_day + 1 > per_day:
            hits = []
            if used_hour + 1 > per_hour:
                hits.append(("hourly", per_hour, used_hour))
            if used_day + 1 > per_day:
                hits.append(("daily", per_day, used_day))
            raise CapacityRefused(_refusals(hits, now=now, tz_name=tz_name))
        rec = dict(rec)
        rec["day_key"] = day_key
        rec["hour_key"] = hour_key
        return reservation.replace(state, rec)
