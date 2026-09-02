"""What one cycle tells the OPERATOR, and what it still tells nobody else.

Grok, on being asked whether the install could be easier:

    "The failure that costs a morning is Test 2: deny is silent to the sender
    AND currently easy to confuse with a dead poller. Make deny visible to the
    OPERATOR only. The stranger still hears silence. The person standing at the
    terminal can see the gate working."

The security property is that a denied sender learns nothing. Nothing here
changes that. What changes is that an operator can distinguish a working deny
from a dead bridge - because when they cannot, the allowlist is the only lever
that looks relevant, and widening it is the fix they reach for.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_platform import FakePlatform  # noqa: E402
from alb import cli  # noqa: E402
from alb.adapters.telegram import api  # noqa: E402
from alb.bridge import run  # noqa: E402


def update(uid, chat, text):
    return {"update_id": uid, "chat_id": chat, "text": text}


class OneCycleReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "alb")
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.env = base / "bridge.env"
        self.env.write_text("ALB_TOKEN=1:x\n", encoding="utf-8")
        os.chmod(self.env, 0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def _once(self, updates):
        """Through cli.main, because the report is a thing the BINARY prints.
        Asserting on the library would prove a number exists that no operator
        ever sees."""
        out = io.StringIO()
        with mock.patch.object(api, "Telegram", lambda *a, **k: FakePlatform(updates)), \
                contextlib.redirect_stdout(out):
            code = cli.main(["--config", str(self.env), "--root", str(self.root),
                             "--once"])
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_a_denied_message_is_reported_to_the_operator(self):
        report = self._once([update(1, "999", "stranger")])
        self.assertIn("denied 1", report)
        self.assertIn("allowlist", report)

    def test_the_report_never_names_the_denied_sender(self):
        """A count is a diagnostic. An id is a log of everyone who messaged the
        bot - which the operator did not ask for and the sender did not agree
        to. Hermes raised this directly when the idea was proposed."""
        report = self._once([update(1, "999", "stranger"), update(2, "888", "another")])
        self.assertNotIn("999", report)
        self.assertNotIn("888", report)
        self.assertNotIn("stranger", report)

    def test_a_published_letter_is_reported(self):
        report = self._once([update(1, "111", "hi")])
        self.assertIn("published 1", report)

    def test_a_mixed_batch_reports_both(self):
        report = self._once([update(1, "111", "mine"), update(2, "999", "not mine")])
        self.assertIn("published 1", report)
        self.assertIn("denied 1", report)

    def test_an_idle_cycle_still_says_something(self):
        """Silence for an idle cycle is the failure this whole change is about.
        A run that prints nothing is indistinguishable from a run that did not
        happen, which is where the operator starts blaming the allowlist."""
        report = self._once([])
        self.assertTrue(report.strip())
        self.assertIn("fetched 0", report)

    def test_the_sender_side_is_untouched_by_the_report(self):
        """The whole point: reporting to the operator must not become replying
        to the stranger. No send may occur on a denied cycle."""
        sent = []

        class Recording(FakePlatform):
            def send(self, chat, text):
                sent.append(chat)

        out = io.StringIO()
        with mock.patch.object(api, "Telegram",
                               lambda *a, **k: Recording([update(1, "999", "x")])), \
                contextlib.redirect_stdout(out):
            cli.main(["--config", str(self.env), "--root", str(self.root), "--once"])
        self.assertEqual(sent, [])


class VersionIsAnswerable(unittest.TestCase):
    """Found by an outside reviewer (Codex, reviewing its own install):
    alb --version errored demanding --root. A version query needs no state
    directory - requiring one makes the simplest possible question about the
    binary fail, which reads as a broken install to anyone checking one."""

    def test_version_needs_no_root(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("agent-letter-bridge", out.getvalue())
