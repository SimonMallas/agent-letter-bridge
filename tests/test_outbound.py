"""W1: the outbound letter, whose creation IS the claim.

The v0.2 spec's crash machine, first slice. One logical reply per source
letter, established in a single atomic operation: the outbound letter id is
deterministic from the source id, and its O_EXCL create in outbox/ is the
claim. Delivery outcomes are immutable event FILES; the letter is never
rewritten after creation.
"""
import errno
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.outbound import store as outbound  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.send.reply import AlreadyClaimed  # noqa: E402


SOURCE_ID = "2026-09-02T190000-telegram-msg-77-abcd1234"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.mail = base / "mail"
        (self.mail / "outbox").mkdir(parents=True)
        self.root = base / "root"
        (self.root / "state").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def compose(self, body="the reply", **kw):
        return outbound.compose(
            self.mail / "outbox", self.root / "state",
            source_id=SOURCE_ID, origin_chat="111",
            sender="codex", body=body, **kw)


class TheLetterIsTheClaim(Base):
    def test_the_id_is_deterministic_from_the_source(self):
        letter_id = self.compose()
        self.assertEqual(letter_id, f"reply-{SOURCE_ID}")

    def test_a_second_compose_for_the_same_source_is_refused(self):
        """The whole point: no separate claim step can race the letter,
        because they are one atomic operation. The loser fails BEFORE any
        orphan exists."""
        self.compose()
        with self.assertRaises(AlreadyClaimed):
            self.compose(body="different words entirely")

    def test_the_refusal_leaves_no_orphan(self):
        self.compose()
        try:
            self.compose(body="second attempt")
        except AlreadyClaimed:
            pass
        letters_on_disk = list((self.mail / "outbox").glob("*.md"))
        self.assertEqual(len(letters_on_disk), 1)

    def test_the_letter_parses_with_the_standard_store(self):
        """Outbound letters are letters - the same two-fence envelope the
        rest of the product already reads."""
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.body.strip(), "the reply")

    def test_gate0_routable_ids_and_correspondent_provenance(self):
        """from/to stay routable participants; the external principal is a
        provenance field. The correspondent key is the stored stable opaque
        derivation, never the alias, never the raw chat id."""
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.meta.get("from"), "codex")
        self.assertEqual(stored.meta.get("to"), "telegram-bridge")
        self.assertEqual(stored.meta.get("re"), SOURCE_ID)
        self.assertEqual(stored.meta.get("type"), "info")
        key = stored.meta.get("correspondent", "")
        self.assertEqual(len(key), 16)
        self.assertNotIn("111", key)

    def test_the_store_beats_the_derivation(self):
        """Authoritative means the stored value wins even when it does not
        match what today's derivation would produce - that is what lets the
        scheme change without splitting identities."""
        import json as j
        (self.root / "state" / "correspondents.json").write_text(
            j.dumps({"telegram:111": "legacykey0000001"}), encoding="utf-8")
        letter_id = self.compose()
        stored = letters.resolve(self.mail / "outbox", letter_id)
        self.assertEqual(stored.meta.get("correspondent"), "legacykey0000001")

    def test_the_correspondent_key_is_stable_across_composes(self):
        """Derived once, stored, the store authoritative thereafter."""
        self.compose()
        first = json.loads((self.root / "state" / "correspondents.json").read_text())
        letter_id2 = outbound.compose(
            self.mail / "outbox", self.root / "state",
            source_id="2026-09-02T191111-telegram-msg-78-ffff0000",
            origin_chat="111", sender="codex", body="again")
        second = json.loads((self.root / "state" / "correspondents.json").read_text())
        self.assertEqual(first, second)

    def test_new_correspondent_is_not_a_chat_fingerprint(self):
        import hashlib
        key = outbound.correspondent_key(self.root / "state", "999001002")
        unsalted = hashlib.sha256(b"telegram:999001002").hexdigest()[:16]
        self.assertNotEqual(key, unsalted)
        for n in range(1_000_000):
            if hashlib.sha256(f"telegram:{n}".encode()).hexdigest()[:16] == key:
                self.fail("correspondent matched an unsalted telegram chat fingerprint")
        blob = (self.root / "state" / "correspondent-salt").read_text()
        self.assertNotIn(key, blob)

    def test_same_chat_and_salt_are_stable(self):
        a = outbound.correspondent_key(self.root / "state", "333")
        b = outbound.correspondent_key(self.root / "state", "333")
        self.assertEqual(a, b)
        other_state = self.root / "other-state"
        other_state.mkdir()
        c = outbound.correspondent_key(other_state, "333")
        self.assertNotEqual(a, c)

    def test_salt_file_is_private_and_minted_once(self):
        outbound.correspondent_key(self.root / "state", "111")
        salt = self.root / "state" / "correspondent-salt"
        self.assertEqual(stat.S_IMODE(salt.stat().st_mode), 0o600)
        first = salt.read_bytes()
        outbound.correspondent_key(self.root / "state", "222")
        self.assertEqual(salt.read_bytes(), first)
        letter = (self.mail / "outbox" / f"{self.compose()}.md").read_text()
        token = first.strip().decode("ascii")
        self.assertNotIn(token, letter)

    def test_incomplete_salt_is_not_an_unsalted_fingerprint(self):
        import hashlib
        salt = self.root / "state" / "correspondent-salt"
        salt.write_bytes(b"")
        os.chmod(salt, 0o600)
        key = outbound.correspondent_key(self.root / "state", "111")
        unsalted = hashlib.sha256(b"telegram:111").hexdigest()[:16]
        self.assertNotEqual(key, unsalted)
        self.assertEqual(len(bytes.fromhex(salt.read_text().strip())), 32)

    def test_short_salt_is_refused_then_replaced(self):
        salt = self.root / "state" / "correspondent-salt"
        salt.write_text("dead")
        os.chmod(salt, 0o600)
        outbound.correspondent_key(self.root / "state", "111")
        self.assertEqual(len(bytes.fromhex(salt.read_text().strip())), 32)

    def test_world_readable_salt_is_tightened(self):
        outbound.correspondent_key(self.root / "state", "111")
        salt = self.root / "state" / "correspondent-salt"
        os.chmod(salt, 0o644)
        outbound.correspondent_key(self.root / "state", "222")
        self.assertEqual(stat.S_IMODE(salt.stat().st_mode), 0o600)

    def test_corrupt_map_is_preserved_not_reset(self):
        path = self.root / "state" / "correspondents.json"
        path.write_text("[]")
        os.chmod(path, 0o600)
        with self.assertRaises(ValueError):
            outbound.correspondent_key(self.root / "state", "111")
        self.assertEqual(path.read_text(), "[]")

    def test_serialized_writers_keep_both_routes(self):
        import threading
        errors = []

        def write(chat):
            try:
                outbound.correspondent_key(self.root / "state", chat)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        a = threading.Thread(target=write, args=("aaa",))
        b = threading.Thread(target=write, args=("bbb",))
        a.start(); b.start()
        a.join(); b.join()
        self.assertEqual(errors, [])
        table = json.loads((self.root / "state" / "correspondents.json").read_text())
        self.assertIn("telegram:aaa", table)
        self.assertIn("telegram:bbb", table)
        self.assertEqual(outbound.origin_chat_for(self.root / "state", table["telegram:aaa"]), "aaa")
        self.assertEqual(outbound.origin_chat_for(self.root / "state", table["telegram:bbb"]), "bbb")

    def test_cached_map_hit_still_requires_dir_fsync(self):
        outbound.correspondent_key(self.root / "state", "111")
        real = outbound._fsync_dir

        def boom(path):
            if pathlib.Path(path) == self.root / "state":
                raise OSError(errno.EIO, "injected dir fsync")
            return real(path)

        with mock.patch.object(outbound, "_fsync_dir", side_effect=boom):
            with self.assertRaises(OSError):
                outbound.correspondent_key(self.root / "state", "111")

    def test_short_write_is_completed_before_publish(self):
        real = os.write
        chunks = []

        def short(fd, data):
            payload = bytes(data) if not isinstance(data, bytes) else data
            if len(payload) > 8:
                chunks.append(len(payload))
                return real(fd, payload[:8])
            return real(fd, payload)

        with mock.patch.object(os, "write", side_effect=short):
            key = outbound.correspondent_key(self.root / "state", "111")
        table = json.loads((self.root / "state" / "correspondents.json").read_text())
        self.assertEqual(table["telegram:111"], key)
        self.assertTrue(chunks)

    def test_unreadable_valid_salt_is_not_rotated(self):
        outbound.correspondent_key(self.root / "state", "111")
        salt = self.root / "state" / "correspondent-salt"
        before = salt.read_bytes()

        def eio(*_a, **_k):
            raise OSError(errno.EIO, "injected salt read")

        with mock.patch.object(outbound, "_read_valid_salt", side_effect=eio):
            with self.assertRaises(OSError):
                outbound.correspondent_key(self.root / "state", "222")
        self.assertEqual(salt.read_bytes(), before)


class EventsAreImmutableFiles(Base):
    def test_composed_event_exists_after_compose(self):
        letter_id = self.compose()
        event = self.root / "state" / "receipts" / letter_id / "1-composed.json"
        self.assertTrue(event.is_file())
        payload = json.loads(event.read_text())
        self.assertEqual(payload["event"], "composed")
        self.assertIn("at", payload)

    def test_no_platform_fact_on_the_letter_ever(self):
        """The letter is written pre-send and never rewritten; anything the
        platform returns later belongs in events, not the envelope."""
        letter_id = self.compose()
        text = next((self.mail / "outbox").glob(f"{letter_id}.md")).read_text()
        self.assertNotIn("message_id", text)

    def test_record_event_appends_a_new_numbered_file(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sent",
                              platform_message_id="4242")
        d = self.root / "state" / "receipts" / letter_id
        names = sorted(p.name for p in d.iterdir())
        self.assertEqual(names, ["1-composed.json", "2-sending.json", "3-sent.json"])
        sent = json.loads((d / "3-sent.json").read_text())
        self.assertEqual(sent["platform_message_id"], "4242")

    def test_a_seq_collision_refuses_rather_than_truncates(self):
        """The pin the gate demanded: two processes computing the same next
        sequence must not silently rewrite history. Whatever names the file,
        O_EXCL at the open is the guarantee - simulate the race by forcing
        the same path twice and expect refusal, with the first content
        intact."""
        from unittest import mock
        letter_id = self.compose()
        d = self.root / "state" / "receipts" / letter_id
        taken = d / "2-sending.json"
        outbound.record_event(self.root / "state", letter_id, "sending")
        original = taken.read_text()
        with mock.patch.object(outbound, "_event_path", return_value=taken):
            with self.assertRaises(FileExistsError):
                outbound.record_event(self.root / "state", letter_id, "sending")
        self.assertEqual(taken.read_text(), original)

    def test_events_never_overwrite(self):
        """Immutable means immutable: recording the same transition twice
        yields two files, not one rewritten - history is append-only even
        when the caller stutters."""
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sending")
        d = self.root / "state" / "receipts" / letter_id
        self.assertEqual(len(list(d.iterdir())), 3)


class RestartReconciliation(Base):
    def test_in_flight_without_terminal_is_ambiguous(self):
        """Code cannot prove whether the syscall reached the platform."""
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        verdicts = outbound.reconcile(self.root / "state")
        self.assertEqual(verdicts, {letter_id: "ambiguous"})

    def test_composed_but_never_sending_is_unsent(self):
        letter_id = self.compose()
        verdicts = outbound.reconcile(self.root / "state")
        self.assertEqual(verdicts, {letter_id: "unsent"})

    def test_terminal_states_reconcile_clean(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.record_event(self.root / "state", letter_id, "sent",
                              platform_message_id="1")
        self.assertEqual(outbound.reconcile(self.root / "state"), {})


class ReconcileRunsAtStartup(Base):
    """Grok's hostile-review flag 1: reconcile existed, was pinned, and was
    never called. A crash after 2-sending left no dead-letter for a human on
    restart - the retry refused (no double-post) but nobody was told why.
    The bridge's first act on rising is now the reconciliation pass, and it
    is idempotent: a reconciled letter gains a terminal 'dead' event so the
    next restart does not re-flag it."""

    def test_ambiguous_in_flight_dead_letters_on_startup(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        flagged = outbound.reconcile_at_startup(self.root / "state")
        self.assertEqual(flagged, [letter_id])
        dead = list((self.root / "state" / "dead-letters").glob("*.json"))
        self.assertEqual(len(dead), 1)
        payload = json.loads(dead[0].read_text())
        self.assertEqual(payload["letter_id"], letter_id)
        self.assertIn("restart", payload["detail"])

    def test_the_pass_is_idempotent_across_restarts(self):
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.reconcile_at_startup(self.root / "state")
        second = outbound.reconcile_at_startup(self.root / "state")
        self.assertEqual(second, [])
        dead = list((self.root / "state" / "dead-letters").glob("*.json"))
        self.assertEqual(len(dead), 1)

    def test_composed_only_is_left_alone(self):
        """Unsent-but-never-sending is safely composable again - not a human's
        problem, not a dead letter."""
        self.compose()
        self.assertEqual(outbound.reconcile_at_startup(self.root / "state"), [])
        self.assertEqual(list((self.root / "state" / "dead-letters").glob("*")), [])


class AThrottleIsNotAnUnknown(Base):
    """A throttle is the one non-terminal state whose outcome we KNOW: the
    platform declined to read the request, so nothing was delivered.

    Reconciliation classifies "sending with no terminal event" as ambiguous
    and dead-letters it, which is right for a send whose fate is genuinely
    unknown. Applied to a throttle it manufactures the exact outcome the
    throttle path exists to prevent - one restart later, quietly, with a
    dead-letter that claims uncertainty we do not have.
    """

    def _throttled(self):
        letter_id = self.compose()
        state = self.root / "state"
        outbound.record_event(state, letter_id, "sending")
        outbound.record_event(state, letter_id, "throttled",
                              detail="throttled with HTTP 429")
        return letter_id

    def test_reconcile_does_not_call_a_throttle_ambiguous(self):
        letter_id = self._throttled()
        verdicts = outbound.reconcile(self.root / "state")
        self.assertEqual(verdicts.get(letter_id), "throttled")

    def test_a_restart_leaves_a_throttled_letter_resumable(self):
        letter_id = self._throttled()
        outbound.reconcile_at_startup(self.root / "state")
        dead = self.root / "state" / "dead-letters"
        self.assertFalse(dead.exists() and any(dead.iterdir()),
                         "a throttle is provably undelivered - never dead-lettered")
        events = [p.name for p in (self.root / "state" / "receipts" / letter_id).iterdir()]
        self.assertFalse(any("dead" in name for name in events), events)

    def test_a_genuinely_ambiguous_send_still_dead_letters(self):
        """The healthy control. Without it, "throttles are spared" and
        "reconciliation stopped working" pass the same test."""
        letter_id = self.compose()
        outbound.record_event(self.root / "state", letter_id, "sending")
        outbound.reconcile_at_startup(self.root / "state")
        dead = self.root / "state" / "dead-letters"
        self.assertTrue(dead.exists() and any(dead.iterdir()))


class EventOrderIsNumericNotAlphabetical(Base):
    """Sequence numbers are written 1.. and read back sorted as TEXT, so at
    ten events "10-sending" sorts before "2-sending" and the tail of the list
    stops being the latest event.

    Harmless while reconcile only asked order-free questions - is any event
    terminal, is sending present. The deferred check made ordering
    load-bearing on an ordering that was never correct, which is how a
    genuinely in-flight send can read as resumable and be sent twice.
    """

    def _churn(self, letter_id, pairs):
        for event in pairs:
            outbound.record_event(self.root / "state", letter_id, event)

    def test_the_tenth_event_is_the_latest_one(self):
        letter_id = self.compose()
        # composed is 1; nine more takes us past the single digit boundary.
        self._churn(letter_id, ["sending", "throttled"] * 4 + ["sending"])
        self.assertEqual(outbound.reconcile(self.root / "state").get(letter_id),
                         "ambiguous",
                         "an in-flight retry must never read as resumable")

    def test_a_throttle_after_ten_events_is_still_deferred(self):
        letter_id = self.compose()
        self._churn(letter_id, ["sending", "throttled"] * 5)
        self.assertEqual(outbound.reconcile(self.root / "state").get(letter_id),
                         "throttled")

    def test_a_terminal_event_past_ten_still_ends_it(self):
        letter_id = self.compose()
        self._churn(letter_id, ["sending", "throttled"] * 4 + ["sending", "sent"])
        self.assertNotIn(letter_id, outbound.reconcile(self.root / "state"))


class ReconcileWalksOutbox(Base):
    """The outbound letter is
    the CLAIM, written and fsynced BEFORE its first receipt. A crash in that
    window leaves a letter with no receipts dir - invisible to the receipts-only
    walk. reconcile(state, outbox) closes that: a stem with NO receipts at all
    is a pre-receipt orphan -> unsent. A terminal letter is kept out of the
    verdict on purpose, so its absence must NOT be read as an orphan.
    This is a library primitive; the startup pass takes no outbox."""

    def _orphan_claim(self, body="the reply"):
        # Crash after the O_EXCL claim, before the first receipt: remove this
        # letter's receipts dir but leave the receipts parent (other letters).
        import shutil
        letter_id = self.compose(body=body)
        shutil.rmtree(self.root / "state" / "receipts" / letter_id)
        return letter_id

    def test_receipts_only_walk_is_blind_to_the_orphan(self):
        # Documents the gap the fix closes: without the outbox, the claim is
        # invisible to reconcile.
        self._orphan_claim()
        self.assertEqual(outbound.reconcile(self.root / "state"), {})

    def test_outbox_walk_classifies_the_orphan_unsent(self):
        letter_id = self._orphan_claim()
        verdicts = outbound.reconcile(self.root / "state", self.mail / "outbox")
        self.assertEqual(verdicts, {letter_id: "unsent"})

    def test_orphan_with_no_receipts_parent_is_classified(self):
        # The very first letter, crashing before its first receipt, leaves no
        # receipts directory at all - the parent never gets created.
        import shutil
        letter_id = self.compose()
        shutil.rmtree(self.root / "state" / "receipts")
        self.assertFalse((self.root / "state" / "receipts").exists())
        self.assertEqual(outbound.reconcile(self.root / "state"), {})
        verdicts = outbound.reconcile(self.root / "state", self.mail / "outbox")
        self.assertEqual(verdicts, {letter_id: "unsent"})

    def test_composed_only_letter_reads_unsent_once(self):
        # A composed-only letter WITH its receipt still reads unsent exactly
        # once; the outbox pass must not add a second entry or duplicate it.
        letter_id = self.compose()
        verdicts = outbound.reconcile(self.root / "state", self.mail / "outbox")
        self.assertEqual(verdicts, {letter_id: "unsent"})

    def test_terminal_letters_are_not_reclassified_by_the_outbox_walk(self):
        # A terminal state is represented by ABSENCE from the
        # receipts verdict, not by an entry. The outbox walk must not read that
        # absence as "no receipts" and stamp a delivered/closed letter unsent.
        import shutil
        for outcome in ("sent", "refused", "ambiguous", "dead"):
            with self.subTest(outcome=outcome):
                lid = self.compose(body=f"body-{outcome}")
                try:
                    if outcome == "sent":
                        outbound.record_event(self.root / "state", lid, "sending")
                        outbound.record_event(self.root / "state", lid, "sent",
                                              platform_message_id="1")
                    else:
                        outbound.record_event(self.root / "state", lid, outcome)
                    without = outbound.reconcile(self.root / "state")
                    withbox = outbound.reconcile(self.root / "state",
                                                 self.mail / "outbox")
                    self.assertEqual(withbox, without,
                                     f"{outcome}: outbox walk changed the verdict")
                finally:
                    (self.mail / "outbox" / f"{lid}.md").unlink(missing_ok=True)
                    shutil.rmtree(self.root / "state" / "receipts" / lid,
                                  ignore_errors=True)

    def test_directory_or_symlink_named_md_is_not_a_claim(self):
        # Only an ordinary file is a claim. A directory or a
        # symlink named "*.md" in the outbox must be ignored, not classified.
        outbox = self.mail / "outbox"
        (outbox / "directory.md").mkdir()
        (outbox / "dangling.md").symlink_to(outbox / "does-not-exist")
        real = outbox / "target.md"
        real.write_text("x", encoding="utf-8")
        (outbox / "link-to-real.md").symlink_to(real)
        verdicts = outbound.reconcile(self.root / "state", outbox)
        self.assertEqual(verdicts, {"target": "unsent"})

    def test_the_claim_persists_so_a_resend_meets_already_claimed(self):
        # Visibility is not recovery: the claim persists, so a fresh send for
        # the same source meets AlreadyClaimed at compose. Guards against anyone
        # "fixing" visibility by faking a send path. (compose-level, not the CLI
        # entry point.)
        self._orphan_claim()
        with self.assertRaises(AlreadyClaimed):
            self.compose()
