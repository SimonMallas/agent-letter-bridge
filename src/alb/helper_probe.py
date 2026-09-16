"""Inspect a doorbell helper without treating its stdout as a ring.

Used by --doctor and setup. A dummy ``ring`` to a nonexistent recipient
must not inject into a live pane; today's helpers print no_live_surface
prose, which this cut records as incompatible until they emit
doorbell-outcome v=1.
"""
from __future__ import annotations

import subprocess

from alb.doorbell_line import is_permitted_doorbell
from alb.ring_outcome import parse_line

PROBE_RECIPIENT = "alb-doctor-probe"
PROBE_ID = "1970-01-01T000000-alb-probe-00000000"
INCOMPATIBLE = "helper does not emit doorbell-outcome v=1"


def _decode(blob):
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return blob


def inspect_helper(binary, runner=subprocess.run, timeout=3):
    """Return (status, message). status is ok | missing | incompatible.

    Never classifies a permitted doorbell line as a successful helper check:
    that would be the helper injecting into this process.
    """
    if not binary:
        return "missing", INCOMPATIBLE + " (no helper configured)"
    try:
        result = runner(
            [binary, "ring", PROBE_RECIPIENT, "info", PROBE_ID],
            capture_output=True, text=True, timeout=timeout,
        )
        stdout = result.stdout or ""
    except FileNotFoundError:
        return "missing", INCOMPATIBLE + " (not found)"
    except subprocess.TimeoutExpired as exc:
        stdout = _decode(exc.stdout)
    except OSError:
        return "missing", INCOMPATIBLE + " (not executable)"

    for line in stdout.splitlines():
        if is_permitted_doorbell(line):
            return "incompatible", INCOMPATIBLE
        parsed = parse_line(line)
        if not isinstance(parsed, str):
            return "ok", "helper emits doorbell-outcome v=1"
    return "incompatible", INCOMPATIBLE
