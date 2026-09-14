"""Inbound photo carry + outbound allowlisted sendPhoto."""
import errno
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fake_platform import FakePlatform  # noqa: E402
from alb.grant import store as grants  # noqa: E402
from alb.initiate import destinations, send as initiate  # noqa: E402
from alb.letter import store as letters  # noqa: E402
from alb.media import attach, inspect, store as media_store  # noqa: E402
from alb.poller import loop  # noqa: E402
from alb.send import reply  # noqa: E402

# 1×1 RGBA PNG. Magic + IHDR only; no ALB decode.
PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class PhotoPlatform(FakePlatform):
    def __init__(self, updates, data=None, fail=False):
        super().__init__(updates)
        self.data = data
        self.fail = fail
        self.downloads = 0

    def download_photo(self, file_id):
        self.downloads += 1
        if self.fail:
            raise RuntimeError("synthetic download failure")
        return self.data


def photo_update(uid, chat, caption="", file_id="file-1"):
    return {
        "update_id": uid, "chat_id": chat, "text": "",
        "caption": caption, "photo_file_id": file_id, "media_kind": "photo",
    }


class Inspect(unittest.TestCase):
    def test_png_magic_and_geometry(self):
        kind, w, h = inspect.preflight(PNG_1x1, "")
        self.assertEqual(kind, inspect.PNG)
        self.assertEqual((w, h), (1, 1))

    def test_gif_is_refused(self):
        with self.assertRaises(inspect.MediaError):
            inspect.detect(b"GIF89a" + b"\x00" * 20)


class Inbound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.ledger = self.root / "delivered.json"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, platform):
        return loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                              state=self.state)

    def test_photo_only_is_not_a_blank_letter(self):
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)
        ids = self._run(platform)
        self.assertEqual(len(ids), 1)
        letter = letters.resolve(self.inbox, ids[0])
        self.assertTrue(letter.meta.get("media_asset_id"))
        self.assertEqual(letter.meta.get("media_kind"), "image")
        self.assertEqual(letter.meta.get("media_type"), "image/png")
        blob = (self.inbox / f"{ids[0]}.md").read_text(encoding="utf-8")
        self.assertNotIn("file-1", blob)
        self.assertNotIn(str(self.state / "media"), blob)
        data = media_store.load_content(
            self.state, ids[0], letter.meta["media_asset_id"])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(platform.staged, 1)

    def test_caption_is_preserved(self):
        platform = PhotoPlatform(
            [photo_update(1, "111", caption="see this")], data=PNG_1x1)
        ids = self._run(platform)
        letter = letters.resolve(self.inbox, ids[0])
        self.assertEqual(letter.body, "see this")

    def test_failed_download_lands_a_marker_and_acks(self):
        platform = PhotoPlatform([photo_update(1, "111", caption="x")], fail=True)
        ids = self._run(platform)
        self.assertEqual(len(ids), 1)
        letter = letters.resolve(self.inbox, ids[0])
        self.assertIn("[photo unavailable]", letter.body)
        self.assertIn("x", letter.body)
        self.assertEqual(letter.meta.get("media_status"), "fetch-failed")
        self.assertFalse(letter.meta.get("media_asset_id"))
        self.assertEqual(platform.staged, 1)

    def test_voice_gets_honesty_marker(self):
        item = {"update_id": 2, "chat_id": "111", "text": "", "caption": "",
                "media_kind": "voice", "photo_file_id": ""}
        ids = self._run(FakePlatform([item]))
        letter = letters.resolve(self.inbox, ids[0])
        self.assertIn("[voice received; content not carried]", letter.body)

    def test_denied_photo_is_silence(self):
        platform = PhotoPlatform([photo_update(1, "999")], data=PNG_1x1)
        ids = self._run(platform)
        self.assertEqual(list(ids), [])
        self.assertEqual(list(self.inbox.glob("*.md")), [])
        self.assertEqual(platform.staged, 1)


class Outbound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.outbox = self.root / "outbox"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["fixture-chat"]}), encoding="utf-8")
        rec = grants.create(self.state, "telegram", "fixture-chat")
        destinations.bind(self.state, "owner", rec["grant_id"])
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        (self.state / "attach-roots.json").write_text(
            json.dumps({"roots": [str(self.allowed)]}), encoding="utf-8")
        self.png = self.allowed / "pic.png"
        self.png.write_bytes(PNG_1x1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outside_root_is_refused_before_send(self):
        outside = self.root / "secret.png"
        outside.write_bytes(PNG_1x1)
        with self.assertRaises(inspect.MediaError):
            attach.read_allowed(self.state, str(outside))

    def test_hard_link_inside_root_to_outside_is_accepted_by_design(self):
        """attach-roots.json is the trust boundary, not the inode. A hard
        link whose directory entry sits inside an allowlisted root is
        sendable even if the same inode is also linked outside. Do not
        'fix' this by refusing nlink>1."""
        outside = self.root / "secret.png"
        outside.write_bytes(PNG_1x1)
        inside = self.allowed / "alias.png"
        os.link(outside, inside)
        data = attach.read_allowed(self.state, str(inside))
        self.assertEqual(data[:8], PNG_1x1[:8])

    def test_symlink_is_refused(self):
        link = self.allowed / "link.png"
        link.symlink_to(self.png)
        with self.assertRaises(inspect.MediaError):
            attach.read_allowed(self.state, str(link))

    def test_send_photo_uses_staged_bytes_not_path(self):
        class Sender:
            def __init__(self):
                self.calls = []

            def send(self, chat_id, text):
                raise AssertionError("sendMessage used for a photo")

            def send_photo(self, chat_id, data, caption=""):
                self.calls.append((chat_id, data[:8], caption))
                return "99"

        sender = Sender()
        oid = initiate.send_initiated(
            sender, self.state, self.outbox, self.allow,
            label="owner", intent_id="pic-1", text="caption here",
            reason="dogfood photo", seat="fixture-agent",
            photo_path=str(self.png))
        self.assertEqual(sender.calls, [("fixture-chat", PNG_1x1[:8], "caption here")])
        blob = (self.outbox / f"{oid}.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.png), blob)
        self.assertNotIn("fixture-chat", blob)
        self.assertIn("media_asset_id:", blob)
        self.png.write_bytes(b"mutated")
        staged = media_store.load_outbound(self.state, oid)
        self.assertEqual(staged[:8], PNG_1x1[:8])

    def test_resume_refuses_altered_staged_bytes(self):
        class Sender:
            def __init__(self):
                self.calls = []
                self.throttle = True

            def send(self, chat_id, text):
                self.calls.append(("text", text))
                return "1"

            def send_photo(self, chat_id, data, caption=""):
                self.calls.append(("photo", bytes(data)))
                if self.throttle:
                    raise reply.Throttled("synthetic throttle", retry_after=1)
                return "2"

        sender = Sender()
        kwargs = dict(label="owner", intent_id="photo-probe", text="caption",
                      reason="synthetic probe", seat="fixture-agent",
                      photo_path=str(self.png))
        with self.assertRaises(reply.Throttled):
            initiate.send_initiated(sender, self.state, self.outbox, self.allow,
                                    **kwargs)
        content = list((self.state / "media").glob("*/*/content"))
        self.assertEqual(len(content), 1)
        content[0].write_bytes(PNG_1x1 + b"tamper")
        sender.throttle = False
        with self.assertRaises(initiate.AuthorityRefused):
            initiate.send_initiated(sender, self.state, self.outbox, self.allow,
                                    **kwargs)
        self.assertEqual(sender.calls[-1][0], "photo")
        self.assertNotIn(("text", "caption"), sender.calls)

    def test_resume_refuses_missing_staged_image(self):
        class Sender:
            def __init__(self):
                self.calls = []
                self.throttle = True

            def send(self, chat_id, text):
                self.calls.append(("text", text))
                return "1"

            def send_photo(self, chat_id, data, caption=""):
                self.calls.append(("photo", bytes(data)))
                if self.throttle:
                    raise reply.Throttled("synthetic throttle", retry_after=1)
                return "2"

        sender = Sender()
        kwargs = dict(label="owner", intent_id="photo-probe-2", text="caption",
                      reason="synthetic probe", seat="fixture-agent",
                      photo_path=str(self.png))
        with self.assertRaises(reply.Throttled):
            initiate.send_initiated(sender, self.state, self.outbox, self.allow,
                                    **kwargs)
        for path in (self.state / "media").glob("*/*/content"):
            path.unlink()
        sender.throttle = False
        with self.assertRaises(initiate.AuthorityRefused):
            initiate.send_initiated(sender, self.state, self.outbox, self.allow,
                                    **kwargs)
        self.assertNotIn(("text", "caption"), sender.calls)


class InboundPromote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.ledger = self.root / "delivered.json"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dedup_retry_promotes_staged_asset(self):
        from unittest import mock
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)
        real = media_store.promote

        def crash_first(state, update_id, letter_id, asset_id=None):
            raise RuntimeError("after durable letter, before asset promotion")

        with mock.patch.object(media_store, "promote", side_effect=crash_first):
            with self.assertRaises(RuntimeError):
                loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                               state=self.state)
        paths = list(self.inbox.glob("*.md"))
        self.assertEqual(len(paths), 1)
        letter = letters.resolve(self.inbox, paths[0].stem)
        result = loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                                state=self.state)
        self.assertEqual(result.duplicate, 1)
        data = media_store.load_content(
            self.state, paths[0].stem, letter.meta["media_asset_id"])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_retry_download_fail_still_promotes_stored_asset(self):
        from unittest import mock
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)

        def crash_first(state, update_id, letter_id, asset_id=None):
            raise RuntimeError("after durable letter, before asset promotion")

        with mock.patch.object(media_store, "promote", side_effect=crash_first):
            with self.assertRaises(RuntimeError):
                loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                               state=self.state)
        paths = list(self.inbox.glob("*.md"))
        self.assertEqual(len(paths), 1)
        letter = letters.resolve(self.inbox, paths[0].stem)
        advertised = letter.meta["media_asset_id"]
        platform.fail = True
        result = loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                                state=self.state)
        self.assertEqual(result.duplicate, 1)
        data = media_store.load_content(self.state, paths[0].stem, advertised)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(platform.staged, 1)

    def test_transient_photo_download_does_not_ack(self):
        from alb.adapters.telegram.api import TransientFailure

        class Boom(PhotoPlatform):
            def download_photo(self, file_id):
                raise TransientFailure("synthetic 429", retry_after=1)

        platform = Boom([photo_update(1, "111")])
        with self.assertRaises(TransientFailure):
            loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                           state=self.state)
        self.assertIsNone(platform.staged)
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    def test_transient_subclass_propagates(self):
        from alb.adapters.telegram.api import TransientFailure

        class Retryable(TransientFailure):
            pass

        class Boom(PhotoPlatform):
            def download_photo(self, file_id):
                raise Retryable("synthetic subclass deferral")

        platform = Boom([photo_update(1, "111")])
        with self.assertRaises(Retryable):
            loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                           state=self.state)
        self.assertIsNone(platform.staged)
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    def test_foreign_class_named_transientfailure_is_marked(self):
        class TransientFailure(Exception):
            pass

        class Boom(PhotoPlatform):
            def download_photo(self, file_id):
                raise TransientFailure("not the adapter type")

        platform = Boom([photo_update(1, "111")])
        ids = loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                             state=self.state)
        self.assertEqual(len(ids), 1)
        letter = letters.resolve(self.inbox, ids[0])
        self.assertEqual(letter.meta.get("media_status"), "fetch-failed")
        self.assertEqual(platform.staged, 1)

    def test_discard_staging_refuses_path_shaped_ids(self):
        media = self.state / "media"
        canary_dir = media / "kept-letter" / ("aa" * 16)
        canary_dir.mkdir(parents=True)
        canary = canary_dir / "content"
        canary.write_bytes(b"KEEP")
        staging = media / "staging" / "1"
        staging.mkdir(parents=True)
        (staging / "x").write_text("staged")
        for bad in ("..", "", "../1", "1/../2", "abc", "-1"):
            with self.assertRaises(media_store.MediaError):
                media_store.discard_staging(self.state, bad)
            self.assertEqual(canary.read_bytes(), b"KEEP")
            self.assertTrue(staging.exists())
        media_store.discard_staging(self.state, "1")
        self.assertFalse(staging.exists())
        self.assertEqual(canary.read_bytes(), b"KEEP")

    def test_duplicate_redelivery_clears_staging(self):
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)
        loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                       state=self.state)
        loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                       state=self.state)
        staging = self.state / "media" / "staging"
        leftover = list(staging.rglob("*")) if staging.exists() else []
        self.assertEqual(leftover, [])

    def test_empty_staged_content_does_not_ack(self):
        from unittest import mock
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)

        def crash_first(state, update_id, letter_id, asset_id=None):
            raise RuntimeError("after durable letter, before asset promotion")

        with mock.patch.object(media_store, "promote", side_effect=crash_first):
            with self.assertRaises(RuntimeError):
                loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                               state=self.state)
        paths = list(self.inbox.glob("*.md"))
        letter = letters.resolve(self.inbox, paths[0].stem)
        advertised = letter.meta["media_asset_id"]
        content = (self.state / "media" / "staging" / "1" / advertised / "content")
        content.unlink()
        self.assertTrue(content.parent.is_dir())
        platform.fail = True
        result = loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                                state=self.state)
        self.assertEqual(result.duplicate, 1)
        self.assertEqual(platform.staged, 1)

    def test_retry_still_fsyncs_visible_dest(self):
        from unittest import mock
        from alb.initiate import durable
        platform = PhotoPlatform([photo_update(1, "111")], data=PNG_1x1)
        real = durable.fsync_dir

        def boom(path):
            path = pathlib.Path(path)
            if path.parent.name == "media" and path.name not in ("staging", "media"):
                raise OSError("injected dest fsync")
            return real(path)

        with mock.patch.object(media_store.durable, "fsync_dir", side_effect=boom):
            result = loop.poll_once(platform, self.inbox, self.ledger, self.allow,
                                    state=self.state)
            self.assertEqual(len(result), 1)
            self.assertEqual(platform.staged, 1)
            letter = letters.resolve(self.inbox, result[0])
            advertised = letter.meta["media_asset_id"]
            with self.assertRaises(media_store.MediaError):
                media_store.load_content(self.state, result[0], advertised)

    def test_load_content_refuses_empty_bytes(self):
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(b"")
        (dest / ".ready").write_bytes(b"")
        with self.assertRaises(media_store.MediaError):
            media_store.load_content(self.state, "letter-x", "ab" * 16)

    def test_mark_ready_fsyncs_dir_when_marker_already_exists(self):
        from unittest import mock
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        (dest / ".ready").write_bytes(b"")
        seen = []
        real = media_store.durable.fsync_dir

        def track(path):
            seen.append(pathlib.Path(path))
            return real(path)

        with mock.patch.object(media_store.durable, "fsync_dir", side_effect=track):
            media_store._mark_ready(dest)
        self.assertIn(dest.resolve(), [p.resolve() for p in seen])

    def test_grandfather_marks_pre_marker_assets(self):
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        with self.assertRaises(media_store.MediaError):
            media_store.load_content(self.state, "letter-x", "ab" * 16)
        self.assertEqual(media_store.count_pre_marker(self.state), 1)
        marked, skipped, failed = media_store.grandfather_ready(self.state)
        self.assertEqual((marked, failed), (1, 0))
        self.assertEqual(
            media_store.load_content(self.state, "letter-x", "ab" * 16)[:8],
            PNG_1x1[:8])
        self.assertEqual(media_store.count_pre_marker(self.state), 0)

    def test_count_pre_marker_does_not_write(self):
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        self.assertEqual(media_store.count_pre_marker(self.state), 1)
        self.assertFalse((dest / ".ready").exists())

    def test_grandfather_refuses_symlinked_letter_dir(self):
        media = self.state / "media"
        media.mkdir(parents=True, exist_ok=True)
        real = self.state / "outside-letter" / ("ab" * 16)
        real.mkdir(parents=True)
        (real / "content").write_bytes(PNG_1x1)
        link = media / "letter-x"
        link.symlink_to(self.state / "outside-letter")
        marked, skipped, failed = media_store.grandfather_ready(self.state)
        self.assertEqual(marked, 0)
        self.assertGreaterEqual(failed, 1)
        self.assertFalse((real / ".ready").exists())

    def test_grandfather_existing_marker_still_runs_barrier(self):
        from unittest import mock
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        (dest / ".ready").write_bytes(b"")
        seen = []
        real = media_store.durable.fsync_dir

        def track(path):
            seen.append(pathlib.Path(path).resolve())
            return real(path)

        with mock.patch.object(media_store.durable, "fsync_dir", side_effect=track):
            marked, skipped, failed = media_store.grandfather_ready(self.state)
        self.assertEqual((marked, failed), (1, 0))
        self.assertIn(dest.resolve(), seen)

    def test_grandfather_isolates_one_asset_failure(self):
        from unittest import mock
        a = self.state / "media" / "letter-a" / ("aa" * 16)
        b = self.state / "media" / "letter-b" / ("bb" * 16)
        for dest in (a, b):
            dest.mkdir(parents=True)
            (dest / "content").write_bytes(PNG_1x1)
        real = media_store._mark_ready

        def boom(path):
            if pathlib.Path(path).name == "aa" * 16:
                raise OSError(errno.EACCES, "injected")
            return real(path)

        with mock.patch.object(media_store, "_mark_ready", side_effect=boom):
            marked, skipped, failed = media_store.grandfather_ready(self.state)
        self.assertEqual(failed, 1)
        self.assertEqual(marked, 1)
        self.assertTrue((b / ".ready").exists())
        self.assertFalse((a / ".ready").exists())

    def _eacces_on_media_iterdir(self):
        from unittest import mock
        media = (self.state / "media").resolve()
        real = pathlib.Path.iterdir

        def boom(path):
            if pathlib.Path(path).resolve() == media:
                raise OSError(errno.EACCES, "injected")
            return real(path)

        return mock.patch.object(pathlib.Path, "iterdir", boom)

    def test_grandfather_unreadable_root_is_failed(self):
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        from alb import cli
        with self._eacces_on_media_iterdir():
            marked, skipped, failed = media_store.grandfather_ready(self.state)
            rc = cli.main(["--grandfather-media", "--root", str(self.root)])
        self.assertEqual((marked, skipped, failed), (0, 0, 1))
        self.assertEqual(rc, 1)
        self.assertFalse((dest / ".ready").exists())

    def test_grandfather_absent_root_is_noop(self):
        self.assertFalse((self.state / "media").exists())
        from alb import cli
        marked, skipped, failed = media_store.grandfather_ready(self.state)
        rc = cli.main(["--grandfather-media", "--root", str(self.root)])
        self.assertEqual((marked, skipped, failed), (0, 0, 0))
        self.assertEqual(rc, 0)

    def test_count_pre_marker_unreadable_root_is_not_zero(self):
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        with self._eacces_on_media_iterdir():
            n = media_store.count_pre_marker(self.state)
        self.assertIsNone(n)

    def _hold_lock_in_child(self):
        import subprocess
        import time
        script = (
            "import sys, time;"
            "sys.path.insert(0, %r);" % str(ROOT / "src") +
            "from alb.bridge import singleton;"
            "ctx = singleton.hold(%r);" % str(self.root) +
            "ctx.__enter__();"
            "time.sleep(300)")
        child = subprocess.Popen([sys.executable, "-c", script])
        for _ in range(100):
            lock = self.root / "bridge.lock"
            if lock.is_file() and lock.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        else:
            child.kill()
            child.wait(timeout=5)
            self.fail("child never recorded the lock")
        return child

    def test_grandfather_refuses_while_bridge_holds_lock(self):
        import io
        from contextlib import redirect_stderr
        from alb import cli
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        child = self._hold_lock_in_child()
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                rc = cli.main(["--grandfather-media", "--root", str(self.root)])
        finally:
            child.kill()
            child.wait(timeout=5)
        self.assertNotEqual(rc, 0)
        self.assertFalse((dest / ".ready").exists())
        self.assertIn(str(child.pid), err.getvalue())

    def test_grandfather_migrates_then_releases_lock(self):
        from alb import cli
        from alb.bridge import singleton
        dest = self.state / "media" / "letter-x" / ("ab" * 16)
        dest.mkdir(parents=True)
        (dest / "content").write_bytes(PNG_1x1)
        rc = cli.main(["--grandfather-media", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertTrue((dest / ".ready").exists())
        self.assertIsNone(singleton.running_pid(self.root))
        with singleton.hold(self.root):
            pass


class ReplyPhoto(unittest.TestCase):
    """--reply-to --photo: same allowlist + preflight as --send --photo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.outbox = self.root / "outbox"
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        (self.state / "attach-roots.json").write_text(
            json.dumps({"roots": [str(self.allowed)]}), encoding="utf-8")
        self.png = self.allowed / "pic.png"
        self.png.write_bytes(PNG_1x1)
        self.letter_id = letters.publish(
            self.inbox, "see this", {"telegram_chat_id": "111"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowlisted_photo_goes_out_as_send_photo(self):
        class Sender:
            def __init__(self):
                self.calls = []

            def send(self, chat_id, text):
                raise AssertionError("sendMessage used for a photo reply")

            def send_photo(self, chat_id, data, caption=""):
                self.calls.append((chat_id, data[:8], caption))
                return "77"

        sender = Sender()
        rid = reply.send_reply(
            sender, self.inbox, self.state, self.allow, self.letter_id,
            "caption", outbox=self.outbox, photo_path=str(self.png))
        self.assertEqual(sender.calls, [("111", PNG_1x1[:8], "caption")])
        self.assertTrue((self.outbox / f"{rid}.md").is_file())
        blob = (self.outbox / f"{rid}.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.png), blob)
        self.assertEqual(media_store.load_outbound(self.state, rid)[:8], PNG_1x1[:8])

    def test_non_allowlisted_file_is_refused_before_claim(self):
        outside = self.root / "secret.png"
        outside.write_bytes(PNG_1x1)

        class Sender:
            def send(self, chat_id, text):
                raise AssertionError("must not send")

            def send_photo(self, chat_id, data, caption=""):
                raise AssertionError("must not send photo")

        with self.assertRaises(inspect.MediaError):
            reply.send_reply(
                Sender(), self.inbox, self.state, self.allow, self.letter_id,
                "x", outbox=self.outbox, photo_path=str(outside))
        self.assertEqual(list(self.outbox.glob("*.md")), [])

    def test_second_reply_still_already_claimed(self):
        class Sender:
            def send_photo(self, chat_id, data, caption=""):
                return "1"

            def send(self, chat_id, text):
                return "2"

        reply.send_reply(
            Sender(), self.inbox, self.state, self.allow, self.letter_id,
            "one", outbox=self.outbox, photo_path=str(self.png))
        with self.assertRaises(reply.AlreadyClaimed):
            reply.send_reply(
                Sender(), self.inbox, self.state, self.allow, self.letter_id,
                "two", outbox=self.outbox)


if __name__ == "__main__":
    unittest.main()
