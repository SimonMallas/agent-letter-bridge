"""Local, owner-only relay filing. No platform calls or message mutation."""
import datetime as dt
import json
import os
import pathlib
import stat

from alb.bridge import run
from alb.letter import store


class FilingError(Exception):
    pass


def _private(path, directory=False, shared_read=False):
    st = path.lstat()
    if (stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & (0o022 if shared_read else 0o077)
            or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode))):
        rule = "owner uid, no symlinks, not shared-writable" if shared_read else "owner-only, no symlinks"
        kind = "directory" if directory else "regular file"
        raise FilingError(f"expected {kind} ({rule}): {path}")


def context(root, config_path=None, mail_root=None):
    root = pathlib.Path(root).absolute()
    home = pathlib.Path.home().resolve()
    if not root.resolve().is_relative_to(home):
        raise FilingError("root must belong to this HOME")
    _private(root, directory=True)
    config_path = pathlib.Path(config_path) if config_path else root / "bridge.env"
    _private(config_path)
    config = run.load_config(config_path)
    mail = pathlib.Path(mail_root or config.get("ALB_MAIL_ROOT") or root)
    return root, mail, config.get("ALB_TO", "agent")


def file_letter(root, letter_id, config_path=None, mail_root=None):
    root, mail, seat = context(root, config_path, mail_root)
    if mail.resolve() != root.resolve():
        raise FilingError("integrated mailbox: use letterbox file <id>")
    return _move(mail, seat, letter_id)


def _move(mail, seat, letter_id, shared_read=False):
    store._check_id(letter_id)
    inbox, processed = mail / "inbox", mail / "processed"
    _private(inbox, directory=True, shared_read=shared_read)
    source = inbox / (letter_id + ".md")
    _private(source, shared_read=shared_read)
    letter = store.resolve(inbox, letter_id)
    if letter.meta.get("to") != seat or letter.meta.get("id") != letter_id:
        raise FilingError("letter is not addressed to this seat or has a mismatched id")
    processed.mkdir(mode=0o700, exist_ok=True)
    _private(processed, directory=True, shared_read=shared_read)
    # Exclusive creation: never overwrite a processed letter. A crash between
    # link and unlink leaves two copies, not a lost letter.
    os.link(source, processed / source.name, follow_symlinks=False)
    store._fsync_dir(processed)
    source.unlink()
    store._fsync_dir(inbox)
    return letter_id


def file_sent_reply(root, letter_id, out_id, config_path=None, mail_root=None):
    """Close out only a reply with a private, durable, latest sent receipt.

    A failure here is a filing failure, never permission to resend the reply.
    """
    root, mail, seat = context(root, config_path, mail_root)
    # Internal receipt-driven close-out also covers the configured integrated
    # seat mailbox. The manual file_letter entry point still refuses it.
    shared_read = mail.resolve() != root.resolve()
    _private(mail, directory=True, shared_read=shared_read)
    config = run.load_config(config_path or root / "bridge.env")
    from alb.outbound.store import reply_recipient
    sender = reply_recipient(run.platform_of(config))
    store._check_id(letter_id)
    store._check_id(out_id)
    if out_id != "reply-" + letter_id:
        raise FilingError("reply does not bind this source")
    _private(mail / "outbox", directory=True, shared_read=shared_read)
    _private(mail / "outbox" / (out_id + ".md"), shared_read=shared_read)
    outbound = store.resolve(mail / "outbox", out_id)
    if (outbound.meta.get("re") != letter_id or outbound.meta.get("from") != seat
            or outbound.meta.get("id") != out_id or outbound.meta.get("to") != sender):
        raise FilingError("outbound envelope does not bind this source/seat")
    receipts = root / "state" / "receipts"
    for directory in (root / "state", receipts, receipts / out_id):
        _private(directory, directory=True)
    events = []
    for path in (receipts / out_id).iterdir():
        sequence, sep, event = path.name.partition("-")
        if not sep or not sequence.isdigit() or not event.endswith(".json"):
            raise FilingError("unrecognised receipt")
        _private(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("event") != event[:-5]:
            raise FilingError("invalid receipt")
        events.append((int(sequence), payload["event"]))
    if (not events or len({n for n, _ in events}) != len(events)
            or max(events)[1] != "sent"):
        raise FilingError("reply has no current proven-sent receipt")
    # A previously filed source needs no mutation. Never replace a destination.
    source = mail / "inbox" / (letter_id + ".md")
    if not source.exists() and not source.is_symlink():
        _private(mail / "inbox", directory=True, shared_read=shared_read)
        _private(mail / "processed", directory=True, shared_read=shared_read)
        _private(mail / "processed" / source.name, shared_read=shared_read)
        filed = store.resolve(mail / "processed", letter_id)
        if filed.meta.get("id") != letter_id or filed.meta.get("to") != seat:
            raise FilingError("processed source does not match this seat/id")
        return letter_id
    return _move(mail, seat, letter_id, shared_read=shared_read)


def unfiled(root, config_path=None, mail_root=None, now=None):
    _, mail, seat = context(root, config_path, mail_root)
    inbox = mail / "inbox"
    if not inbox.exists():
        return "unfiled: 0 (oldest: none)"
    if inbox.is_symlink() or not inbox.is_dir():
        raise FilingError("unsafe inbox")
    now = now or dt.datetime.now(dt.timezone.utc)
    ages, count, unknown = [], 0, 0
    for path in inbox.glob("*.md"):
        if path.is_symlink() or not path.is_file():
            raise FilingError("unsafe letter")
        letter = store.resolve(inbox, path.stem)
        if letter.meta.get("to") != seat:
            continue
        if letter.meta.get("id") != path.stem:
            raise FilingError("mismatched letter id")
        count += 1
        try:
            sent = dt.datetime.fromisoformat(letter.meta.get("sent", "").replace("Z", "+00:00"))
            if sent.tzinfo is None:
                raise ValueError("unknown timezone")
            ages.append(max(0, int((now - sent).total_seconds())))
        except ValueError:
            unknown += 1
    oldest = "unknown" if unknown else (f"PT{max(ages)}S" if ages else "none")
    return f"unfiled: {count} (oldest: {oldest})"
