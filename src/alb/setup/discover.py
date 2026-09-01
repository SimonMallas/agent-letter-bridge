"""The two lookups `alb init` may perform, both explicitly asked for.

Neither happens unless the operator chose it in that moment. They live here,
apart from the wizard, so that what reaches the network and what reads the
process table are one short auditable file rather than a branch inside a
conversation.
"""
import re
import subprocess

from alb.adapters.telegram import api


def read_chat_ids(token, timeout=10):
    """One getUpdates call. Consumes nothing, and cannot.

    NO OFFSET IS SENT. The platform consumes everything at or below the mark it
    is given, so a call that sends no mark cannot advance one - the operator's
    messages stay queued for the bridge to fetch properly later. This is the
    same read-only call the documented curl performs.

    Returns `chat` ids only. The payload also contains a `from` id, identical in
    a direct message and different in a group, and an allowlist holding the
    wrong one denies everything silently. Not returning it is what makes the
    trap unreachable rather than merely documented.
    """
    payload = api._request(api.BASE, "getUpdates", {"timeout": 0}, token,
                           timeout=timeout)

    found = {}
    for item in payload.get("result", []):
        message = item.get("message") or {}
        chat = message.get("chat") or {}
        if not chat.get("id"):
            continue
        chat_id = str(chat["id"])
        # A label so the operator can tell which id is theirs. Shown, never
        # written: the allowlist holds ids, and a name in it would be a record
        # of a person the file does not need.
        label = chat.get("title") or " ".join(
            part for part in (chat.get("first_name"), chat.get("username")) if part)
        found.setdefault(chat_id, {"chat_id": chat_id, "label": label or chat.get("type", "")})
    return list(found.values())


def list_panes(notifier="cmux"):
    """What the multiplexer says exists. Never interpreted, never chosen.

    Returns [] on any failure. A missing multiplexer is a supported
    configuration, not an error to report at setup.
    """
    if notifier == "tmux":
        argv = ["tmux", "list-panes", "-a", "-F",
                "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}"]
    else:
        argv = ["cmux", "--id-format", "uuids", "tree", "--all"]

    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    if notifier == "tmux":
        panes = []
        for line in result.stdout.splitlines():
            pane_id, _, label = line.strip().partition("\t")
            if pane_id:
                panes.append({"id": pane_id, "label": label})
        return panes

    return _cmux_surfaces(result.stdout)


# A cmux tree names windows, workspaces, panes and surfaces, each with its own
# uuid, drawn with box characters. The ring types into a SURFACE, so the other
# three ids are offers that would fail - and taking the first field of a tree
# line offers the box-drawing itself. Matched explicitly, and anything that
# does not match is not offered at all.
_SURFACE = re.compile(
    r'\bsurface\s+([0-9A-Fa-f-]{36})\b'      # the id the ring needs
    r'(?:[^"\n]*"([^"]*)")?'                  # its title, when it has one
)


def _cmux_surfaces(output):
    panes = []
    for line in output.splitlines():
        match = _SURFACE.search(line)
        if match:
            panes.append({"id": match.group(1), "label": match.group(2) or ""})
    return panes
