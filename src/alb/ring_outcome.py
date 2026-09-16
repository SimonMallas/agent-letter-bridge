"""Doorbell-outcome v=1 consumer.

Stdout only. Tokens only. Never substring-search for ``submitted`` or
``timeout=``. The helper's exit code is not the outcome.
"""
from __future__ import annotations

import re
from typing import NamedTuple

PREFIX = "doorbell-outcome "
OUTCOMES = frozenset({"submitted", "pasted_not_submitted", "no_live_surface"})
SUBMITTED_REASONS = frozenset({"-"})
PASTED_REASONS = frozenset({"enter_failed", "-"})
VALUE = re.compile(r"^[A-Za-z0-9._:%+-]+$")
TARGET_PANE = re.compile(r"^%[0-9]+$")


class Classification(NamedTuple):
    status: str
    outcome: str | None
    reason: str | None
    target: str | None
    retry: bool
    health: str


def _fail(status: str, reason: str | None = None) -> Classification:
    return Classification(
        status=status,
        outcome="no_live_surface" if status not in {"unparseable", "submitted", "pasted_not_submitted"} else None,
        reason=reason or status,
        target="-" if status != "unparseable" else None,
        retry=False,
        health="failing",
    )


def parse_line(line: str) -> Classification | str:
    """Parse one contract line. Return Classification-shaped tokens or an error tag."""
    if not line.startswith(PREFIX):
        return "not-prefix"
    parts = line.split(" ")
    if len(parts) != 5 or parts[0] != "doorbell-outcome":
        return "arity"
    keys = []
    vals = {}
    for part in parts[1:]:
        if part.count("=") != 1:
            return "token"
        key, _, value = part.partition("=")
        keys.append(key)
        vals[key] = value
    if keys != ["v", "outcome", "reason", "target"]:
        return "order"
    if vals["v"] != "1":
        return "version"
    for key, value in vals.items():
        if value == "-":
            continue
        if not VALUE.match(value):
            return "charset"
        if "%" in value and key != "target":
            return "percent"
        if key == "target" and "%" in value and not TARGET_PANE.match(value):
            return "pane"
    outcome, reason, target = vals["outcome"], vals["reason"], vals["target"]
    if outcome not in OUTCOMES:
        return "outcome"
    if outcome == "submitted" and (reason not in SUBMITTED_REASONS or target == "-"):
        return "cross"
    if outcome == "pasted_not_submitted" and (reason not in PASTED_REASONS or target == "-"):
        return "cross"
    if outcome == "no_live_surface" and (reason == "-" or target != "-"):
        return "cross"
    return Classification(
        status=outcome if outcome != "no_live_surface" else reason,
        outcome=outcome,
        reason=reason,
        target=target,
        retry=False,
        health="ok" if outcome == "submitted" else "failing",
    )


def classify(stdout: str, stderr: str = "", *,
             exit_code: int | str = 0, timed_out: bool = False,
             missing_executable: bool = False) -> Classification:
    """Independent consumer action from helper stdout + process fate.

    ``stderr`` is accepted and discarded so callers cannot accidentally merge.
    """
    del stderr
    if missing_executable:
        return Classification(
            status="adapter_unavailable",
            outcome="no_live_surface",
            reason="adapter_unavailable",
            target="-",
            retry=False,
            health="failing",
        )
    text = stdout if isinstance(stdout, str) else ""
    contract = [line for line in text.splitlines() if line.startswith(PREFIX)]
    nonzero = timed_out or (exit_code not in (0, "0", "", None))

    if len(contract) > 1:
        return _fail("unparseable")

    if len(contract) == 0:
        if timed_out:
            return Classification(
                status="unconfirmed",
                outcome="no_live_surface",
                reason="unconfirmed",
                target="-",
                retry=False,
                health="failing",
            )
        return _fail("unparseable")

    parsed = parse_line(contract[0])
    if isinstance(parsed, str):
        # Partial/malformed contract stdout plus caller timeout is unconfirmed,
        # not unparseable (Pi rev5 edge). Exit 0 with a broken line stays
        # unparseable: silent-looking success is forbidden.
        if timed_out:
            return Classification(
                status="unconfirmed",
                outcome="no_live_surface",
                reason="unconfirmed",
                target="-",
                retry=False,
                health="failing",
            )
        return _fail("unparseable")

    if parsed.outcome == "submitted":
        if nonzero:
            return Classification(
                status="unconfirmed",
                outcome="no_live_surface",
                reason="unconfirmed",
                target="-",
                retry=False,
                health="failing",
            )
        return parsed._replace(retry=False, health="ok")

    if parsed.outcome == "pasted_not_submitted":
        if nonzero:
            return Classification(
                status="unconfirmed",
                outcome="no_live_surface",
                reason="unconfirmed",
                target="-",
                retry=False,
                health="failing",
            )
        return parsed._replace(retry=False, health="failing")

    retry = parsed.reason == "helper_timeout"
    return parsed._replace(retry=retry, health="failing")
