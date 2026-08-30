"""Canary: prove the send path is alive, on a schedule the operator owns.

An outbound path can rot silently. Nothing tells you a reply would fail until
the moment you need one to work, and by then you are already in the incident
the canary existed to prevent.

This is deliberately NOT a mock: it goes through the real send helper, so it
exercises the allowlist, the claim ledger and the platform exactly as a genuine
reply does. A canary that bypasses those tests nothing worth testing.

The operator confirms receipt in their own chat. Nothing here can prove the
message arrived - only that the send path accepted it - which is why the
confirmation is a human step and stays one.
"""
import json
import pathlib
import time

from allowlist import gate
from letter import store
from send import reply


class NoCanaryTarget(Exception):
    """No allowlisted chat, so nowhere legitimate to send.

    A canary that invents a destination is worse than no canary: it would send
    to somewhere nobody authorised in order to report good health.
    """


def _target(root):
    path = pathlib.Path(root) / "allowlist.json"
    try:
        chats = json.loads(path.read_text(encoding="utf-8")).get("chats") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        chats = []
    if not chats:
        raise NoCanaryTarget("no allowlisted chat to send a canary to")
    return str(chats[0])


def _log(root, line):
    path = pathlib.Path(root) / "state" / "canary.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}\n")


def run(sender, root):
    """Send one canary through the real path. Returns the reply id."""
    root = pathlib.Path(root)
    chat_id = _target(root)

    # The fixture lives in its own directory, NOT the inbox. A canary letter is
    # not mail: it must never be swept, read, or acted on as though a person
    # had sent it.
    fixtures = root / "state" / "canary"
    fixtures.mkdir(parents=True, exist_ok=True)

    letter_id = store.publish(fixtures, "canary fixture", {"chat_id": chat_id})

    # The claim is per letter and text, so a fixed body would make every run
    # after the first refuse as a replay and look like a failure.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = f"agent-letter-bridge canary {stamp} - send path alive"

    try:
        reply_id = reply.send_reply(
            sender, fixtures, root / "state", root / "allowlist.json",
            letter_id, text,
        )
    except Exception as exc:
        _log(root, f"FAILED {type(exc).__name__}: {exc}")
        raise
    _log(root, f"sent {reply_id} to {chat_id}")
    return reply_id
