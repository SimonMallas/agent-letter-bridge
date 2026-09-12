"""A0 §4: derived counts under a per-binding flock. No stored counter."""
import pathlib

from alb.grant import store as grants
from alb.initiate import durable, ids, reservation
from alb.outbound import store as outbound

CHARGED = {"reserved", "held", "settled-sent", "settled-ambiguous"}
LIVE = {"reserved", "held"}


class CapacityRefused(Exception):
    """No new admission. Nothing durable was created."""


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
              payload_digest, now=None):
    policy = grants.POLICY
    with durable.flock(_lock_path(state, binding_key), blocking=True):
        day_key, hour_key = ids.window_keys(now)
        used_day, used_hour, active_q = counts(
            state, binding_key, day_key=day_key, hour_key=hour_key)
        if (used_day >= policy["per_day"] or used_hour >= policy["per_hour"]
                or active_q >= policy["max_queued"]):
            raise CapacityRefused("capacity refused")
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


def readmit_existing(state, rec, *, now=None):
    """Crossing predicate: an existing reservation is not a new admission."""
    policy = grants.POLICY
    binding_key = rec["binding_key"]
    with durable.flock(_lock_path(state, binding_key), blocking=True):
        day_key, hour_key = ids.window_keys(now)
        used_day, used_hour, _active = counts(
            state, binding_key, day_key=day_key, hour_key=hour_key,
            exclude=rec["outbound_id"])
        if rec.get("day_key") == day_key and rec.get("hour_key") == hour_key:
            return rec
        if used_day + 1 > policy["per_day"] or used_hour + 1 > policy["per_hour"]:
            raise CapacityRefused("capacity refused")
        rec = dict(rec)
        rec["day_key"] = day_key
        rec["hour_key"] = hour_key
        return reservation.replace(state, rec)
