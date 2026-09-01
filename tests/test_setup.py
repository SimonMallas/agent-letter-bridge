"""`alb init`: the boilerplate steps, and none of the human ones.

All four reviewers independently proposed one command that creates the state
directory, writes a mode-600 config, and writes a DENY-ALL allowlist. All four
independently refused the same shortcuts. This suite pins the refusals, because
the value of this command is entirely in what it will not do:

  - it never invents an allowlist entry
  - it never puts a secret in argv, and so never in shell history
  - it never touches the network unless the operator says so, in that moment
  - it never overwrites something that already exists
  - it never picks a pane, or a mailbox, on the operator's behalf
"""
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.setup import wizard  # noqa: E402


class ScriptedConsole:
    """A human with a fixed set of answers, and a transcript of what they saw."""

    def __init__(self, answers=None, secrets=None):
        self.answers = list(answers or [])
        self.secrets = list(secrets or [])
        self.said = []
        self.asked = []

    def say(self, text=""):
        self.said.append(str(text))

    def ask(self, question, default=""):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else default

    def ask_secret(self, question):
        self.asked.append(question)
        return self.secrets.pop(0) if self.secrets else ""

    @property
    def transcript(self):
        return "\n".join(self.said)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "alb"

    def tearDown(self):
        self.tmp.cleanup()

    def run_init(self, answers=None, secrets=("123456:TOKEN",), **kw):
        console = ScriptedConsole(answers, list(secrets))
        result = wizard.init(self.root, console, **kw)
        return console, result


class ItCreatesTheBoilerplate(Base):
    def test_the_state_directory_is_created_private(self):
        """0700 explicitly, never inherited from umask. The dogfood install
        proved that class: a directory made with the default umask is 0755 and
        nobody notices until they look."""
        self.run_init(answers=["", "print", ""])
        self.assertEqual(mode(self.root), 0o700)

    def test_the_config_is_written_mode_600(self):
        self.run_init(answers=["", "print", ""])
        env = self.root / "bridge.env"
        self.assertEqual(mode(env), 0o600)
        self.assertIn("ALB_TOKEN=123456:TOKEN", env.read_text(encoding="utf-8"))

    def test_the_written_config_passes_the_loaders_own_checks(self):
        """Setup must SATISFY the rules, not bypass them. A config this command
        wrote that the binary then refuses is worse than no command."""
        from alb.bridge import run
        self.run_init(answers=["", "print", ""])
        loaded = run.load_config(self.root / "bridge.env")
        self.assertEqual(loaded["ALB_TOKEN"], "123456:TOKEN")

    def test_the_allowlist_is_created_denying_everyone(self):
        """Fail-closed on arrival. The file existing is a convenience; the file
        being empty is the security property."""
        self.run_init(answers=["", "print", ""])
        allow = self.root / "allowlist.json"
        self.assertEqual(mode(allow), 0o600)
        self.assertEqual(json.loads(allow.read_text())["chats"], [])

    def test_a_deny_all_allowlist_really_denies(self):
        """Asserting the file's contents is not asserting the gate. Ask the
        gate."""
        from alb.allowlist import gate
        self.run_init(answers=["", "print", ""])
        self.assertFalse(gate.allows(self.root / "allowlist.json", "111"))


class ItRefusesToGuess(Base):
    def test_it_never_writes_a_chat_id_the_operator_did_not_give(self):
        self.run_init(answers=["", "print", ""])
        self.assertEqual(json.loads((self.root / "allowlist.json").read_text())["chats"], [])

    def test_it_does_not_touch_the_network_when_the_operator_declines(self):
        """Pi's constraint: a public product should be able to say setup never
        reaches the platform unless you ask it to. Declining must make that
        literally true, not nearly true."""
        calls = []
        self.run_init(answers=["", "print", ""],
                      chat_id_reader=lambda token: calls.append(token) or [])
        self.assertEqual(calls, [])

    def test_it_reads_the_chat_id_only_when_the_operator_asks(self):
        """Simon's decision: keep the choice. The trap-killer stays available;
        the no-network claim stays true for anyone who declines."""
        calls = []

        def reader(token):
            calls.append(token)
            return [{"chat_id": "111", "label": "you"}]

        console, _ = self.run_init(answers=["", "read", "", "1"], chat_id_reader=reader)
        self.assertEqual(calls, ["123456:TOKEN"])
        self.assertEqual(json.loads((self.root / "allowlist.json").read_text())["chats"], ["111"])

    def test_the_network_call_is_described_before_it_happens(self):
        """Narrated, not hidden. The operator agreeing is only consent if they
        were told what they were agreeing to."""
        def reader(token):
            return [{"chat_id": "111", "label": "you"}]

        console, _ = self.run_init(answers=["", "read", "", "1"], chat_id_reader=reader)
        described = console.transcript.lower()
        self.assertIn("getupdates", described)
        self.assertIn("nothing", described)

    def test_it_never_picks_a_pane(self):
        """A listing cannot tell you which pane holds the agent the operator
        means, and a knock typed into the wrong pane lands in someone else's
        session."""
        console, result = self.run_init(
            answers=["", "print", ""],
            panes=[{"id": "%1", "label": "zsh"}, {"id": "%2", "label": "agent"}])
        self.assertNotIn("ALB_SURFACE", (self.root / "bridge.env").read_text())

    def test_it_never_assumes_a_mailbox(self):
        """Grok: filesystem presence is not consent. There is also no detector
        to be wrong - the operator gives a path or does not."""
        console, result = self.run_init(answers=["", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        self.assertNotIn("ALB_MAIL_ROOT", (self.root / "bridge.env").read_text())

    def test_a_mailbox_path_is_used_when_the_operator_gives_one(self):
        mailbox = pathlib.Path(self.tmp.name) / "seat"
        (mailbox / "inbox").mkdir(parents=True)
        console, result = self.run_init(answers=[str(mailbox), "grok", "print", ""])
        self.assertEqual(result["mode"], "integrated")
        env = (self.root / "bridge.env").read_text()
        self.assertIn(f"ALB_MAIL_ROOT={mailbox}", env)
        self.assertIn("ALB_TO=grok", env)


class ItNeverDestroys(Base):
    def test_an_existing_config_is_not_overwritten(self):
        self.root.mkdir(parents=True)
        env = self.root / "bridge.env"
        env.write_text("ALB_TOKEN=already:here\n", encoding="utf-8")
        os.chmod(env, 0o600)
        self.run_init(answers=["", "print", ""])
        self.assertIn("already:here", env.read_text(encoding="utf-8"))

    def test_an_existing_allowlist_is_not_emptied(self):
        """Re-running setup on a working bridge must not silently disarm it -
        an allowlist reset to deny-all reads exactly like a dead bot."""
        self.root.mkdir(parents=True)
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        os.chmod(allow, 0o600)
        self.run_init(answers=["", "print", ""])
        self.assertEqual(json.loads(allow.read_text())["chats"], ["111"])

    def test_it_says_what_it_skipped(self):
        """Leaving a file alone silently is how someone edits the wrong copy."""
        self.root.mkdir(parents=True)
        (self.root / "bridge.env").write_text("ALB_TOKEN=already:here\n", encoding="utf-8")
        os.chmod(self.root / "bridge.env", 0o600)
        console, _ = self.run_init(answers=["", "print", ""])
        self.assertIn("bridge.env", console.transcript)
        self.assertIn("kept", console.transcript.lower())


class TheSecretNeverLands(Base):
    def test_the_token_is_asked_for_without_echo(self):
        """ask_secret, not ask. A token echoed into a terminal is a token in a
        scrollback buffer."""
        console = ScriptedConsole(["", "print", ""], ["123456:TOKEN"])
        wizard.init(self.root, console)
        self.assertEqual(console.secrets, [])

    def test_the_token_is_never_printed_back(self):
        console, _ = self.run_init(answers=["", "print", ""])
        self.assertNotIn("123456:TOKEN", console.transcript)

    def test_there_is_no_flag_that_would_put_a_token_in_shell_history(self):
        """Pi's constraint, pinned as a signature check: a --token flag is the
        failure, because argv is the one place a secret is recorded by
        something the operator does not control."""
        import inspect
        params = set(inspect.signature(wizard.init).parameters)
        self.assertNotIn("token", params)
