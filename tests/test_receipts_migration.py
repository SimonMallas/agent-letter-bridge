import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alb.bridge import run
from test_auto_filing import AutoFilingTests


class ReceiptMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.receipts = self.state / "receipts"
        self.receipts.mkdir(mode=0o700)

    def test_only_legacy_directory_changes_and_notice_once(self):
        self.receipts.chmod(0o755)
        child = self.receipts / "old-reply"
        child.mkdir(mode=0o755)
        letter = child / "1-sent.json"
        letter.write_text("unchanged")
        letter.chmod(0o644)
        mailbox = self.root / "inbox"
        mailbox.mkdir(mode=0o755)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(run.migrate_receipts_permissions(self.root))
            self.assertFalse(run.migrate_receipts_permissions(self.root))
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertEqual(self.receipts.stat().st_mode & 0o777, 0o700)
        self.assertEqual(child.stat().st_mode & 0o777, 0o755)
        self.assertEqual(letter.stat().st_mode & 0o777, 0o644)
        self.assertEqual(letter.read_text(), "unchanged")
        self.assertEqual(mailbox.stat().st_mode & 0o777, 0o755)

    def test_private_is_untouched(self):
        before = self.receipts.stat()
        with patch("alb.bridge.run.os.fchmod") as chmod:
            self.assertFalse(run.migrate_receipts_permissions(self.root))
            chmod.assert_not_called()
        self.assertEqual(self.receipts.stat().st_ctime_ns, before.st_ctime_ns)

    def test_other_modes_refused_without_change(self):
        for mode in (0o750, 0o775, 0o777, 0o705, 0o1755):
            self.receipts.chmod(mode)
            with self.assertRaises(run.ConfigError):
                run.migrate_receipts_permissions(self.root)
            self.assertEqual(self.receipts.stat().st_mode & 0o7777, mode)

    def test_other_owner_refused(self):
        self.receipts.chmod(0o755)
        with patch("alb.bridge.run.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(run.ConfigError):
                run.migrate_receipts_permissions(self.root)
        self.assertEqual(self.receipts.stat().st_mode & 0o777, 0o755)

    def test_symlink_refused(self):
        target = self.state / "target"
        self.receipts.rename(target)
        target.chmod(0o755)
        self.receipts.symlink_to(target)
        with self.assertRaises(run.ConfigError):
            run.migrate_receipts_permissions(self.root)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_not_directory_refused(self):
        self.receipts.rmdir()
        self.receipts.write_text("keep")
        with self.assertRaises(run.ConfigError):
            run.migrate_receipts_permissions(self.root)
        self.assertEqual(self.receipts.read_text(), "keep")

    def test_absent_is_not_created(self):
        self.receipts.rmdir()
        self.assertFalse(run.migrate_receipts_permissions(self.root))
        self.assertFalse(self.receipts.exists())


class StartupMigrationTests(AutoFilingTests):
    def test_startup_legacy_receipts_then_autofile(self):
        receipts = self.root / "state/receipts"
        receipts.mkdir(mode=0o755)
        receipts.chmod(0o755)
        rc, out, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertIn("migrated own state/receipts", out)
        self.assertIn("alb: filed", out)
        self.assertFalse(self.source.exists())
        self.assertEqual(receipts.stat().st_mode & 0o777, 0o700)

    def test_startup_refuses_other_mode_before_send(self):
        receipts = self.root / "state/receipts"
        receipts.mkdir(mode=0o775)
        receipts.chmod(0o775)
        rc, out, err = self.call()
        self.assertEqual(rc, 2)
        self.assertNotIn("alb: sent", out)
        self.assertTrue(self.source.exists())
        self.assertEqual(receipts.stat().st_mode & 0o777, 0o775)


if __name__ == "__main__":
    unittest.main()
