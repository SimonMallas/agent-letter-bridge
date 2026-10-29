"""Receipt-driven filing through the real CLI/send path; fake network only."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alb import cli, filing
from alb.letter import store
from alb.outbound import store as outbound
from alb.send import reply


class AutoFilingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        env = patch.dict(os.environ, HOME=str(self.home))
        env.start()
        self.addCleanup(env.stop)
        self.root = self.home / "bridge"
        self.root.mkdir(mode=0o700)
        for name in ("inbox", "processed", "outbox", "state"):
            (self.root / name).mkdir(mode=0o700)
        self.config = self.root / "bridge.env"
        self.config.write_text("ALB_TOKEN=fake\nALB_TO=builder\n")
        self.config.chmod(0o600)
        (self.root / "allowlist.json").write_text(json.dumps({"chats": ["123"]}))
        self.ident = store.publish(self.root / "inbox", "incoming", {
            "id": "", "from": "telegram-bridge", "to": "builder", "chat_id": "123"})
        self.source = self.root / "inbox" / (self.ident + ".md")
        self.dest = self.root / "processed" / self.source.name
        self.before = self.source.read_bytes()

    def call(self, failure=None):
        class Sender:
            def send(self, *args, **kwargs):
                if failure:
                    raise failure
                return "42"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("alb.cli.run.make_sender", return_value=Sender()), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(["--root", str(self.root), "--config", str(self.config),
                           "--reply-to", self.ident, "--text", "handled"])
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_sent_files_source(self):
        rc, out, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertIn("alb: filed", out)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.dest.read_bytes(), self.before)

    def test_ambiguous_stays_visible(self):
        rc, out, err = self.call(reply.AmbiguousOutcome("timeout"))
        self.assertEqual(rc, 3)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertFalse(self.dest.exists())

    def test_throttled_then_resumed_files_only_on_sent(self):
        self.assertEqual(self.call(reply.Throttled("wait"))[0], 4)
        self.assertTrue(self.source.exists())
        rc, out, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.source.exists())
        self.assertTrue(self.dest.exists())

    def test_definite_refusal_does_not_file(self):
        self.assertEqual(self.call(reply.DefiniteRefusal("no"))[0], 1)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.dest.exists())

    def test_filing_failure_is_not_send_failure(self):
        self.dest.write_text("do not overwrite")
        rc, out, err = self.call()
        self.assertEqual(rc, 0)
        self.assertIn("source not auto-filed", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual(self.dest.read_text(), "do not overwrite")
        # Still no retry: composed reply remains claimed.
        self.assertEqual(self.call()[0], 1)

    def test_integrated_closeout_but_manual_file_refuses(self):
        mail = self.home / "seat"
        mail.mkdir(mode=0o755)
        for name in ("inbox", "processed", "outbox"):
            (mail / name).mkdir(mode=0o755)
        self.source.rename(mail / "inbox" / self.source.name)
        self.source = mail / "inbox" / self.source.name
        self.dest = mail / "processed" / self.source.name
        with self.config.open("a") as f:
            f.write(f"ALB_MAIL_ROOT={mail}\n")
        with self.assertRaisesRegex(filing.FilingError, "letterbox file"):
            filing.file_letter(self.root, self.ident)
        rc, out, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertIn("alb: filed", out)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.dest.read_bytes(), self.before)
        self.assertEqual(filing.unfiled(self.root), "unfiled: 0 (oldest: none)")
        self.dest.rename(self.source)
        self.source.chmod(0o644)
        for directory in (mail, mail / "inbox", mail / "processed", mail / "outbox"):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            directory.chmod(0o775)
            with self.assertRaises(filing.FilingError):
                filing.file_sent_reply(self.root, self.ident, "reply-" + self.ident)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o775)
            directory.chmod(0o755)
        filing.file_sent_reply(self.root, self.ident, "reply-" + self.ident)
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o644)

    def test_outbound_identity_and_recipient_are_bound(self):
        self.call()
        self.dest.rename(self.source)
        out_id = "reply-" + self.ident
        path = self.root / "outbox" / (out_id + ".md")
        original = path.read_text()
        for old, new in ((f"id: {out_id}", "id: wrong"),
                         ("to: telegram-bridge", "to: other-bridge")):
            self.assertIn(old, original)
            path.write_text(original.replace(old, new))
            with self.assertRaises(filing.FilingError):
                filing.file_sent_reply(self.root, self.ident, out_id)
            self.assertTrue(self.source.exists())
        path.write_text(original)

    def test_shared_writable_and_receipt_state_refused(self):
        self.call()
        self.dest.rename(self.source)
        out_id = "reply-" + self.ident
        for path in (self.root / "state/receipts", self.root / "state/receipts" / out_id):
            path.chmod(0o755)
            with self.assertRaises(filing.FilingError):
                filing.file_sent_reply(self.root, self.ident, out_id)
            path.chmod(0o700)
        self.source.chmod(0o666)
        with self.assertRaises(filing.FilingError):
            filing.file_sent_reply(self.root, self.ident, out_id)
        self.assertTrue(self.source.exists())

    def test_invalid_receipt_contents_cannot_prove_sent(self):
        self.call()
        self.dest.rename(self.source)
        out_id = "reply-" + self.ident
        sent = list((self.root / "state/receipts" / out_id).glob("*-sent.json"))[0]
        sent.write_text('{"event": "ambiguous"}')
        with self.assertRaises(filing.FilingError):
            filing.file_sent_reply(self.root, self.ident, out_id)
        self.assertTrue(self.source.exists())

    def test_latest_ambiguous_receipt_refuses(self):
        self.call()
        self.dest.rename(self.source)
        out_id = "reply-" + self.ident
        outbound.record_event(self.root / "state", out_id, "ambiguous")
        with self.assertRaises(filing.FilingError):
            filing.file_sent_reply(self.root, self.ident, out_id)
        self.assertTrue(self.source.exists())

    def test_already_processed_is_safe(self):
        self.source.rename(self.dest)
        rc, out, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.dest.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
