"""Wiring: config, and one cycle of poll -> letter -> ring.

This is the only module that knows all the pieces exist. Each piece keeps its
own privilege; this one just hands the output of the untrusted poller to the
notifier, and nothing else.
"""
import json
import os
import pathlib
import stat
import time

from notifier import ring
from poller import loop

# ALB_SURFACE is deliberately NOT required. Running without a multiplexer is a
# supported way to use this: mail lands durably and nothing pings, and the
# operator finds it by looking. Requiring a surface would mean the docs promise
# a mode the code refuses to start in.
REQUIRED = ("ALB_TOKEN",)


class ConfigError(Exception):
    """Refuse loudly at startup rather than fail obscurely at 3am."""


def load_config(path):
    """Read the operator's env file, refusing anything unsafe or incomplete."""
    path = pathlib.Path(path)
    try:
        mode = path.stat().st_mode
    except OSError:
        raise ConfigError(f"no config at {path}") from None

    # The file holds a bot token. Starting with a credential any local process
    # can read is worse than not starting.
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        raise ConfigError(
            f"refusing to load {path}: permissions allow group or other to "
            f"read a file containing a token. Run: chmod 600 {path}"
        )

    config = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()

    # Name what is missing, never what is present: an error message that
    # helpfully echoed the config would print the token.
    missing = [key for key in REQUIRED if not config.get(key)]
    if missing:
        raise ConfigError(f"missing required settings: {', '.join(missing)}")
    return config


def _record_ring(root, state, reason):
    """Ring outcome, written where the operator and the watchdog can read it."""
    path = pathlib.Path(root) / "state" / "ring-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(f"{path}.tmp")
    tmp.write_text(json.dumps(
        {"state": state, "reason": reason, "at": time.time()}), encoding="utf-8")
    os.replace(tmp, path)


def run_once(platform, transport, surface, root,
             sender="telegram-bridge", recipient="agent"):
    """One cycle. Returns the letters published.

    A conflict propagates: the caller exits cleanly so the token's holder keeps
    running. Note that a clean exit under a restart-on-crash-only service policy
    stays down by design - that is intended, and must be documented wherever
    this is deployed.
    """
    root = pathlib.Path(root)
    published = loop.poll_once(
        platform,
        root / "inbox",
        root / "delivered.json",
        root / "allowlist.json",
        health_path=root / "state" / "health.json",
        processed=root / "processed",
        sender=sender,
        recipient=recipient,
    )

    # Tell the platform only now: every letter in this batch is durably on
    # disk, so it is safe for the platform to forget them. Acking internally
    # was never consumption - a cycle that ends without this re-reads
    # everything on the next poll.
    confirm = getattr(platform, "confirm", None)
    if confirm is not None:
        confirm()

    if not published:
        return published

    if not surface:
        # No multiplexer configured. Not an error and not a silent gap: the
        # letters are on disk and the absence of a bell is recorded where
        # --status and --doctor will show it.
        _record_ring(root, "disabled", "no ALB_SURFACE configured; mail lands, nothing rings")
        return published

    # COALESCED: one ring for the batch, not one per letter. The recipient
    # sweeps the inbox, so a ring per letter is noise that can outrun the
    # reader. The ring names the newest letter only to prove one exists.
    try:
        ring.notify(transport, surface, root / "inbox", published[-1])
    except Exception as exc:
        # Letters are authoritative; rings only accelerate. A dead notifier
        # must never cost a message - the mail is already on disk and will be
        # found by a sweep.
        #
        # But the swallow that protects the letter also HIDES ring death, and
        # mail-with-no-bell is a failure state, not a quieter tier. So the
        # failure is recorded where a human and a monitor can see it, without
        # ever being allowed to affect the letter.
        _record_ring(root, "failing", f"{type(exc).__name__}: {exc}")
    else:
        _record_ring(root, "ok", "delivered")

    return published
