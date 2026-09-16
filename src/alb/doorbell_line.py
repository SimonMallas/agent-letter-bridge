"""Receiver permitted-lines for an inter-agent doorbell.

Dual-capable: the old doorbell line and the middle-inserted ``from <sender>``
shape. A contract outcome line is excluded on purpose — it is not a ring.
The standalone Bridge payload must not match this grammar (self-exclusion).
"""
from __future__ import annotations

import re

TYPE = r"(?:request|delegate|status|blocker|result|ack|nack|info)"
SENDER = r"[A-Za-z][A-Za-z0-9._-]{0,31}"
TOKEN = r"[0-9a-f]{8}"
# Path after ``in `` is one non-space token (inbox location). Synthetic fixtures
# use ``inbox/``; live bus lines name a real inbox path.
_LINE = re.compile(
    rf"^📬 (?:letterbox|bus) doorbell: unacked {TYPE}"
    rf"(?: from ({SENDER}))?"
    rf" in \S+ — please check"
    rf"(?: · {TOKEN})?$"
)


def is_permitted_doorbell(line: str) -> bool:
    """True if an agent should treat ``line`` as a ring, not ordinary text."""
    if line.startswith("doorbell-outcome "):
        return False
    return _LINE.fullmatch(line) is not None
