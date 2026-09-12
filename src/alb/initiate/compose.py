"""O_EXCL initiated letter in the outbox. Never writes grant-id or chat id."""
import os
import pathlib

from alb.letter import store as letters
from alb.outbound import store as outbound
from alb.send.reply import AlreadyClaimed
from alb.initiate import durable


def claim_letter(outbox, outbound_id, *, seat, intent_id, reason, route_ref,
                 body, thread="", media=None):
    letters._check_id(outbound_id)
    outbox = pathlib.Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    extra = {
        "intent_id": intent_id,
        "reason": reason,
        "route_ref": route_ref,
        "kind": "initiated",
    }
    if media:
        extra["media_asset_id"] = media["asset_id"]
        extra["media_kind"] = media["kind"]
        extra["media_type"] = media["media_type"]
        extra["media_bytes"] = str(media["bytes"])
        extra["media_name"] = media["name"]
    meta = letters.envelope(
        sender=seat, recipient="owner", kind="initiated",
        extra=extra,
    )
    meta["id"] = outbound_id
    meta["thread"] = thread or outbound_id
    text = letters._serialise(meta, body)
    path = outbox / f"{outbound_id}.md"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise AlreadyClaimed(f"{outbound_id}: already composed")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    durable.fsync_dir(outbox)
    return outbound_id


def load_letter(outbox, outbound_id):
    return letters.resolve(pathlib.Path(outbox), outbound_id)
