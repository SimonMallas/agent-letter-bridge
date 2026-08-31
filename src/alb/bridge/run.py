"""Wiring: config, and one cycle of poll -> letter -> ring.

This is the only module that knows all the pieces exist. Each piece keeps its
own privilege; this one just hands the output of the untrusted poller to the
notifier, and nothing else.
"""
import json
import os
import pathlib
import stat
import subprocess
import time

from alb.notifier import ring
from alb.poller import loop

# ALB_SURFACE is deliberately NOT required. Running without a multiplexer is a
# supported way to use this: mail lands durably and nothing pings, and the
# operator finds it by looking. Requiring a surface would mean the docs promise
# a mode the code refuses to start in.
REQUIRED = ("ALB_TOKEN",)

# Every setting this tool reads. An unknown key is refused rather than ignored:
# a dogfood install set ALB_NOTIFIER=tmux, nothing read it, and the bridge
# reported success - so the operator believed they had selected a notifier that
# did not exist, and their deployment diverged from their config in silence. A
# key that looks like it did something is worse than one that errors.
KNOWN = ("ALB_TOKEN", "ALB_SURFACE", "ALB_FROM", "ALB_TO", "ALB_NOTIFIER",
         "ALB_MAIL_ROOT")

# Transports that exist. Naming one that does not is refused rather than
# defaulted, because defaulting is what let a deployment believe it had
# selected tmux for days while nothing read the setting.
NOTIFIERS = ("cmux", "tmux")


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

    notifier = config.get("ALB_NOTIFIER", "cmux")
    if notifier not in NOTIFIERS:
        raise ConfigError(
            f"unsupported ALB_NOTIFIER {notifier!r}. Available: "
            f"{', '.join(NOTIFIERS)}."
        )

    unknown = [key for key in config if key not in KNOWN]
    if unknown:
        raise ConfigError(
            f"unknown settings: {', '.join(sorted(unknown))}. "
            f"This tool reads only: {', '.join(KNOWN)}. "
            f"Refusing rather than ignoring them, so a setting never appears to "
            f"take effect when nothing read it."
        )
    return config


# Private by default, not private if the operator remembers. The state
# directory holds a canary log naming the chats you messaged, an offset file
# describing your traffic, and dead letters quoting failures. Letters were
# already 0600; everything AROUND them was being created world-listable,
# because a directory made with the default umask is 0755 and nobody notices
# until they look.
# The letterbox helper, overridable because this is not everyone's path.
BUS_BINARY = os.environ.get("ALB_BUS_BINARY", "bus.sh")

DIR_MODE = 0o700
FILE_MODE = 0o600


def prepare_root(root):
    """Create the state layout with private permissions from the first run."""
    root = pathlib.Path(root)
    for path in (root, root / "inbox", root / "processed", root / "state"):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(DIR_MODE)
        except OSError:
            # A filesystem that cannot express this is a lost guarantee, not a
            # reason to refuse to run.
            pass
    return root


def write_private(path, text):
    """Write a state file that nobody else can read.

    Created before writing rather than chmod'd after, so the contents are never
    briefly world-readable on disk.
    """
    path = pathlib.Path(path)
    tmp = pathlib.Path(f"{path}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def prepare_mail_root(mail_root):
    """Create ONLY what letters need, in a directory that may not be ours.

    Deliberately not prepare_root. That function chmods the root, inbox,
    processed and state to 0700, which is right for a directory this bridge
    owns and wrong for a shared mailbox: it would write our umask onto identity
    and surface files belonging to something else, and create private state
    beside mail that must not carry it.

    So: inbox and processed if missing, nothing else, and the parent's
    permissions are left exactly as found.
    """
    mail_root = pathlib.Path(mail_root)
    for name in ("inbox", "processed"):
        (mail_root / name).mkdir(parents=True, exist_ok=True)
    return mail_root


def _bus_ring(recipient, kind, letter_id, binary=None):
    """Ring through the letterbox's own helper rather than imitating it.

    An inter-agent doorbell has an exact grammar, including a token the helper
    derives from the letter id. A line assembled here would either be rejected
    by the recipient's matcher or send them to look up the wrong token - so the
    helper that owns those bytes is the thing that must emit them.

    Integrated mode may assume the helper exists: it is only reachable by
    pointing at a letterbox, which implies one is installed.
    """
    subprocess.run([binary or BUS_BINARY, "ring", recipient, kind, letter_id],
                   check=True, capture_output=True)


def _record_ring(root, state, reason):
    """Ring outcome, written where the operator and the watchdog can read it."""
    path = pathlib.Path(root) / "state" / "ring-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(
        {"state": state, "reason": reason, "at": time.time()}))


def run_once(platform, transport, surface, root,
             sender="telegram-bridge", recipient="agent", mail_root=None):
    """One cycle. Returns the letters published.

    A conflict propagates: the caller exits cleanly so the token's holder keeps
    running. Note that a clean exit under a restart-on-crash-only service policy
    stays down by design - that is intended, and must be documented wherever
    this is deployed.
    """
    root = pathlib.Path(root)

    # Letters may live somewhere this bridge does not own. State never does.
    integrated = mail_root is not None and pathlib.Path(mail_root) != root
    mail = prepare_mail_root(mail_root) if integrated else root

    published = loop.poll_once(
        platform,
        mail / "inbox",
        # Under state/, with the other ledgers. It sat at the root while the
        # operations guidance told operators to preserve "the state ledgers" on
        # a machine move - so the one file that prevents republishing every
        # letter was the one the instruction missed.
        root / "state" / "delivered.json",
        root / "allowlist.json",
        # NOT here. The heartbeat is what a supervisor reads as "this process
        # is working", and a poll is not a completed cycle: consumption is only
        # transmitted afterwards. Writing it here meant a confirm failure left
        # a fresh heartbeat behind, so a bridge that had consumed nothing
        # looked healthy. The cycle claims liveness, below, once it is done.
        health_path=None,
        processed=mail / "processed",
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

    # Only now: fetched, letters durable, and the platform told. A cycle that
    # raised before this point leaves the previous heartbeat, which is the
    # honest signal - nothing completed.
    loop._write_heartbeat(root / "state" / "health.json")

    if not published:
        return published

    if not surface and not integrated:
        # Integrated mode needs no surface: the letterbox helper resolves the
        # recipient's registered pane itself, which is the whole reason to use
        # it rather than imitate it. Requiring ALB_SURFACE here would make an
        # operator pin a surface that nothing then reads.
        #
        # No multiplexer configured. Not an error and not a silent gap: the
        # letters are on disk and the absence of a bell is recorded where
        # --status and --doctor will show it.
        _record_ring(root, "disabled", "no ALB_SURFACE configured; mail lands, nothing rings")
        return published

    # COALESCED: one ring for the batch, not one per letter. The recipient
    # sweeps the inbox, so a ring per letter is noise that can outrun the
    # reader. The ring names the newest letter only to prove one exists.
    try:
        if integrated:
            # The letterbox's own doorbell, so it matches every skill that
            # already exists there. The standalone notifier is NOT also used:
            # two injects would be two submissions.
            _bus_ring(recipient, "info", published[-1])
        else:
            ring.notify(transport, surface, mail / "inbox", published[-1])
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
