"""Local, owner-only relay filing. No platform calls or message mutation."""
import datetime as dt
import os
import pathlib
import stat

from alb.bridge import run
from alb.letter import store


class FilingError(Exception):
    pass


def _private(path, directory=False):
    st = path.lstat()
    if (stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077
            or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode))):
        raise FilingError("expected an owner-only, non-symlink " + str(path))


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
    store._check_id(letter_id)
    inbox, processed = root / "inbox", root / "processed"
    _private(inbox, directory=True)
    source = inbox / (letter_id + ".md")
    _private(source)
    letter = store.resolve(inbox, letter_id)
    if letter.meta.get("to") != seat or letter.meta.get("id") != letter_id:
        raise FilingError("letter is not addressed to this seat or has a mismatched id")
    processed.mkdir(mode=0o700, exist_ok=True)
    _private(processed, directory=True)
    # Exclusive creation: never overwrite a processed letter. A crash between
    # link and unlink leaves two copies, not a lost letter.
    os.link(source, processed / source.name, follow_symlinks=False)
    store._fsync_dir(processed)
    source.unlink()
    store._fsync_dir(inbox)
    return letter_id


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
