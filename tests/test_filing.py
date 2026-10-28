import contextlib
import datetime as dt
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alb import cli, filing


class FilingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = self.home / "bridge"
        self.root.mkdir(mode=0o700)
        (self.root / "inbox").mkdir(mode=0o700)
        self.config = self.root / "bridge.env"
        self.config.write_text("ALB_TOKEN=fake\nALB_TO=builder\n")
        self.config.chmod(0o600)

    def letter(self, name="test-1", to="builder", sent="2026-01-01T00:00:00Z"):
        p = self.root / "inbox" / (name + ".md")
        p.write_text(f"---\nid: {name}\nto: {to}\nsent: {sent}\n---\nbody\n")
        p.chmod(0o600)
        return p

    def test_file_exact_bytes(self):
        p = self.letter()
        data = p.read_bytes()
        filing.file_letter(self.root, "test-1")
        self.assertFalse(p.exists())
        self.assertEqual((self.root / "processed/test-1.md").read_bytes(), data)

    def test_extra_invalid_utf8_file_refuses_cleanly(self):
        source = self.letter()
        data = b"---\nid: test-1\nto: builder\n---\n\xff\n"
        source.write_bytes(data)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            result = cli.main(["file", "test-1", "--root", str(self.root)])
        self.assertEqual(result, 2)
        self.assertIn("alb: refused:", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())
        self.assertEqual(source.read_bytes(), data)
        self.assertFalse((self.root / "processed/test-1.md").exists())

    def test_cli(self):
        self.letter()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["file", "test-1", "--root", str(self.root)]), 0)

    def test_integrated_refuses(self):
        p = self.letter()
        with self.assertRaisesRegex(filing.FilingError, "letterbox file"):
            filing.file_letter(self.root, "test-1", mail_root=self.home / "mail")
        self.assertTrue(p.exists())

    def test_wrong_seat(self):
        self.letter(to="other")
        with self.assertRaises(filing.FilingError):
            filing.file_letter(self.root, "test-1")

    def test_no_overwrite(self):
        p = self.letter()
        (self.root / "processed").mkdir(mode=0o700)
        target = self.root / "processed/test-1.md"
        target.write_text("keep")
        with self.assertRaises(FileExistsError):
            filing.file_letter(self.root, "test-1")
        self.assertTrue(p.exists())
        self.assertEqual(target.read_text(), "keep")

    def test_symlink_refused(self):
        p = self.letter()
        p.rename(self.root / "elsewhere")
        p.symlink_to(self.root / "elsewhere")
        with self.assertRaises(filing.FilingError):
            filing.file_letter(self.root, "test-1")

    def test_public_config_refused(self):
        self.config.chmod(0o644)
        with self.assertRaises(filing.FilingError):
            filing.file_letter(self.root, "test-1")

    def test_other_home_refused(self):
        with patch.dict(os.environ, {"HOME": str(self.home / "different")}):
            with self.assertRaises(filing.FilingError):
                filing.file_letter(self.root, "test-1")

    def test_count_and_age_only_own_seat(self):
        self.letter()
        self.letter("other-1", to="other")
        now = dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(filing.unfiled(self.root, now=now), "unfiled: 1 (oldest: PT60S)")
        filing.file_letter(self.root, "test-1")
        self.assertEqual(filing.unfiled(self.root, now=now), "unfiled: 0 (oldest: none)")

    def test_unknown_time_not_zero(self):
        self.letter(sent="")
        self.assertEqual(filing.unfiled(self.root), "unfiled: 1 (oldest: unknown)")

    def test_integrated_status_counts_configured_mailbox(self):
        mail = self.home / "mail"
        mail.mkdir()
        (mail / "inbox").mkdir()
        self.letter().rename(mail / "inbox/test-1.md")
        with self.config.open("a") as f:
            f.write(f"ALB_MAIL_ROOT={mail}\n")
        self.assertIn("unfiled: 1", filing.unfiled(self.root))
        with self.assertRaisesRegex(filing.FilingError, "letterbox file"):
            filing.file_letter(self.root, "test-1")

    def test_status_cli(self):
        self.letter()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(["--status", "--root", str(self.root)])
        self.assertIn("unfiled: 1 (oldest: PT", output.getvalue())
        self.assertNotIn("ALB_TOKEN", output.getvalue())

    def test_processed_symlink_refused(self):
        p = self.letter()
        dest = self.home / "outside"
        dest.mkdir()
        (self.root / "processed").symlink_to(dest)
        with self.assertRaises(filing.FilingError):
            filing.file_letter(self.root, "test-1")
        self.assertTrue(p.exists())
        self.assertEqual(list(dest.iterdir()), [])

    def test_malformed_status_is_unknown_not_zero(self):
        self.letter().write_text("not an envelope")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(["--status", "--root", str(self.root)])
        self.assertIn("unfiled: unknown", output.getvalue())

    def test_path_id_refused(self):
        from alb.letter import store
        with self.assertRaises(store.UnsafeIdentifier):
            filing.file_letter(self.root, "../outside")


if __name__ == "__main__":
    unittest.main()
