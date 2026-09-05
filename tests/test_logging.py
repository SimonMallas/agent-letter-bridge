"""ALB must record its own failures, not rely on how it was started.

A relay died at 02:10 and the only evidence was pane scrollback nobody kept.
The alb.log beside it was 540 bytes from two days earlier - because that file
was shell redirection, not the product, and a restart without the redirect
silently ended the record. Diagnosis was reconstructed from a heartbeat
timestamp and somebody's memory.

So the log is the product's job, and it has to hold under the conditions that
make logs matter: a failing platform, and a failing disk.
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb import cli  # noqa: E402


class TheProductKeepsItsOwnRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def log_text(self):
        p = self.root / "alb.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_a_line_reaches_the_log_and_the_terminal(self):
        with mock.patch.object(cli.sys, "stderr") as err:
            cli.log(self.root, "transient, retrying: HTTP 429")
        self.assertIn("transient, retrying: HTTP 429", self.log_text())
        self.assertTrue(err.write.called or err.method_calls,
                        "the pane must still show it; the log is additional")

    def test_every_line_is_timestamped(self):
        cli.log(self.root, "fetch failed: HTTP 401")
        first = self.log_text().splitlines()[0]
        self.assertRegex(first, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ",
                         "a log without times cannot answer 'since when'")

    def test_it_appends_rather_than_replaces(self):
        cli.log(self.root, "first")
        cli.log(self.root, "second")
        self.assertEqual(len(self.log_text().splitlines()), 2)

    def test_a_missing_root_does_not_stop_the_bridge(self):
        """The control that matters. A log that can kill the poller is worse
        than no log: it turns a diagnostic aid into the same class of defect
        as dying on a 429 - ending the process over something incidental."""
        gone = self.root / "does" / "not" / "exist"
        cli.log(gone, "the platform is refusing us")  # must not raise

    def test_an_unwritable_log_does_not_stop_the_bridge(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            cli.log(self.root, "the platform is refusing us")  # must not raise

    def test_it_does_not_grow_without_bound(self):
        (self.root / "alb.log").write_text("x" * (cli.LOG_MAX_BYTES + 1),
                                           encoding="utf-8")
        cli.log(self.root, "after the cap")
        self.assertIn("after the cap", self.log_text())
        self.assertLess((self.root / "alb.log").stat().st_size,
                        cli.LOG_MAX_BYTES + 4096)
        self.assertTrue((self.root / "alb.log.1").exists(),
                        "the previous window is kept, not discarded")
