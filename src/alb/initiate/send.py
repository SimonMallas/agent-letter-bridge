"""A0 initiated send: admit → reserve → claim → composed → cross → settle.

Reply path is not imported for routing. Platform exceptions come from alb.send.reply
because the Telegram adapter already raises them.
"""
import hashlib
import pathlib

from alb.allowlist import gate
from alb.grant import store as grants
from alb.initiate import budget, compose, destinations, durable, ids, reservation
from alb.outbound import store as outbound
from alb.send import reply


class AuthorityRefused(Exception):
    """Grant, allowlist, or digest check failed at a crossing."""


class TerminalExists(Exception):
    """This outbound already has a terminal receipt. Never recross."""


def _require_claim(outbox, rec, grant, *, text, reason, seat, intent_id,
                   photo_hash=""):
    """A reservation without a matching durable outbox letter is not sendable."""
    from alb.letter import store as letters
    try:
        letter = compose.load_letter(outbox, rec["outbound_id"])
    except (letters.NoSuchLetter, letters.UnsafeIdentifier, letters.MalformedLetter,
            OSError):
        raise AuthorityRefused("claim missing") from None
    expected_ref = ids.route_ref(grant["grant_id"], rec.get("binding_key") or "")
    if (letter.body != text
            or letter.meta.get("type") != "initiated"
            or letter.meta.get("intent_id") != intent_id
            or letter.meta.get("reason") != reason
            or letter.meta.get("route_ref") != expected_ref
            or letter.meta.get("from") != seat):
        raise AuthorityRefused("claim mismatch")
    digest = _digest(grant, seat=seat, intent_id=intent_id, text=text,
                     reason=reason, photo_hash=photo_hash)
    if digest != rec.get("payload_digest"):
        raise AuthorityRefused("payload digest mismatch")


def _digest(grant, *, seat, intent_id, text, reason, photo_hash=""):
    return ids.payload_digest(
        body=text, kind="initiated", seat=seat, intent_id=intent_id,
        binding_key=grant["binding_key"], reason=reason, photo_hash=photo_hash)


def _authority(state, allowlist_path, grant, *, seat, intent_id, text, reason,
               expected_digest, photo_hash=""):
    grants.validate(grant)
    live = grants.require_granted(state, grant["platform"], grant["chat_id"])
    if live["grant_id"] != grant["grant_id"]:
        raise AuthorityRefused("grant binding changed")
    if not gate.allows(allowlist_path, grant["chat_id"]):
        raise reply.NotPermitted("destination not permitted at send time")
    digest = _digest(live, seat=seat, intent_id=intent_id, text=text, reason=reason,
                     photo_hash=photo_hash)
    if digest != expected_digest:
        raise AuthorityRefused("payload digest mismatch")
    return live, digest


def _repair_live_reservation(state, rec, existing):
    """A0 §5: a terminal receipt must not leave a live/charged reservation."""
    if rec.get("status") not in ("reserved", "held"):
        return rec
    rec = dict(rec)
    if existing == "sent":
        rec["status"] = "settled-sent"
    elif existing == "refused":
        rec["status"] = "refunded"
    elif existing in ("ambiguous", "dead"):
        rec["status"] = "settled-ambiguous"
    else:
        return rec
    return reservation.replace(state, rec)


def _unresolved_sending(state, outbound_id):
    """Newest receipt is sending: A0-ambiguous. An earlier throttle does not authorize recross."""
    receipts = pathlib.Path(state) / "receipts" / outbound_id
    if not receipts.is_dir():
        return False
    events = outbound._history(receipts)
    if not events:
        return False
    return events[-1] == "sending"


def _terminal_receipt(state, outbound_id):
    receipts = pathlib.Path(state) / "receipts" / outbound_id
    if not receipts.is_dir():
        return None
    events = []
    for path in receipts.iterdir():
        _, _, rest = path.name.partition("-")
        event = rest.removesuffix(".json")
        if event:
            events.append(event)
    for event in events:
        if event in outbound.TERMINAL:
            return event
    return None


def _settle(state, rec, event, **fields):
    outbound.record_event(state, rec["outbound_id"], event, **fields)
    if event == "sent":
        rec = dict(rec)
        rec["status"] = "settled-sent"
        reservation.replace(state, rec)
    elif event == "refused":
        rec = dict(rec)
        rec["status"] = "refunded"
        reservation.replace(state, rec)
    elif event == "throttled":
        rec = dict(rec)
        rec["status"] = "held"
        reservation.replace(state, rec)
    elif event in ("ambiguous", "dead"):
        rec = dict(rec)
        rec["status"] = "settled-ambiguous"
        reservation.replace(state, rec)
        reply._dead_letter(state, rec["outbound_id"], rec["outbound_id"],
                           fields.get("detail", event))
    return rec


def _cross(sender, state, allowlist_path, grant, rec, *, text, reason, seat,
           intent_id, now=None):
    oid = rec["outbound_id"]
    existing = _terminal_receipt(state, oid)
    if existing is not None:
        _repair_live_reservation(state, rec, existing)
        raise TerminalExists("terminal receipt forbids a new crossing")
    if _unresolved_sending(state, oid):
        _settle(state, rec, "ambiguous", detail="sending with no terminal")
        raise reply.AmbiguousOutcome("sending with no terminal receipt")
    if rec.get("status") not in ("reserved", "held"):
        raise TerminalExists("reservation is not sendable")
    rec = budget.readmit_existing(state, rec, now=now, grant=grant)
    try:
        grant = grants.load(state, rec["grant_id"])
        live, _digest_now = _authority(
            state, allowlist_path, grant, seat=seat, intent_id=intent_id,
            text=text, reason=reason, expected_digest=rec["payload_digest"],
            photo_hash=rec.get("photo_hash") or "")
    except (AuthorityRefused, reply.NotPermitted, grants.PolicyError):
        _settle(state, rec, "refused", detail="local policy")
        raise
    photo_hash = rec.get("photo_hash") or ""
    photo = None
    if photo_hash:
        from alb.media import store as media_store
        photo = media_store.load_outbound(state, oid)
        if photo is None or hashlib.sha256(photo).hexdigest() != photo_hash:
            _settle(state, rec, "refused", detail="staged photo missing or altered")
            raise AuthorityRefused("staged photo missing or altered")
    outbound.record_event(state, oid, "sending")
    try:
        if photo_hash:
            platform_id = sender.send_photo(live["chat_id"], photo, caption=text)
        else:
            platform_id = sender.send(live["chat_id"], text)
    except reply.Throttled as exc:
        _settle(state, rec, "throttled", detail=str(exc))
        raise
    except reply.DefiniteRefusal as exc:
        _settle(state, rec, "refused", detail=str(exc))
        raise
    except reply.AmbiguousOutcome as exc:
        _settle(state, rec, "ambiguous", detail=str(exc))
        raise
    except Exception as exc:
        _settle(state, rec, "ambiguous",
                detail=f"unclassified {type(exc).__name__}")
        raise reply.AmbiguousOutcome(
            f"unclassified sender failure: {type(exc).__name__}") from exc
    _settle(state, rec, "sent", platform_message_id=str(platform_id))
    from alb import msgindex
    msgindex.record(state, live["platform"], live["chat_id"],
                    str(platform_id), oid)
    return oid


def send_initiated(sender, state, outbox, allowlist_path, *, label, intent_id,
                   text, reason, seat, now=None, photo_path=None, photo_bytes=None):
    """Compose (if needed) and cross. Same CLI gesture resumes a throttle."""
    from alb.media import attach, inspect, store as media_store

    ids.check_label(label)
    ids.check_intent(intent_id)
    ids.check_reason(reason)
    text = text or ""
    if not isinstance(text, str):
        raise ids.UsageError("text required")
    if not text and photo_path is None and photo_bytes is None:
        raise ids.UsageError("text required")
    if not isinstance(seat, str) or not seat:
        raise ids.UsageError("seat required")

    data = None
    photo_hash = ""
    if photo_bytes is not None or photo_path is not None:
        data = photo_bytes if photo_bytes is not None else attach.read_allowed(
            state, photo_path)
        inspect.preflight(data, text)
        photo_hash = hashlib.sha256(data).hexdigest()

    grant_id = destinations.resolve(state, label)
    grant = grants.load(state, grant_id)
    if grant.get("platform") != "telegram":
        raise ids.UsageError("platform not supported")
    if len(text.encode("utf-8")) > grants.effective(grant, "max_body"):
        raise ids.UsageError("body too large")
    digest = _digest(grant, seat=seat, intent_id=intent_id, text=text,
                     reason=reason, photo_hash=photo_hash)
    oid = ids.outbound_id(grant["grant_id"], seat, intent_id)

    with durable.flock(budget._txn_path(state, oid), blocking=True):
        existing = reservation.load_optional(state, oid)
        if existing is not None:
            term = _terminal_receipt(state, oid)
            if term is not None:
                _repair_live_reservation(state, existing, term)
                raise TerminalExists("terminal receipt forbids a new crossing")
            if _unresolved_sending(state, oid):
                _settle(state, existing, "ambiguous",
                        detail="sending with no terminal")
                raise reply.AmbiguousOutcome("sending with no terminal receipt")
            if existing.get("binding_key") != grant.get("binding_key"):
                raise AuthorityRefused("binding mismatch")
            if existing.get("payload_digest") != digest:
                raise AuthorityRefused("payload digest mismatch")
            try:
                grants.validate(grant)
                _require_claim(outbox, existing, grant, text=text, reason=reason,
                               seat=seat, intent_id=intent_id,
                               photo_hash=photo_hash)
            except (grants.PolicyError, AuthorityRefused):
                _settle(state, existing, "refused", detail="local policy")
                raise
            return _cross(sender, state, allowlist_path, grant, existing,
                          text=text, reason=reason, seat=seat,
                          intent_id=intent_id, now=now)
        grants.validate(grant)
        rec = budget.admit_new(
            state, grant["binding_key"], outbound_id=oid,
            grant_id=grant["grant_id"], intent_id=intent_id, seat=seat,
            payload_digest=digest, now=now, grant=grant)
        media = None
        try:
            if data is not None:
                rec = dict(rec)
                rec["photo_hash"] = photo_hash
                rec = reservation.replace(state, rec)
                media = media_store.stage_outbound(state, oid, data)
            compose.claim_letter(
                outbox, oid, seat=seat, intent_id=intent_id, reason=reason,
                route_ref=ids.route_ref(grant["grant_id"], grant["binding_key"]),
                body=text, media=media)
        except Exception:
            rec = dict(rec)
            rec["status"] = "refunded"
            reservation.replace(state, rec)
            raise
        outbound.record_event(state, oid, "composed")
        return _cross(sender, state, allowlist_path, grant, rec,
                      text=text, reason=reason, seat=seat, intent_id=intent_id,
                      now=now)
