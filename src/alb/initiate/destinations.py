"""Operator label -> grant-id map. Never stores a chat id."""
import pathlib

from alb.grant import store as grants
from alb.initiate import durable, ids


class UnknownLabel(Exception):
    """No destination bound to this label."""


def _path(state):
    return pathlib.Path(state) / "destinations.json"


def _load(state):
    path = _path(state)
    try:
        rec = durable.read_json(path)
    except (OSError, ValueError):
        return {}
    table = rec.get("labels")
    return table if isinstance(table, dict) else {}


def resolve(state, label):
    ids.check_label(label)
    table = _load(state)
    grant_id = table.get(label)
    if not isinstance(grant_id, str):
        raise UnknownLabel("unknown label")
    grants._safe_id(grant_id)
    return grant_id


def bind(state, label, grant_id):
    ids.check_label(label)
    grants._safe_id(grant_id)
    table = _load(state)
    table[label] = grant_id
    durable.write_json(_path(state), {"labels": table}, exclusive=False)


def unbind(state, label):
    ids.check_label(label)
    table = _load(state)
    table.pop(label, None)
    durable.write_json(_path(state), {"labels": table}, exclusive=False)


def list_labels(state):
    return dict(_load(state))
