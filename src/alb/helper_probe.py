"""Inspect a doorbell helper without treating its stdout as a ring.

Used by --doctor and setup. A dummy ``ring`` to a nonexistent reserved
recipient must not inject into a live pane; today's helpers print
no_live_surface prose, which this cut records as incompatible until they
emit doorbell-outcome v=1.
"""
from __future__ import annotations

import subprocess

from alb.doorbell_line import is_permitted_doorbell
from alb.ring_outcome import classify

PROBE_RECIPIENT = "alb-doctor-probe"
PROBE_ID = "1970-01-01T000000-alb-probe-00000000"
INCOMPATIBLE = "helper does not emit doorbell-outcome v=1"
INJECTED = "helper injected during probe"


def _decode(blob):
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return blob


def inspect_helper(binary, runner=subprocess.run, timeout=3):
    """Return (status, message). status is ok | missing | incompatible.

    Scan all stdout first. Any permitted doorbell line is an injection.
    Compatibility requires exactly one contract line (via classify) that is
    no_live_surface with target=-. submitted/pasted for the probe recipient
    is unsafe, not compatible.
    """
    if not binary:
        return "missing", INCOMPATIBLE + " (no helper configured)"
    if PROBE_RECIPIENT != "alb-doctor-probe":
        return "incompatible", "probe recipient is not reserved"
    try:
        result = runner(
            [binary, "ring", PROBE_RECIPIENT, "info", PROBE_ID],
            capture_output=True, text=True, timeout=timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except FileNotFoundError:
        return "missing", INCOMPATIBLE + " (not found)"
    except subprocess.TimeoutExpired as exc:
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
    except OSError:
        return "missing", INCOMPATIBLE + " (not executable)"

    if any(is_permitted_doorbell(line) for line in stdout.splitlines()):
        return "incompatible", INJECTED

    cls = classify(stdout, stderr)
    if cls.status == "unparseable":
        return "incompatible", INCOMPATIBLE
    if cls.outcome in ("submitted", "pasted_not_submitted"):
        return "incompatible", INJECTED
    if cls.outcome != "no_live_surface" or cls.target != "-":
        return "incompatible", INCOMPATIBLE
    return "ok", "helper emits doorbell-outcome v=1"
