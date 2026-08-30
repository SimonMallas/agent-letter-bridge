"""Durable-letter store."""
import hashlib
import json
import os
import pathlib
import re
import time
import uuid

# An identifier is an opaque token, never a path. Anything outside this
# alphabet is refused rather than resolved - unchecked, a crafted id escapes
# the store directory.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class UnsafeIdentifier(Exception):
    """The identifier is path-shaped. Refuse it; never resolve it."""


class NoSuchLetter(Exception):
    """The identifier did not resolve to exactly one letter."""


class MalformedLetter(Exception):
    """The file is not a well-formed letter. Refuse it; never guess."""


class Letter:
    def __init__(self, letter_id, meta, body):
        self.id = letter_id
        self.meta = meta
        self.body = body


def _serialise(meta, body):
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append(body)
    return "\n".join(lines) + "\n"


def update_token(update_id):
    """Stable, filename-safe token for a platform update id.

    Embedding this in the letter's filename makes "has this update already been
    published?" answerable from the letters themselves, cheaply and exactly -
    the ledger is a fast path, not the only evidence.
    """
    return hashlib.sha256(str(update_id).encode("utf-8")).hexdigest()[:12]


def find_by_update(update_id, searched):
    """Return the letter published for this exact update, or None.

    The filename token is a TRUNCATED digest, so it is an INDEX, not an
    identity: two distinct updates can collide in its 48 bits. A collision that
    suppressed publication would silently LOSE a message - strictly worse than
    the duplicate this lookup exists to prevent. So the token narrows the
    search cheaply, and the candidate is then VERIFIED against the update id
    the letter itself recorded. Never believe the index alone.

    Searched directories must include anywhere letters travel - an inbox that
    is swept to `processed` still counts as published, or every swept letter
    could be republished on a late redelivery.
    """
    pattern = f"*-u{update_token(update_id)}.md"
    for directory in searched:
        for path in sorted(pathlib.Path(directory).glob(pattern)):
            try:
                found = resolve(path.parent, path.name[:-3])
            except (MalformedLetter, NoSuchLetter, UnsafeIdentifier, OSError):
                # An unreadable candidate proves nothing either way. Keep
                # looking rather than assuming a match.
                continue
            if str(found.meta.get("update_id", "")) == str(update_id):
                return path
    return None


def publish(inbox, body, meta, update_id=None):
    """Publish atomically: temp file, then hardlink into place.

    The destination name either exists complete or does not exist at all. A
    direct write would leave a readable partial letter if it failed midway, and
    a reader cannot tell a partial letter from a short one.

    The temp file is dot-prefixed, so it is never mistaken for a letter even
    while it exists.
    """
    inbox = pathlib.Path(inbox)
    # Two independent components, and the distinction matters:
    #   - a random part guarantees the FILENAME is unique, so two letters in
    #     the same second never collide and the hardlink never fails
    #   - the update token is a lookup INDEX, deliberately not unique, so a
    #     redelivery can be found by glob and then verified
    # Merging them would make a token collision a filename collision, which
    # surfaces as a hard error instead of a verified non-match.
    unique = uuid.uuid4().hex[:8]
    stamp = time.strftime("%Y%m%dT%H%M%S")
    letter_id = (f"{stamp}-{unique}-u{update_token(update_id)}"
                 if update_id is not None else f"{stamp}-{unique}")
    temp = inbox / f".tmp-{letter_id}"
    dest = inbox / f"{letter_id}.md"

    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_serialise(meta, body))
            fh.flush()
            os.fsync(fh.fileno())
        # link() refuses to overwrite, so a duplicate id can never clobber a
        # letter that already exists.
        os.link(temp, dest)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    temp.unlink(missing_ok=True)
    return letter_id


def _check_id(letter_id):
    if not isinstance(letter_id, str) or not _SAFE_ID.match(letter_id):
        raise UnsafeIdentifier(f"path-shaped or invalid identifier refused")


def resolve(inbox, letter_id):
    _check_id(letter_id)
    path = pathlib.Path(inbox) / f"{letter_id}.md"
    # Exact match only. Substring or glob resolution misdelivers.
    if not path.is_file():
        raise NoSuchLetter(f"{letter_id}: no letter with this exact id")
    # Note on line endings: read_text uses universal newlines, so a letter
    # written with CRLF by a foreign writer is already normalised here. No
    # explicit translation is needed and adding one would be dead code that
    # looks like a guarantee. Two tests pin the behaviour so nobody "fixes"
    # this again, and so a future change away from read_text is caught.
    text = path.read_text(encoding="utf-8")
    # Two fences REQUIRED. A one-fence file must never parse, or body lines
    # become routing metadata - the fence-spoof class.
    parts = text.split("---\n")
    if len(parts) < 3 or parts[0] != "":
        raise MalformedLetter(f"{letter_id}: two-fence frontmatter required")
    frontmatter, body = parts[1], "---\n".join(parts[2:])
    meta = {}
    for line in frontmatter.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return Letter(letter_id, meta, body.rstrip("\n"))


def _load_delivered(ledger):
    ledger = pathlib.Path(ledger)
    if not ledger.is_file():
        return []
    try:
        data = json.loads(ledger.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger must not crash delivery. Treating it as empty risks
        # a duplicate letter; treating it as authoritative risks a lost one.
        # Duplicate-with-evidence is the safer failure.
        return []
    return data if isinstance(data, list) else []


def _record_delivered(ledger, delivered, update_id, cap):
    delivered.append(update_id)
    tmp = pathlib.Path(f"{ledger}.tmp")
    tmp.write_text(json.dumps(delivered[-cap:]), encoding="utf-8")
    os.replace(tmp, ledger)


def publish_once(inbox, ledger, update_id, body, meta, cap=1000, searched=None):
    """Publish unless this platform update has already been delivered.

    ORDER IS THE INVARIANT, and it is not arbitrary:
      1. consult the ledger  - BEFORE publish
      2. publish the letter
      3. record in the ledger - AFTER the letter exists

    Recording first would mark an update delivered that never landed, so the
    platform's redelivery would be silently skipped and the message lost. This
    ordering fails toward duplicate-with-evidence instead. Do not "simplify" it.
    """
    delivered = _load_delivered(ledger)
    if update_id in delivered:
        return None

    # The ledger is a FAST PATH, not the only evidence. A crash between the
    # hardlink and the ledger write leaves a letter on disk that the ledger
    # never learned about, and a ledger-only check would publish a second one.
    # The letters themselves outlive that window, so consult them too.
    searched = list(searched) if searched else [inbox]
    if find_by_update(update_id, searched) is not None:
        _record_delivered(ledger, delivered, update_id, cap)
        return None

    # The letter must RECORD the identity the lookup verifies against. Relying
    # on the caller to include it would make the dedup silently depend on a
    # convention nothing enforces - and a missing field reads as "not a match",
    # which republishes.
    meta = dict(meta or {})
    meta["update_id"] = update_id

    letter_id = publish(inbox, body, meta, update_id=update_id)
    _record_delivered(ledger, delivered, update_id, cap)
    return letter_id
