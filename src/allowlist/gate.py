"""Fail-closed allowlist.

Deny is the default and the failure mode. A missing, malformed, empty or
wrong-shaped allowlist denies everything - there is no configuration that turns
this off, because an open default in a tool that holds a messaging token is a
CVE-shaped first issue.

Operators must be taught that SILENCE IS THE DENY PATH SUCCEEDING. A correctly
working allowlist is indistinguishable from a dead bot, and an operator who does
not know that will disable the control to "fix" it.
"""
import json
import pathlib


def allows(path, chat_id):
    """True only if chat_id is explicitly listed. Every other outcome is False."""
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return False

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False

    if not isinstance(data, dict):
        return False

    chats = data.get("chats")
    if not isinstance(chats, list) or not chats:
        return False

    # Compare as strings: a numeric id must not slip past a string allowlist,
    # and neither must the reverse.
    return str(chat_id) in {str(c) for c in chats}
