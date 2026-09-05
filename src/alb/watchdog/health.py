"""Independent monitoring. Reads a mirrored health file and reports.

It restarts nothing. Restarting is the service manager's job; a monitor with
authority over what it monitors violates the rule that the monitor never
depends on the thing it watches.

Freshness equals LIVENESS, and the state says what kind. The heartbeat used to
be written only after a completed cycle, which made two healthy conditions -
starting up, and correctly waiting out a rate limit - indistinguishable from
death. It is now written whenever the loop goes round, so this module reads
both fields: the timestamp for "is it there" and the state for "what is it
doing", with a different allowance for each.
"""
import json
import math
import pathlib
import time

from alb.adapters.telegram.api import MAX_RETRY_AFTER as _MAX_PLATFORM_WAIT


class Status:
    def __init__(self, state, reason):
        self.state = state
        self.reason = reason


def now():
    return time.time()


def status(path, max_age):
    """Report on the monitored process. Never act on it."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        heartbeat = float(data["heartbeat"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A monitor that crashes on a bad file is a monitor that stops
        # monitoring. Report the uncertainty instead.
        return Status("unknown", "health file missing or unreadable")

    age = now() - heartbeat
    if age > max_age:
        return Status("stale", f"heartbeat is stale: {int(age)}s old, limit {max_age}s")
    return Status("ok", f"heartbeat {int(age)}s old")


# How long each state may go quiet before it stops being a state and starts
# being a corpse. These differ because the states differ: a bridge waiting out
# a rate limit is quiet BECAUSE it is behaving, and one number for everything
# would either kill it or give a genuinely hung process an hour's grace.
ALLOWANCE = {
    "running": 120,     # a long poll plus slack
    "starting": 300,    # a first poll can block; five minutes is not a start
    # DERIVED, not chosen: the classifier accepts a platform wait of up to
    # MAX_RETRY_AFTER, so anything smaller here would declare a bridge dead
    # while it correctly honours a floor the platform asked for - the
    # supervisor breaking the retry contract the bridge is keeping. The slack
    # covers the backoff applied on top of that floor.
    "degraded": _MAX_PLATFORM_WAIT + 300,
    # A bridge that yielded a contested token is not dead and must never be
    # restarted: doing so fights for a token it deliberately stood down from,
    # undoing yield-never-fight from the outside. It stays quiet forever by
    # design, so no allowance applies.
    "yielded": None,
}
DEFAULT_ALLOWANCE = 120

# A timestamp ahead of us is a clock rollback, a corrupted file or a foreign
# writer. Left unchecked it reads as fresh indefinitely - the longer it is
# wrong, the healthier it looks. Small skew is ordinary and tolerated.
CLOCK_SKEW_TOLERANCE = 30


class Verdict:
    """What the asking agent should DO, separate from what it is looking at.

    The state is an observation; the action is the decision. Keeping them
    apart means a seat cannot quietly invent its own mapping from one to the
    other, which is how four seats ended up with four arrangements.
    """

    def __init__(self, state, action, reason):
        self.state = state
        self.action = action
        self.reason = reason


def verdict(path):
    """Read one health file and say what to do about it. Never acts.

    Absence is deliberately NOT death: a missing file may mean the bridge has
    never run here, and restarting something that was never installed is a
    different mistake from restarting something that stopped.
    """
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        heartbeat = float(data["heartbeat"])
        # JSON admits NaN and Infinity, float() passes them through, and the
        # arithmetic below would then raise - so a corrupted file would crash
        # the reader that exists to absorb corrupted files. Checked HERE,
        # inside the same guard, rather than trusted downstream.
        if not math.isfinite(heartbeat):
            raise ValueError("non-finite heartbeat")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return Verdict("unknown", "investigate",
                       "no readable health file: the bridge may never have "
                       "run here, which is not the same as having stopped")

    # A file written by an older version has no state. Treat it as running -
    # seats upgrade at different times and a missing field is not corruption.
    state = data.get("state", "running")
    age = int(now() - heartbeat)

    if age < -CLOCK_SKEW_TOLERANCE:
        return Verdict("unknown", "investigate",
                       f"heartbeat is {-age}s in the FUTURE: a clock moved, "
                       f"the file is corrupt, or something else is writing it")

    if state not in ALLOWANCE and state != "running":
        # Never act on a record we cannot read. A future version, a typo or a
        # corrupted field is precisely what an operator should see, and "none"
        # is the one answer that hides it.
        return Verdict("unknown", "investigate",
                       f"unrecognised state {state!r}: this file was written "
                       f"by something this version does not understand")

    if state == "degraded" and data.get("reason") not in (
            "throttled_429", "upstream_5xx", "network"):
        return Verdict("unknown", "investigate",
                       "degraded without a reason this version knows: cannot "
                       "tell a legitimate wait from a stuck one")

    allowance = ALLOWANCE.get(state, DEFAULT_ALLOWANCE)
    if allowance is None:
        return Verdict(state, "investigate",
                       "the bridge yielded a contested token and stood down. "
                       "Restarting it would fight for a token it refused to "
                       "fight for - find the other consumer first")

    if age <= allowance:
        if state == "degraded":
            return Verdict("degraded", "none",
                           f"waiting ({data.get('reason', 'unknown')}), "
                           f"heartbeat {age}s old - leave it alone")
        return Verdict(state, "none", f"heartbeat {age}s old")

    return Verdict("dead", "restart",
                   f"last seen {age}s ago while {state}; allowance {allowance}s")
