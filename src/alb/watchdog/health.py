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
import pathlib
import time


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
    "degraded": 1800,   # backoff can legitimately run minutes, not half a day
}
DEFAULT_ALLOWANCE = 120


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
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return Verdict("unknown", "investigate",
                       "no readable health file: the bridge may never have "
                       "run here, which is not the same as having stopped")

    # A file written by an older version has no state. Treat it as running -
    # seats upgrade at different times and a missing field is not corruption.
    state = data.get("state", "running")
    age = int(now() - heartbeat)
    allowance = ALLOWANCE.get(state, DEFAULT_ALLOWANCE)

    if age <= allowance:
        if state == "degraded":
            return Verdict("degraded", "none",
                           f"waiting ({data.get('reason', 'unknown')}), "
                           f"heartbeat {age}s old - leave it alone")
        return Verdict(state, "none", f"heartbeat {age}s old")

    return Verdict("dead", "restart",
                   f"last seen {age}s ago while {state}; allowance {allowance}s")
