"""A1 grant lifecycle: private, fail-closed, explicit create only.

Fixture chat ids are labelled fakes, not production tokens.
"""
import errno
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.grant import store as grants  # noqa: E402


CHAT = "fixture-chat"
PLATFORM = "telegram"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **kw):
        return grants.create(self.state, PLATFORM, CHAT, **kw)


class ExplicitCreate(Base):
    def test_create_writes_private_record(self):
        rec = self.create()
        path = self.state / "grants" / f"{rec['grant_id']}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_grant_id_is_at_least_128_bits(self):
        rec = self.create()
        self.assertGreaterEqual(len(bytes.fromhex(rec["grant_id"])), 16)

    def test_record_carries_binding_and_policy(self):
        rec = self.create()
        self.assertEqual(rec["platform"], PLATFORM)
        self.assertEqual(rec["chat_id"], CHAT)
        self.assertTrue(rec["enabled"])
        self.assertEqual(rec["binding_key"], grants.binding_key(PLATFORM, CHAT))
        self.assertEqual(rec["per_day"], 3)
        self.assertEqual(rec["per_hour"], 2)
        self.assertEqual(rec["timezone"], "Europe/London")
        self.assertEqual(rec["max_queued"], 5)
        self.assertEqual(rec["max_body"], 4000)

    def test_missing_on_use_does_not_create(self):
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, "0" * 32)
        self.assertFalse((self.state / "grants").exists())


class FailClosed(Base):
    def test_missing_grant_is_policy_error(self):
        self.create()
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, "ab" * 16)

    def test_corrupt_grant_is_policy_error(self):
        rec = self.create()
        path = self.state / "grants" / f"{rec['grant_id']}.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, rec["grant_id"])

    def test_invalid_utf8_is_policy_error(self):
        rec = self.create()
        path = self.state / "grants" / f"{rec['grant_id']}.json"
        path.write_bytes(b"\xff\xfe not utf-8")
        os.chmod(path, 0o600)
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, rec["grant_id"])

    def test_disabled_grant_fails_validate(self):
        rec = self.create()
        grants.disable(self.state, rec["grant_id"])
        loaded = grants.load(self.state, rec["grant_id"])
        with self.assertRaises(grants.PolicyError):
            grants.validate(loaded)

    def test_expired_grant_fails_validate(self):
        rec = self.create(expiry=1)  # 1970
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec, now=time.time())

    def test_inconsistent_policy_fails_validate(self):
        rec = self.create()
        rec["per_day"] = 99
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)

    def test_malformed_expiry_is_policy_error_not_valueerror(self):
        rec = self.create()
        rec["expiry"] = "notanumber"
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)
        rec["expiry"] = []
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)

    def test_allowlisted_but_not_granted_cannot_target(self):
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_diagnostics_redact_chat_and_grant_id(self):
        rec = self.create()
        try:
            grants.load(self.state, "ff" * 16)
        except grants.PolicyError as exc:
            msg = str(exc)
            self.assertNotIn(CHAT, msg)
            self.assertNotIn(rec["grant_id"], msg)
            self.assertNotIn("ff" * 16, msg)
        grants.disable(self.state, rec["grant_id"])
        try:
            grants.validate(grants.load(self.state, rec["grant_id"]))
        except grants.PolicyError as exc:
            self.assertNotIn(CHAT, str(exc))
            self.assertNotIn(rec["grant_id"], str(exc))


class StrictTypes(Base):
    def _valid(self):
        rec = self.create()
        grants.validate(rec)
        return rec

    def test_truthy_enabled_does_not_authorize(self):
        rec = self._valid()
        rec["enabled"] = "false"
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)
        rec["enabled"] = [False]
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)

    def test_non_finite_expiry_does_not_authorize(self):
        rec = self._valid()
        for bad in ("NaN", "Infinity", "-Infinity", "inf", 1e1000):
            rec["expiry"] = bad
            with self.assertRaises(grants.PolicyError):
                grants.validate(rec)

    def test_missing_expiry_key_is_not_unlimited(self):
        rec = self._valid()
        rec["expiry"] = 1
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec, now=time.time())
        del rec["expiry"]
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec, now=time.time())

    def test_missing_created_and_container_platform_fail(self):
        rec = self._valid()
        del rec["created"]
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)
        rec = self._valid()
        rec["platform"] = ["telegram"]
        with self.assertRaises(grants.PolicyError):
            grants.validate(rec)


class PathAndTemp(Base):
    def test_world_readable_grants_dir_is_refused(self):
        self.create()
        os.chmod(self.state / "grants", 0o755)
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_load_rejects_path_traversal(self):
        rec = self.create()
        planted = json.loads(
            (self.state / "grants" / f"{rec['grant_id']}.json").read_text())
        planted["grant_id"] = "../outside-fixture"
        outside = self.state / "outside-fixture.json"
        outside.write_text(json.dumps(planted), encoding="utf-8")
        os.chmod(outside, 0o600)
        marker = self.state / "outside-fixture.ready"
        marker.write_bytes(b"")
        os.chmod(marker, 0o600)
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, "../outside-fixture")
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, "ab/cd")

    def test_requested_id_must_match_filename_and_record(self):
        rec = self.create()
        path = self.state / "grants" / f"{rec['grant_id']}.json"
        other = "ab" * 16
        data = json.loads(path.read_text())
        data["grant_id"] = other
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, rec["grant_id"])
        copy = self.state / "grants" / "fixture-copy.json"
        copy.write_text(path.read_text())
        os.chmod(copy, 0o600)
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_load_rejects_symlink(self):
        rec = self.create()
        real = self.state / "grants" / f"{rec['grant_id']}.json"
        outside = self.state / "outside.json"
        outside.write_text(real.read_text())
        os.chmod(outside, 0o600)
        real.unlink()
        real.symlink_to(outside)
        with self.assertRaises(grants.PolicyError):
            grants.load(self.state, rec["grant_id"])

    def test_temp_symlink_does_not_clobber(self):
        rec = self.create()
        victim = self.state / "victim"
        victim.write_bytes(b"UNCHANGED")
        nonce = "cafecafe" * 2
        tmp = self.state / "grants" / f".{rec['grant_id']}.{os.getpid()}.{nonce}.partial"
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(victim)
        with mock.patch.object(grants.secrets, "token_hex", return_value=nonce):
            grants.disable(self.state, rec["grant_id"])
        self.assertEqual(victim.read_bytes(), b"UNCHANGED")
        self.assertIs(grants.load(self.state, rec["grant_id"])["enabled"], False)

    def test_stale_world_readable_temp_does_not_publish_0644(self):
        rec = self.create()
        nonce = "cafecafe" * 2
        tmp = self.state / "grants" / f".{rec['grant_id']}.{os.getpid()}.{nonce}.partial"
        tmp.write_text("stale")
        os.chmod(tmp, 0o644)
        with mock.patch.object(grants.secrets, "token_hex", return_value=nonce):
            grants.disable(self.state, rec["grant_id"])
        dest = self.state / "grants" / f"{rec['grant_id']}.json"
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)

    def test_failed_publication_is_not_authorizing(self):
        rec = self.create()
        grants_dir = self.state / "grants"
        orig = grants._fsync_dir

        def boom(path):
            others = [p for p in grants_dir.glob("*.json") if p.stem != rec["grant_id"]]
            if others and path == grants_dir:
                raise OSError("injected")
            return orig(path)

        grants._fsync_dir = boom
        try:
            with self.assertRaises(grants.PolicyError):
                grants.create(self.state, PLATFORM, "fixture-chat-2")
        finally:
            grants._fsync_dir = orig
        others = [p for p in grants_dir.glob("*.json") if p.stem != rec["grant_id"]]
        self.assertEqual(others, [])
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, "fixture-chat-2")

    def test_load_rejects_nonprivate_grants_directory(self):
        rec = self.create()
        os.chmod(self.state / "grants", 0o755)
        with self.assertRaises(grants.PolicyError):
            grants.validate(grants.load(self.state, rec["grant_id"]))

    def test_load_rejects_symlink_grants_directory(self):
        rec = self.create()
        directory = self.state / "grants"
        outside = pathlib.Path(self.tmp.name) / "outside-grants-fixture"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(grants.PolicyError):
            grants.validate(grants.load(self.state, rec["grant_id"]))

    def test_disable_does_not_write_through_symlink_grants_directory(self):
        rec = self.create()
        directory = self.state / "grants"
        outside = pathlib.Path(self.tmp.name) / "outside-grants-fixture"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        target = outside / f"{rec['grant_id']}.json"
        before = target.read_bytes()
        try:
            grants.disable(self.state, rec["grant_id"])
        except grants.PolicyError:
            pass
        self.assertEqual(target.read_bytes(), before)

    def test_create_does_not_chmod_or_write_symlink_grants_directory(self):
        self.state.mkdir()
        outside = pathlib.Path(self.tmp.name) / "outside-dir-fixture"
        outside.mkdir(mode=0o755)
        outside.chmod(0o755)
        (self.state / "grants").symlink_to(outside, target_is_directory=True)
        try:
            self.create()
        except grants.PolicyError:
            pass
        self.assertEqual(
            (stat.S_IMODE(outside.stat().st_mode), len(list(outside.iterdir()))),
            (0o755, 0))


class PublicationProtocol(Base):
    GID = "ab" * 16

    def test_reader_cannot_accept_create_before_failed_final_sync(self):
        orig = grants._fsync_dir
        observed = []

        def fail_after_reader(path):
            if path == self.state / "grants":
                try:
                    observed.append(
                        grants.require_granted(self.state, PLATFORM, CHAT)["enabled"])
                except grants.PolicyError:
                    observed.append(False)
                raise OSError("injected")
            return orig(path)

        grants._fsync_dir = fail_after_reader
        try:
            with self.assertRaises(grants.PolicyError):
                self.create()
        finally:
            grants._fsync_dir = orig
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)
        self.assertEqual(observed, [False])

    def test_process_death_before_directory_sync_leaves_no_usable_grant(self):
        child = (
            "import os, pathlib, sys\n"
            "from alb.grant import store as grants\n"
            "state = pathlib.Path(sys.argv[1])\n"
            "real = grants._fsync_dir\n"
            "def die_at_final_sync(path):\n"
            "    if path == state / 'grants':\n"
            "        os._exit(73)\n"
            "    return real(path)\n"
            "grants._fsync_dir = die_at_final_sync\n"
            "grants.create(state, 'telegram', 'fixture-chat')\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", child, str(self.state)],
            capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(len(list((self.state / "grants").glob("*.json"))), 1)
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_competing_create_cannot_overwrite_completed_winner(self):
        real_open = grants.os.open
        switched = False
        winner = []
        outer = []

        def switch_before_temp_open(path, flags, *args, **kwargs):
            nonlocal switched
            if str(path).endswith(".partial") and flags & os.O_CREAT and not switched:
                switched = True
                with mock.patch.object(grants.secrets, "token_hex", return_value=self.GID):
                    winner.append(grants.create(self.state, PLATFORM, "fixture-B"))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(grants.os, "open", side_effect=switch_before_temp_open):
            try:
                with mock.patch.object(grants.secrets, "token_hex", return_value=self.GID):
                    outer.append(grants.create(self.state, PLATFORM, CHAT))
            except grants.PolicyError:
                pass
        self.assertEqual(len(winner), 1)
        stored = grants.load(self.state, self.GID)
        self.assertEqual(stored, winner[0])
        self.assertEqual(outer, [])

    def test_refused_collision_cannot_unlink_active_disable_stage(self):
        rec = self.create()
        gid = rec["grant_id"]
        real_replace = grants.os.replace
        refused = []

        def collide_during_disable(source, target):
            try:
                with mock.patch.object(grants.secrets, "token_hex", return_value=gid):
                    grants.create(self.state, PLATFORM, "fixture-other")
            except grants.PolicyError:
                refused.append(True)
            return real_replace(source, target)

        with mock.patch.object(grants.os, "replace", side_effect=collide_during_disable):
            try:
                grants.disable(self.state, gid)
            except grants.PolicyError:
                pass
        self.assertEqual(refused, [True])
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_existing_destination_refused_without_changes(self):
        rec = self.create()
        dest = self.state / "grants" / f"{rec['grant_id']}.json"
        before = dest.read_bytes()
        with mock.patch.object(grants.secrets, "token_hex", return_value=rec["grant_id"]):
            with self.assertRaises(grants.PolicyError):
                grants.create(self.state, PLATFORM, "fixture-other")
        self.assertEqual(dest.read_bytes(), before)

    def _create_forced(self, gid=None):
        gid = gid or self.GID
        real = grants.secrets.token_hex
        with mock.patch.object(grants.secrets, "token_hex",
                               side_effect=lambda n: gid if n == 16 else real(n)):
            return grants.create(self.state, PLATFORM, CHAT)

    def test_marker_file_sync_error_removes_owned_marker(self):
        real_open, real_fsync = grants.os.open, grants.os.fsync
        ready = self.state / "grants" / f"{self.GID}.ready"
        dest = self.state / "grants" / f"{self.GID}.json"
        ready_fd = []
        fired = []

        def track_open(path, flags, *args, **kw):
            fd = real_open(path, flags, *args, **kw)
            if pathlib.Path(path) == ready:
                ready_fd.append(fd)
            return fd

        def fail_marker_sync(fd):
            if ready_fd and fd == ready_fd[0] and not fired:
                fired.append(True)
                raise OSError(errno.EIO, "synthetic ready-file fsync")
            return real_fsync(fd)

        with mock.patch.object(grants.os, "open", side_effect=track_open), \
             mock.patch.object(grants.os, "fsync", side_effect=fail_marker_sync):
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        self.assertEqual(fired, [True])
        self.assertFalse(dest.exists())
        self.assertFalse(ready.exists())
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_leftover_ready_without_dest_is_quarantined(self):
        grants_dir = self.state / "grants"
        grants_dir.mkdir(parents=True, mode=0o700)
        os.chmod(grants_dir, 0o700)
        ready = grants_dir / f"{self.GID}.ready"
        dest = grants_dir / f"{self.GID}.json"
        ready.write_bytes(b"")
        os.chmod(ready, 0o600)
        orig = grants._fsync_dir
        saw_dest = []

        def watch(path):
            if dest.exists():
                saw_dest.append(True)
            return orig(path)

        grants._fsync_dir = watch
        try:
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        finally:
            grants._fsync_dir = orig
        self.assertEqual(saw_dest, [])
        self.assertTrue(ready.exists())
        self.assertFalse(dest.exists())
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)

    def test_orphan_marker_cannot_authorize_reused_id_before_payload_sync(self):
        real_open, real_fsync = grants.os.open, grants.os.fsync
        ready = self.state / "grants" / f"{self.GID}.ready"
        dest = self.state / "grants" / f"{self.GID}.json"
        ready_fd = []
        fired = []

        def track_open(path, flags, *args, **kw):
            fd = real_open(path, flags, *args, **kw)
            if pathlib.Path(path) == ready:
                ready_fd.append(fd)
            return fd

        def fail_marker_sync(fd):
            if ready_fd and fd == ready_fd[0] and not fired:
                fired.append(True)
                raise OSError(errno.EIO, "synthetic ready-file fsync")
            return real_fsync(fd)

        with mock.patch.object(grants.os, "open", side_effect=track_open), \
             mock.patch.object(grants.os, "fsync", side_effect=fail_marker_sync):
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        self.assertFalse(dest.exists())
        orig = grants._fsync_dir
        observed = []

        def inspect_retry(path):
            if path == self.state / "grants" and dest.exists():
                try:
                    observed.append(
                        grants.require_granted(self.state, PLATFORM, CHAT)["enabled"])
                except grants.PolicyError:
                    observed.append(False)
                raise OSError(errno.EIO, "synthetic retry before payload directory sync")
            return orig(path)

        grants._fsync_dir = inspect_retry
        try:
            try:
                self._create_forced()
            except grants.PolicyError:
                pass
        finally:
            grants._fsync_dir = orig
        self.assertNotIn(True, observed)

    def test_preexisting_unique_temp_is_exclusive(self):
        grants_dir = self.state / "grants"
        grants_dir.mkdir(parents=True, mode=0o700)
        os.chmod(grants_dir, 0o700)
        nonce = "cafecafe" * 2
        tmp = grants_dir / f".{self.GID}.{os.getpid()}.{nonce}.partial"
        tmp.write_text("occupied")
        os.chmod(tmp, 0o644)
        with mock.patch.object(grants, "_unlink_stale"), \
             mock.patch.object(grants.secrets, "token_hex",
                               side_effect=lambda n: self.GID if n == 16 else nonce):
            with self.assertRaises(grants.PolicyError):
                grants.create(self.state, PLATFORM, CHAT)
        self.assertFalse((grants_dir / f"{self.GID}.json").exists())

    def test_marker_lookup_eio_is_not_absence(self):
        real_lstat = grants.os.lstat
        fired = []
        ready = self.state / "grants" / f"{self.GID}.ready"
        dest = self.state / "grants" / f"{self.GID}.json"

        def fail_one_lookup(path, *args, **kwargs):
            if pathlib.Path(path) == ready and not fired:
                fired.append(True)
                raise OSError(errno.EIO, "synthetic marker lookup I/O error")
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(grants.os, "lstat", side_effect=fail_one_lookup):
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        self.assertEqual(fired, [True])
        self.assertFalse(dest.exists())
        self.assertFalse(ready.exists())

    def test_unreadable_leftover_marker_cannot_certify_new_payload(self):
        grants_dir = self.state / "grants"
        grants_dir.mkdir(parents=True, mode=0o700)
        os.chmod(grants_dir, 0o700)
        ready = grants_dir / f"{self.GID}.ready"
        dest = grants_dir / f"{self.GID}.json"
        ready.write_bytes(b"fixture-leftover-marker")
        os.chmod(ready, 0o600)
        real_lstat = grants.os.lstat
        orig = grants._fsync_dir
        failed_lookup = []
        observed = []

        def fail_one_lookup(path, *args, **kwargs):
            if pathlib.Path(path) == ready and not failed_lookup:
                failed_lookup.append(True)
                raise OSError(errno.EIO, "synthetic existing-marker lookup failure")
            return real_lstat(path, *args, **kwargs)

        def stop_before_payload_sync(path):
            if path == grants_dir and dest.exists():
                try:
                    observed.append(
                        grants.require_granted(self.state, PLATFORM, CHAT)["enabled"])
                except grants.PolicyError:
                    observed.append(False)
                raise OSError(errno.EIO, "synthetic stop before payload directory sync")
            return orig(path)

        with mock.patch.object(grants.os, "lstat", side_effect=fail_one_lookup), \
             mock.patch.object(grants, "_fsync_dir", side_effect=stop_before_payload_sync):
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        self.assertEqual(failed_lookup, [True])
        self.assertEqual(ready.read_bytes(), b"fixture-leftover-marker")
        self.assertEqual(observed, [])

    def test_failed_temp_exclusive_open_preserves_other_owner_file(self):
        real_open = grants.os.open
        other_stage = []
        contents = b"fixture-other-owner-stage"

        def collide_at_open(path, flags, *args, **kwargs):
            path = pathlib.Path(path)
            if path.name.endswith(".partial") and flags & os.O_CREAT and not other_stage:
                other_stage.append(path)
                fd = real_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, contents)
                finally:
                    os.close(fd)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(grants.os, "open", side_effect=collide_at_open):
            with self.assertRaises(grants.PolicyError):
                self._create_forced()
        self.assertEqual(len(other_stage), 1)
        self.assertTrue(other_stage[0].exists())
        self.assertEqual(other_stage[0].read_bytes(), contents)
        self.assertFalse((self.state / "grants" / f"{self.GID}.json").exists())

    def test_early_marker_refusal_does_not_delete_unclaimed_stage(self):
        grants_dir = self.state / "grants"
        grants_dir.mkdir(parents=True, mode=0o700)
        os.chmod(grants_dir, 0o700)
        ready = grants_dir / f"{self.GID}.ready"
        ready.write_bytes(b"fixture-leftover-marker")
        os.chmod(ready, 0o600)
        nonce = "ef" * 8
        stage = grants_dir / f".{self.GID}.{os.getpid()}.{nonce}.partial"
        stage.write_bytes(b"fixture-existing-stage")
        os.chmod(stage, 0o600)
        real = grants.secrets.token_hex
        with mock.patch.object(grants.secrets, "token_hex",
                               side_effect=lambda n: self.GID if n == 16 else nonce):
            with self.assertRaises(grants.PolicyError):
                grants.create(self.state, PLATFORM, CHAT)
        self.assertTrue(stage.exists())
        self.assertEqual(stage.read_bytes(), b"fixture-existing-stage")
        self.assertEqual(ready.read_bytes(), b"fixture-leftover-marker")
        self.assertFalse((grants_dir / f"{self.GID}.json").exists())

    def test_unreadable_marker_refuses_reader(self):
        """No id collision: EIO on one marker must not skip it and accept the other."""
        self._create_forced()
        second_id = "cd" * 16
        real = grants.secrets.token_hex
        with mock.patch.object(
                grants.secrets, "token_hex",
                side_effect=lambda n: second_id if n == 16 else real(n)):
            grants.create(self.state, PLATFORM, CHAT, now=1)
        with self.assertRaises(grants.PolicyError):
            grants.require_granted(self.state, PLATFORM, CHAT)
        ready = self.state / "grants" / f"{self.GID}.ready"
        real_lstat = grants.os.lstat
        failed = []

        def unavailable(path, *args, **kwargs):
            if pathlib.Path(path) == ready:
                failed.append(True)
                raise OSError(errno.EIO, "synthetic persistent marker lookup failure")
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(grants.os, "lstat", side_effect=unavailable):
            with self.assertRaises(grants.PolicyError):
                grants.require_granted(self.state, PLATFORM, CHAT)
        self.assertGreaterEqual(len(failed), 1)


class SetupDoesNotMintAuthority(Base):
    def test_wizard_does_not_import_grant_create(self):
        from alb.setup import wizard
        src = pathlib.Path(wizard.__file__).read_text(encoding="utf-8")
        self.assertNotIn("alb.grant", src)
        self.assertNotIn("grant.create", src)


if __name__ == "__main__":
    unittest.main()
