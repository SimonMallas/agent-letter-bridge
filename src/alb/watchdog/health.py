"""Independent monitoring. Reads a mirrored health file and reports.

It restarts nothing. Restarting is the service manager's job; a monitor with
authority over what it monitors violates the rule that the monitor never
depends on the thing it watches.

Freshness equals a COMPLETED CYCLE: the heartbeat is written only after fetch,
durable publication and platform confirm have all succeeded, so a
supervisor can judge the process from outside without its cooperation.
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
