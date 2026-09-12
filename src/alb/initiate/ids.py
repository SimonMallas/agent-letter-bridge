"""A2: opaque outbound-id and payload digest. Length-prefixed so ('a','bc') ≠ ('ab','c')."""
import hashlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alb.grant import store as grants

_INTENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RAW_CHAT = re.compile(r"^-?\d+$")


class UsageError(Exception):
    """Operator input refused before any durable write."""


def length_prefixed(*parts):
    out = bytearray()
    for part in parts:
        if not isinstance(part, str):
            raise UsageError("id parts must be strings")
        raw = part.encode("utf-8")
        out.extend(len(raw).to_bytes(4, "big"))
        out.extend(raw)
    return bytes(out)


def route_ref(grant_id, binding_key):
    """Letter-facing route ref: keyed by grant entropy, not a chat fingerprint."""
    grants._safe_id(grant_id)
    if not isinstance(binding_key, str) or not binding_key:
        raise UsageError("binding key required")
    return hashlib.sha256(f"route{grant_id}{binding_key}".encode()).hexdigest()[:16]


def outbound_id(grant_id, seat, intent_id):
    grants._safe_id(grant_id)
    if not isinstance(seat, str) or not seat:
        raise UsageError("seat required")
    check_intent(intent_id)
    digest = hashlib.sha256(length_prefixed(grant_id, seat, intent_id)).hexdigest()
    return f"v1-{digest}"


def payload_digest(*, body, kind, seat, intent_id, binding_key, reason,
                   photo_hash=""):
    parts = [body, kind, seat, intent_id, binding_key, reason]
    if photo_hash:
        parts.append(photo_hash)
    return hashlib.sha256(length_prefixed(*parts)).hexdigest()


def check_intent(intent_id):
    if not isinstance(intent_id, str) or not _INTENT_RE.fullmatch(intent_id):
        raise UsageError("intent id refused")
    return intent_id


def check_label(label):
    if not isinstance(label, str) or not label:
        raise UsageError("label required")
    if _RAW_CHAT.fullmatch(label) or ":" in label or label.startswith("@"):
        raise UsageError("raw destination refused")
    if not _LABEL_RE.fullmatch(label):
        raise UsageError("label refused")
    return label


def check_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise UsageError("reason required (audit only; it does not authorize)")
    return reason


def window_keys(now=None, tz_name=None):
    tz_name = tz_name or grants.POLICY["timezone"]
    tz = ZoneInfo(tz_name)
    if now is None:
        when = datetime.now(tz)
    elif isinstance(now, (int, float)):
        when = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(tz)
    else:
        when = now.astimezone(tz)
    return when.strftime("%Y-%m-%d"), when.strftime("%Y-%m-%dT%H")
