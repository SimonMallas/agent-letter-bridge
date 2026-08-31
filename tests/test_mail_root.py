"""Integrated mode: letters go to a shared mailbox, private state never does.

Grok approved the split and blocked a list of implementations of it. Their
condition: mail-root is a LETTERS-ONLY pointer. Anything else relocates the
contamination it exists to prevent rather than removing it.

These are the pins they said they would look for.
"""
import contextlib
import io
import json
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from alb.bridge import run  # noqa: E402
from fake_platform import FakePlatform, update  # noqa: E402

# Everything that must stay in the private root, checked by name rather than by
# category so a new writer cannot quietly join the wrong side.
PRIVATE = ("allowlist.json", "bridge.lock", "delivered.json", "state")


class NoTransport:
    def deliver(self, surface, line):
        raise AssertionError("standalone notifier used in integrated mode")


class MailRootIsLettersOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "private")
        # A stand-in for a shared mailbox that already exists and is not ours.
        self.mail = base / "shared" / "seat"
        (self.mail / "inbox").mkdir(parents=True)
        self.mail.chmod(0o755)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cycle(self, platform, transport=None):
        with mock.patch.object(run, "_bus_ring") as ring:
            run.run_once(platform, transport or NoTransport(), "", self.root,
                         mail_root=self.mail, recipient="grok-build")
        return ring

    def test_the_default_mail_root_is_the_state_root(self):
        """Standalone behaviour must be byte-identical, so the default cannot
        be a new location."""
        import inspect
        sig = inspect.signature(run.run_once)
        self.assertIsNone(sig.parameters["mail_root"].default,
                          "mail_root must default to None and resolve to root")

    def test_letters_land_in_the_mailbox(self):
        self._cycle(FakePlatform([update(1, "111", "hi")]))
        self.assertEqual(len(list((self.mail / "inbox").glob("*.md"))), 1)
        self.assertEqual(list((self.root / "inbox").glob("*.md")), [])

    def test_no_private_state_is_created_in_the_mailbox(self):
        """THE CONDITION. Grok: anything else relocates the bug."""
        self._cycle(FakePlatform([update(1, "111", "hi")]))
        for name in PRIVATE:
            with self.subTest(name=name):
                self.assertFalse((self.mail / name).exists(),
                                 f"{name} leaked into the shared mailbox")

    def test_the_mailbox_parent_is_not_chmodded(self):
        """A shared seat directory holds identity and surface files that are
        not ours. Writing our umask onto it is the same contamination wearing
        a new flag."""
        before = stat.S_IMODE(self.mail.stat().st_mode)
        self._cycle(FakePlatform([update(1, "111", "hi")]))
        self.assertEqual(stat.S_IMODE(self.mail.stat().st_mode), before,
                         "changed the permissions of a directory we do not own")

    def test_the_dedup_ledger_stays_private(self):
        """Bookkeeping, not mail."""
        self._cycle(FakePlatform([update(1, "111", "hi")]))
        self.assertTrue((self.root / "state" / "delivered.json").exists())
        self.assertFalse((self.mail / "state").exists())

    def test_a_denied_sender_publishes_nothing_and_does_not_ring(self):
        ring = self._cycle(FakePlatform([update(1, "999", "let me in")]))
        self.assertEqual(list((self.mail / "inbox").glob("*.md")), [])
        ring.assert_not_called()

    def test_integrated_mode_rings_through_the_bus_not_a_copied_line(self):
        """Grok: do not reimplement the doorbell line. Their skill accepts an
        exact format including an 8-hex token derived by the bus helper, so a
        homemade line is either rejected or reads the wrong token."""
        ring = self._cycle(FakePlatform([update(1, "111", "hi")]))
        ring.assert_called_once()
        recipient, kind, letter_id = ring.call_args[0][:3]
        self.assertEqual(recipient, "grok-build")
        self.assertEqual(kind, "info")
        # And the id of the letter that was actually published. Asserting only
        # the recipient and type would pass while ringing about the wrong
        # letter, which reads as a delivered knock and resolves to nothing.
        published = [p.stem for p in (self.mail / "inbox").glob("*.md")]
        self.assertIn(letter_id, published)

    def test_the_standalone_notifier_is_not_used_in_integrated_mode(self):
        """Two injects is two Enters."""
        self._cycle(FakePlatform([update(1, "111", "hi")]))  # NoTransport asserts


class ReplyFindsLettersWhereTheyLive(unittest.TestCase):
    """Grok's block: until this is one code path, "outbound is alb --reply-to"
    is a sentence the binary does not implement.

    The poller writes to the mailbox; the send path searched only the private
    root. In integrated mode that refuses a reply to a letter that plainly
    exists - the dogfood send bug in a new place.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "private")
        self.mail = base / "shared" / "seat"
        (self.mail / "inbox").mkdir(parents=True)
        (self.mail / "processed").mkdir(parents=True)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _publish(self):
        with mock.patch.object(run, "_bus_ring"):
            run.run_once(FakePlatform([update(1, "111", "hi")]), None, "",
                         self.root, mail_root=self.mail, recipient="grok-build")
        return [p.stem for p in (self.mail / "inbox").glob("*.md")][0]

    def test_a_reply_finds_a_letter_in_the_mailbox(self):
        from alb.send import reply
        letter_id = self._publish()
        sent = []
        sender = type("S", (), {"send": lambda s, c, t: sent.append((c, t))})()
        reply.send_reply(sender, self.mail / "inbox", self.root / "state",
                         self.root / "allowlist.json", letter_id, "a reply",
                         searched=[self.mail / "inbox", self.mail / "processed"])
        self.assertEqual(sent[0][0], "111")

    def test_a_reply_finds_a_letter_after_it_has_been_filed(self):
        """bus file moves the letter to processed before anyone replies."""
        from alb.send import reply
        letter_id = self._publish()
        for f in (self.mail / "inbox").glob("*.md"):
            f.rename(self.mail / "processed" / f.name)
        sent = []
        sender = type("S", (), {"send": lambda s, c, t: sent.append((c, t))})()
        reply.send_reply(sender, self.mail / "inbox", self.root / "state",
                         self.root / "allowlist.json", letter_id, "a reply",
                         searched=[self.mail / "inbox", self.mail / "processed"])
        self.assertEqual(sent[0][0], "111")


class ReplyThroughTheBinary(unittest.TestCase):
    """Through main(), not through a searched list the test builds itself.

    Grok's residual: the other reply tests pass their own searched list, so
    reverting the line in the CLI that builds it would leave them green. That
    is the fixture-versus-production hide again - a test proving the library
    can do something, while the thing the operator actually runs cannot.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "private")
        self.mail = base / "shared" / "seat"
        (self.mail / "inbox").mkdir(parents=True)
        (self.mail / "processed").mkdir(parents=True)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.env = base / "bridge.env"
        self.env.write_text(f"ALB_TOKEN=1:x\nALB_MAIL_ROOT={self.mail}\n",
                            encoding="utf-8")
        self.env.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_binary_replies_to_a_letter_in_the_mailbox(self):
        from alb import cli
        from alb.adapters.telegram import api

        with mock.patch.object(run, "_bus_ring"):
            run.run_once(FakePlatform([update(1, "111", "hi")]), None, "",
                         self.root, mail_root=self.mail, recipient="grok-build")
        letter_id = [p.stem for p in (self.mail / "inbox").glob("*.md")][0]

        sent = []
        # The CLI prints on success; a suite that prints is a suite whose
        # output stops being read.
        with mock.patch.object(api.Telegram, "send",
                               lambda self, chat, text: sent.append((chat, text))), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--config", str(self.env), "--root", str(self.root),
                             "--reply-to", letter_id, "--text", "a reply"])
        self.assertEqual(code, 0)
        self.assertEqual(sent[0][0], "111")


class TheHealthFileMustNotLie(unittest.TestCase):
    """The helper exits 0 whether the knock was submitted, merely pasted, or
    had no live surface at all. Trusting the exit code means reporting a
    delivered ring for a pane that is gone - and the ring health file is the
    ONLY tell that a doorbell has stopped working."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "private")
        self.mail = base / "shared" / "seat"
        (self.mail / "inbox").mkdir(parents=True)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cycle_with_helper_saying(self, output):
        completed = mock.Mock(returncode=0, stdout=output, stderr="")
        with mock.patch.object(run.subprocess, "run", return_value=completed):
            run.run_once(FakePlatform([update(1, "111", "hi")]),
                         None, "", self.root,
                         mail_root=self.mail, recipient="grok-build")
        return json.loads((self.root / "state" / "ring-health.json").read_text())

    def test_a_submitted_knock_is_recorded_as_delivered(self):
        record = self._cycle_with_helper_saying(
            "bus: doorbell submitted to grok-build on SOME-UUID")
        self.assertEqual(record["state"], "ok")

    def test_no_live_surface_is_not_recorded_as_delivered(self):
        record = self._cycle_with_helper_saying("bus: no_live_surface for grok-build")
        self.assertEqual(record["state"], "failing")
        self.assertIn("no_live_surface", record["reason"])

    def test_pasted_but_not_submitted_is_not_delivered(self):
        record = self._cycle_with_helper_saying("bus: pasted_not_submitted")
        self.assertEqual(record["state"], "failing")


class StandaloneIsUnchanged(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = run.prepare_root(pathlib.Path(self.tmp.name) / "alb")
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_letters_land_in_the_state_root_and_the_alb_notifier_rings(self):
        rung = []
        transport = type("T", (), {"deliver": lambda s, surf, line: rung.append(line)})()
        with mock.patch.object(run, "_bus_ring") as bus:
            run.run_once(FakePlatform([update(1, "111", "hi")]),
                         transport, "SURFACE-1", self.root)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
        self.assertEqual(len(rung), 1)
        self.assertIn("bridge inbox", rung[0])
        bus.assert_not_called()
