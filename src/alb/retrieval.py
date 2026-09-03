"""W4: read the correspondence. Read-only, stdlib, exact - correct first.

No indexes and no cleverness: scan the mail directories, parse envelopes
with the one parser the product already trusts, filter in memory. The
archive is small by construction (one operator's correspondence), and a
wrong answer costs more than a slow one.
"""
import pathlib
import tarfile

from alb.letter import store
from alb.letter.store import NoSuchLetter, MalformedLetter  # re-exported


def _rows(mail_dirs):
    rows = []
    for d in mail_dirs:
        d = pathlib.Path(d)
        if not d.is_dir():
            continue
        direction = "out" if d.name == "outbox" else "in"
        for path in d.glob("*.md"):
            try:
                stored = store.resolve(d, path.stem)
            except (NoSuchLetter, MalformedLetter):
                # One bad file must not take the archive down with it - the
                # same lesson the letterbox check learned this week. resolve
                # already fail-closes per file; the walk skips and continues.
                continue
            rows.append({
                "mtime": path.stat().st_mtime,
                "id": path.stem,
                "direction": direction,
                "from": stored.meta.get("from", ""),
                "to": stored.meta.get("to", ""),
                "type": stored.meta.get("type", ""),
                "re": stored.meta.get("re", ""),
                "thread": stored.meta.get("thread", "") or path.stem,
                "correspondent": stored.meta.get("correspondent", ""),
                "meta": stored.meta,
                "body": stored.body,
                "where": str(path),
            })
    # Publish order, not id order: ids born in the same second differ only
    # by random hex, and an archive that shuffles a reply before its source
    # is lying about the conversation. mtime is the store's own memory of
    # when each letter landed; the id breaks exact ties deterministically.
    rows.sort(key=lambda r: (r["mtime"], r["id"]))
    return rows


def list_letters(mail_dirs, correspondent=None, direction=None, kind=None):
    rows = _rows(mail_dirs)
    if correspondent:
        rows = [r for r in rows if r["correspondent"] == correspondent]
    if direction:
        rows = [r for r in rows if r["direction"] == direction]
    if kind:
        rows = [r for r in rows if r["type"] == kind]
    return rows


def show(mail_dirs, letter_id):
    for r in _rows(mail_dirs):
        if r["id"] == letter_id:
            return r
    raise NoSuchLetter(f"{letter_id}: no letter with this exact id")


def search(mail_dirs, text):
    """Exact substring over body and envelope values. Never fuzzy - and
    never empty: '' is a substring of everything, so an empty search is a
    listing wearing a search's name. Refused (grok's flag)."""
    if not text or not text.strip():
        raise ValueError("search text must be non-empty")
    hits = []
    for r in _rows(mail_dirs):
        haystacks = [r["body"]] + [str(v) for v in r["meta"].values()]
        if any(text in h for h in haystacks):
            hits.append(r)
    return hits


def thread(mail_dirs, member_id):
    """The correspondence, both halves, in order - addressed by ANY member."""
    rows = _rows(mail_dirs)
    root = None
    for r in rows:
        if r["id"] == member_id:
            root = r["thread"] if r["direction"] == "in" else None
            if root is None:
                # An outbound letter's thread field carries the root.
                root = r["meta"].get("thread") or r["re"] or r["id"]
            break
    if root is None:
        raise NoSuchLetter(f"{member_id}: no letter with this exact id")
    return [r for r in rows if (r["thread"] == root or r["id"] == root
                                or r["meta"].get("thread") == root)]


def export_thread(mail_dirs, state, member_id, dest):
    """A tar the operator keeps: the thread's letters (both halves) plus the
    delivery receipts for its outbound letters. Read-only on the store."""
    members = thread(mail_dirs, member_id)
    state = pathlib.Path(state)
    with tarfile.open(dest, "w") as tar:
        for r in members:
            src = pathlib.Path(r["where"])
            # Regular files only. A symlink in a mailbox is not a letter, and
            # following it would pull arbitrary target bytes into an archive
            # that promises to be a copy of the correspondence (grok's flag).
            if src.is_symlink() or not src.is_file():
                continue
            tar.add(src, arcname=f"letters/{r['id']}.md")
            receipts = state / "receipts" / r["id"]
            if receipts.is_dir():
                for event in sorted(receipts.iterdir()):
                    if event.is_symlink() or not event.is_file():
                        continue
                    tar.add(event, arcname=f"receipts/{r['id']}/{event.name}")
    return dest
