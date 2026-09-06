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
        # helper_found is pinned unless a test says otherwise. Left to the real
        # shutil.which, these tests would ask an extra question on a machine
        # that has a letterbox installed and not on one that does not - so the
        # suite would pass or fail depending on whose laptop it ran on.
        kw.setdefault("helper_found", lambda name: "/usr/local/bin/bus.sh")
        # HERMETIC BY DEFAULT, learned the loud way: the first run of this
        # suite after the resident offer landed created 359 real cmux
        # workspaces on the maintainer's machine, because these defaults fell
        # through to the real environment (cmux_born read the session's env,
        # the offer defaulted to yes, and the real cmux was invoked). A test
        # that can touch the operator's multiplexer is not a test. Every real
        # side effect is stubbed here; a test that WANTS the offer opts in.
        kw.setdefault("cmux_born", lambda: False)
        kw.setdefault("bridge_running", lambda root: False)
        kw.setdefault("start_pane", lambda title, command: (_ for _ in ()).throw(
            AssertionError("start_pane reached without an explicit test override")))
        console = ScriptedConsole(answers, list(secrets))
        result = wizard.init(self.root, console, **kw)
        return console, result


class ItCreatesTheBoilerplate(Base):
    def test_the_state_directory_is_created_private(self):
        """0700 explicitly, never inherited from umask. The dogfood install
        proved that class: a directory made with the default umask is 0755 and
        nobody notices until they look."""
        self.run_init(answers=["n", "print", ""])
        self.assertEqual(mode(self.root), 0o700)

    def test_the_config_is_written_mode_600(self):
        self.run_init(answers=["n", "print", ""])
        env = self.root / "bridge.env"
        self.assertEqual(mode(env), 0o600)
        self.assertIn("ALB_TOKEN=123456:TOKEN", env.read_text(encoding="utf-8"))

    def test_the_written_config_passes_the_loaders_own_checks(self):
        """Setup must SATISFY the rules, not bypass them. A config this command
        wrote that the binary then refuses is worse than no command."""
        from alb.bridge import run
        self.run_init(answers=["n", "print", ""])
        loaded = run.load_config(self.root / "bridge.env")
        self.assertEqual(loaded["ALB_TOKEN"], "123456:TOKEN")

    def test_the_allowlist_is_created_denying_everyone(self):
        """Fail-closed on arrival. The file existing is a convenience; the file
        being empty is the security property."""
        self.run_init(answers=["n", "print", ""])
        allow = self.root / "allowlist.json"
        self.assertEqual(mode(allow), 0o600)
        self.assertEqual(json.loads(allow.read_text())["chats"], [])

    def test_a_deny_all_allowlist_really_denies(self):
        """Asserting the file's contents is not asserting the gate. Ask the
        gate."""
        from alb.allowlist import gate
        self.run_init(answers=["n", "print", ""])
        self.assertFalse(gate.allows(self.root / "allowlist.json", "111"))


class ItRefusesToGuess(Base):
    def test_it_never_writes_a_chat_id_the_operator_did_not_give(self):
        self.run_init(answers=["n", "print", ""])
        self.assertEqual(json.loads((self.root / "allowlist.json").read_text())["chats"], [])

    def test_it_does_not_touch_the_network_when_the_operator_declines(self):
        """Pi's constraint: a public product should be able to say setup never
        reaches the platform unless you ask it to. Declining must make that
        literally true, not nearly true."""
        calls = []
        self.run_init(answers=["n", "print", ""],
                      chat_id_reader=lambda token: calls.append(token) or [])
        self.assertEqual(calls, [])

    def test_it_reads_the_chat_id_only_when_the_operator_asks(self):
        """Simon's decision: keep the choice. The trap-killer stays available;
        the no-network claim stays true for anyone who declines."""
        calls = []

        def reader(token):
            calls.append(token)
            return [{"chat_id": "111", "label": "you"}]

        console, _ = self.run_init(answers=["n", "read", "", "1"], chat_id_reader=reader)
        self.assertEqual(calls, ["123456:TOKEN"])
        self.assertEqual(json.loads((self.root / "allowlist.json").read_text())["chats"], ["111"])

    def test_the_network_call_is_described_before_it_happens(self):
        """Narrated, not hidden. The operator agreeing is only consent if they
        were told what they were agreeing to."""
        def reader(token):
            return [{"chat_id": "111", "label": "you"}]

        console, _ = self.run_init(answers=["n", "read", "", "1"], chat_id_reader=reader)
        described = console.transcript.lower()
        self.assertIn("getupdates", described)
        self.assertIn("nothing", described)

    def test_it_never_picks_a_pane(self):
        """A listing cannot tell you which pane holds the agent the operator
        means, and a knock typed into the wrong pane lands in someone else's
        session."""
        console, result = self.run_init(
            answers=["n", "print", ""],
            panes=[{"id": "%1", "label": "zsh"}, {"id": "%2", "label": "agent"}])
        self.assertNotIn("ALB_SURFACE", (self.root / "bridge.env").read_text())

    def test_it_never_assumes_a_mailbox(self):
        """Grok: filesystem presence is not consent. There is also no detector
        to be wrong - the operator gives a path or does not."""
        console, result = self.run_init(answers=["n", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        self.assertNotIn("ALB_MAIL_ROOT", (self.root / "bridge.env").read_text())

    def test_a_mailbox_path_is_used_when_the_operator_gives_one(self):
        mailbox = pathlib.Path(self.tmp.name) / "seat"
        (mailbox / "inbox").mkdir(parents=True)
        console, result = self.run_init(
            answers=["y", str(mailbox), "an-agent", "print", ""])
        self.assertEqual(result["mode"], "integrated")
        env = (self.root / "bridge.env").read_text()
        self.assertIn(f"ALB_MAIL_ROOT={mailbox}", env)
        self.assertIn("ALB_TO=an-agent", env)


class ItNeverDestroys(Base):
    def test_an_existing_config_is_not_overwritten(self):
        self.root.mkdir(parents=True)
        env = self.root / "bridge.env"
        env.write_text("ALB_TOKEN=already:here\n", encoding="utf-8")
        os.chmod(env, 0o600)
        self.run_init(answers=["n", "print", ""])
        self.assertIn("already:here", env.read_text(encoding="utf-8"))

    def test_an_existing_allowlist_is_not_emptied(self):
        """Re-running setup on a working bridge must not silently disarm it -
        an allowlist reset to deny-all reads exactly like a dead bot."""
        self.root.mkdir(parents=True)
        allow = self.root / "allowlist.json"
        allow.write_text(json.dumps({"chats": ["111"]}), encoding="utf-8")
        os.chmod(allow, 0o600)
        self.run_init(answers=["n", "print", ""])
        self.assertEqual(json.loads(allow.read_text())["chats"], ["111"])

    def test_it_says_what_it_skipped(self):
        """Leaving a file alone silently is how someone edits the wrong copy."""
        self.root.mkdir(parents=True)
        (self.root / "bridge.env").write_text("ALB_TOKEN=already:here\n", encoding="utf-8")
        os.chmod(self.root / "bridge.env", 0o600)
        console, _ = self.run_init(answers=["n", "print", ""])
        self.assertIn("bridge.env", console.transcript)
        self.assertIn("kept", console.transcript.lower())


class TheSecretNeverLands(Base):
    def test_the_token_is_asked_for_without_echo(self):
        """ask_secret, not ask. A token echoed into a terminal is a token in a
        scrollback buffer."""
        console = ScriptedConsole(["n", "print", ""], ["123456:TOKEN"])
        self.run_init(answers=None)  # not used; kept for symmetry
        wizard.init(self.root / "echo-check", console,
                    helper_found=lambda name: "x", cmux_born=lambda: False,
                    bridge_running=lambda root: False)
        self.assertEqual(console.secrets, [])

    def test_the_token_is_never_printed_back(self):
        console, _ = self.run_init(answers=["n", "print", ""])
        self.assertNotIn("123456:TOKEN", console.transcript)

    def test_there_is_no_flag_that_would_put_a_token_in_shell_history(self):
        """Pi's constraint, pinned as a signature check: a --token flag is the
        failure, because argv is the one place a secret is recorded by
        something the operator does not control."""
        import inspect
        params = set(inspect.signature(wizard.init).parameters)
        self.assertNotIn("token", params)


class TheMailboxQuestionIsSmallFirst(Base):
    """Grok, who is the only person to have run an integrated install:

        "Ask 'does this agent already receive mail from other agents on this
        machine?' then, only if yes, path and name. Blank on those two must not
        silently become standalone-with-a-token."

    The first question was a single prompt carrying mode, path and name at
    once. Simon had already decided integrated, ran init, and got a standalone
    config - then Grok hand-appended three keys. Nothing errored.
    """

    def test_the_first_question_is_answerable_without_knowing_a_path(self):
        console, _ = self.run_init(answers=["n", "print", ""])
        first = console.asked[0].lower()
        self.assertNotIn("path", first)

    def test_no_is_standalone_and_asks_nothing_further_about_mail(self):
        console, result = self.run_init(answers=["n", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        self.assertFalse(any("participant" in q.lower() for q in console.asked))

    def test_blank_is_treated_as_no(self):
        console, result = self.run_init(answers=["", "print", ""])
        self.assertEqual(result["mode"], "standalone")

    def test_yes_then_path_and_name_gives_a_complete_integrated_config(self):
        mailbox = pathlib.Path(self.tmp.name) / "seat"
        (mailbox / "inbox").mkdir(parents=True)
        _, result = self.run_init(answers=["y", str(mailbox), "an-agent", "print", ""])
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertEqual(result["mode"], "integrated")
        self.assertIn(f"ALB_MAIL_ROOT={mailbox}", env)
        self.assertIn("ALB_TO=an-agent", env)

    def test_yes_then_a_blank_path_does_not_silently_become_standalone(self):
        """The exact failure. Saying yes and then leaving a prompt empty
        produced a token-only config that runs, delivers, and rings nowhere the
        operator expected - with no error at any point."""
        console, result = self.run_init(answers=["y", "", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        transcript = console.transcript.lower()
        self.assertIn("standalone", transcript)
        self.assertIn("re-run", transcript)

    def test_yes_then_a_blank_name_does_not_silently_become_standalone(self):
        mailbox = pathlib.Path(self.tmp.name) / "seat"
        (mailbox / "inbox").mkdir(parents=True)
        console, result = self.run_init(answers=["y", str(mailbox), "", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        self.assertIn("re-run", console.transcript.lower())

    def test_a_downgraded_install_stops_behaving_as_integrated(self):
        """Found by the mutation gate, not by me. Asserting the reported mode
        was not enough: the fallback also has to stop the rest of the run
        treating this as integrated, and the visible consequence is the
        letterbox helper. A standalone bridge never calls it, so being asked
        for its path is being asked to solve a problem you do not have."""
        console, result = self.run_init(
            answers=["y", "", "print", ""],
            helper_found=lambda name: None)
        self.assertEqual(result["mode"], "standalone")
        self.assertFalse(any("helper" in q.lower() for q in console.asked))

    def test_the_examples_are_not_this_machine(self):
        """A shipped example is a path a stranger reads. Ours would either
        leak the team's layout into a repo that may go public, or be
        confidently wrong on their disk."""
        console, _ = self.run_init(answers=["y", "", "print", ""])
        transcript = console.transcript
        for leak in ("shared-" "brain", "grok-build", "simon" "ai", "/Users/"):
            self.assertNotIn(leak, transcript)


class TheHelperIsAskedForOnlyWhenMissing(Base):
    """Grok: do not add a flag. Default bus.sh on PATH; ask only if it is not
    there, and only when integrated - a standalone install never calls it."""

    def _integrated(self, answers, which):
        mailbox = pathlib.Path(self.tmp.name) / "seat"
        (mailbox / "inbox").mkdir(parents=True)
        console = ScriptedConsole(["y", str(mailbox), "an-agent"] + answers,
                                  ["123456:TOKEN"])
        result = wizard.init(self.root, console, helper_found=which,
                             cmux_born=lambda: False,
                             bridge_running=lambda root: False)
        return console, result

    def test_a_helper_on_the_path_is_not_asked_about(self):
        console, _ = self._integrated(["print", ""], which=lambda name: "/usr/local/bin/bus.sh")
        self.assertFalse(any("helper" in q.lower() for q in console.asked))
        self.assertNotIn("ALB_BUS_BINARY", (self.root / "bridge.env").read_text())

    def test_a_missing_helper_is_asked_for(self):
        console, _ = self._integrated(["/opt/bus.sh", "print", ""], which=lambda name: None)
        self.assertIn("ALB_BUS_BINARY=/opt/bus.sh",
                      (self.root / "bridge.env").read_text(encoding="utf-8"))

    def test_a_standalone_install_never_asks_about_the_helper(self):
        console = ScriptedConsole(["n", "print", ""], ["123456:TOKEN"])
        wizard.init(self.root, console, helper_found=lambda name: None,
                    cmux_born=lambda: False, bridge_running=lambda root: False)
        self.assertFalse(any("helper" in q.lower() for q in console.asked))


class InitCatchesWhatTheRuntimeWouldRefuse(Base):
    """Review findings. init's promise (Kimi's constraint, pinned) is that a
    config written by setup passes the loader's own checks. Two inputs broke
    the spirit of that promise silently: a mailbox path that does not exist,
    and an empty token."""

    def test_a_mailbox_that_does_not_exist_downgrades_loudly(self):
        """The runtime now refuses to invent a mailbox, so a config naming a
        ghost path would fail at first run - hours after the operator walked
        away believing the install was done. init is the moment the human is
        still present, so it is the moment to say so."""
        console, result = self.run_init(
            answers=["y", str(self.root / "no-such-seat"), "an-agent", "print", ""])
        self.assertEqual(result["mode"], "standalone")
        transcript = console.transcript.lower()
        self.assertIn("does not exist", transcript)
        self.assertIn("re-run", transcript)
        self.assertNotIn("ALB_MAIL_ROOT",
                         (self.root / "bridge.env").read_text(encoding="utf-8"))

    def test_an_empty_token_is_named_before_the_operator_walks_away(self):
        console, _ = self.run_init(answers=["n", "print", ""], secrets=("",))
        transcript = console.transcript.lower()
        self.assertIn("no token", transcript)
        self.assertIn("refuse to start", transcript)


class TheResidentOffer(Base):
    """init ends by offering to start the bridge - the gap both real installs
    stalled in. Kimi's review shaped every branch here:

    - the offer appears ONLY when init itself is cmux-born, because pane
      creation obeys the same born-inside ACL as the ring; an offer whose
      precondition is invisible becomes a reported failure that looks like
      a product bug
    - consent means seeing the exact command and title BEFORE answering,
      and the report after names what started and how to stop it
    - the printed fallback is byte-identical to what the pane would run
    - re-run safety is the flock, never a pane-name detector
    """

    def _init(self, answers, started=None, **kw):
        started = started if started is not None else []
        kw.setdefault("cmux_born", lambda: True)
        kw.setdefault("bridge_running", lambda root: False)
        kw.setdefault("start_pane", lambda title, command: started.append((title, command)) or "SURFACE-NEW")
        kw.setdefault("panes", [{"id": "AGENT-PANE", "label": "agent"}])
        console = ScriptedConsole(answers, ["123456:TOKEN"])
        result = wizard.init(self.root, console, **kw)
        return console, result, started

    def test_yes_starts_the_bridge_in_a_new_pane(self):
        console, result, started = self._init(["n", "print", "AGENT-PANE", "y"])
        self.assertEqual(len(started), 1)
        title, command = started[0]
        self.assertIn("DO NOT CLOSE", title)
        self.assertIn(f"--root {self.root}", command)
        self.assertIn(f"--config {self.root}/bridge.env", command)

    def test_the_command_is_shown_before_the_question(self):
        """Consent to a named thing, not to "start services?"."""
        console, _, started = self._init(["n", "print", "AGENT-PANE", "y"])
        transcript = console.transcript
        self.assertIn("alb --config", transcript[:transcript.index("It can start now")])

    def test_the_report_names_the_surface_and_the_stop_path(self):
        console, _, _ = self._init(["n", "print", "AGENT-PANE", "y"])
        out = console.transcript
        self.assertIn("SURFACE-NEW", out)
        self.assertIn("close", out.lower())

    def test_no_prints_the_identical_command_instead(self):
        console, _, started = self._init(["n", "print", "AGENT-PANE", "no"])
        self.assertEqual(started, [])
        self.assertIn(f"alb --config {self.root}/bridge.env --root {self.root}",
                      console.transcript)

    def test_not_cmux_born_skips_the_question_and_says_why(self):
        console, _, started = self._init(["n", "print", "AGENT-PANE", ""],
                                         cmux_born=lambda: False)
        self.assertEqual(started, [])
        self.assertFalse(any("start the bridge now" in q for q in console.asked))
        out = console.transcript.lower()
        self.assertIn("inside a cmux pane", out)
        self.assertIn("alb --config", console.transcript)

    def test_a_running_bridge_skips_the_offer_entirely(self):
        console, _, started = self._init(["n", "print", "AGENT-PANE", ""],
                                         bridge_running=lambda root: True)
        self.assertEqual(started, [])
        self.assertIn("already running", console.transcript.lower())

    def test_default_is_yes(self):
        console, _, started = self._init(["n", "print", "AGENT-PANE", ""])
        self.assertEqual(len(started), 1)

    def test_a_failed_start_degrades_to_the_printed_command(self):
        def boom(title, command):
            raise RuntimeError("no socket")
        console, result, _ = self._init(["n", "print", "AGENT-PANE", "y"], start_pane=boom)
        self.assertIn("alb --config", console.transcript)
        self.assertIn("could not", console.transcript.lower())


class TheRingAsksForItsSurface(Base):
    """Codex finding 1 (verified): init listed panes, told the operator to
    edit bridge.env later, then offered to START the bridge - which loads the
    env NOW. Saying yes produced a resident with the ring disabled until a
    restart nobody was told about. The ring step now accepts the id the
    operator pastes (they supply it - listing without choosing still holds)
    and writes it to the env BEFORE the resident offer reads it."""

    def test_a_pasted_surface_is_written_to_the_env(self):
        console, result = self.run_init(
            answers=["n", "print", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"],
            panes=[{"id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "label": "agent",
                    "notifier": "cmux"}])
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_SURFACE=0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", env)
        self.assertIn("ALB_NOTIFIER=cmux", env)

    def test_an_unknown_paste_is_not_configured(self):
        """Fat-fingered uuid: success + default cmux is grok's defect one step later."""
        console, result = self.run_init(
            answers=["n", "print", "not-in-the-list"],
            panes=[{"id": "%1", "label": "main:0.0 zsh", "notifier": "tmux"}])
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertNotIn("ALB_SURFACE", env)
        self.assertNotIn("ALB_NOTIFIER", env)
        self.assertNotEqual(result.get("ring"), "configured")
        self.assertIn("not in the list", console.transcript.lower())
        from alb.cli import _init_status
        self.assertEqual(_init_status(result), 1)

    def test_a_tmux_pane_sets_the_notifier_from_the_paste(self):
        """latitude-pi shape: cmux absent, tmux present. No extra question."""
        console, result = self.run_init(
            answers=["n", "print", "%1"],
            panes=[{"id": "%1", "label": "main:0.0 zsh", "notifier": "tmux"}])
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_SURFACE=%1", env)
        self.assertIn("ALB_NOTIFIER=tmux", env)
        self.assertEqual(result.get("ring"), "configured")

    def test_blank_does_not_write_a_surface(self):
        """Blank is not a skip-to-success. No id, no ALB_SURFACE. The
        operator still has to supply one; init will not invent or skip."""
        console, result = self.run_init(
            answers=["n", "print", ""],
            panes=[{"id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "label": "agent"}])
        self.assertNotIn("ALB_SURFACE", (self.root / "bridge.env").read_text(encoding="utf-8"))
        self.assertIn("not an install", console.transcript.lower())

    def test_the_id_must_come_from_the_operator_not_the_listing(self):
        """One pane in the listing is still not a choice init may make."""
        console, result = self.run_init(
            answers=["n", "print", ""],
            panes=[{"id": "only-pane-here", "label": "agent"}])
        self.assertNotIn("only-pane-here", (self.root / "bridge.env").read_text(encoding="utf-8"))

    def test_incomplete_install_is_nonzero_for_the_agent(self):
        """docs/agent-install.md branches on exit status, not on prose."""
        from alb.cli import _init_status
        _, result = self.run_init(answers=["n", "print"], panes=[])
        self.assertEqual(_init_status(result), 1)
        _, result = self.run_init(
            answers=["n", "print", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"],
            panes=[{"id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "label": "agent"}])
        self.assertEqual(result.get("ring"), "configured")
        self.assertEqual(_init_status(result), 0)

    def test_no_visible_panes_is_not_a_silent_skip(self):
        """Grok: empty discovery printed one line and succeeded. Fail closed."""
        console, result = self.run_init(answers=["n", "print"], panes=[])
        self.assertNotIn("ALB_SURFACE", (self.root / "bridge.env").read_text(encoding="utf-8"))
        out = console.transcript.lower()
        self.assertIn("no panes", out)
        self.assertEqual(result.get("resident"), "incomplete")

    def test_it_will_not_start_a_bell_less_bridge(self):
        """Grok's install: init succeeded, ring disabled, --status said
        disabled not broken. Simon: that is not an install."""
        started = []
        console, result = self.run_init(
            answers=["n", "print", "", "y"],
            cmux_born=lambda: True,
            start_pane=lambda title, command: started.append(title) or "S-1")
        self.assertEqual(started, [])
        out = console.transcript.lower()
        self.assertIn("no ring", out)
        self.assertIn("not an install", out)
        self.assertEqual(result.get("resident"), "incomplete")


class RerunningAfterAnIncompleteInstall(Base):
    """Pi's find, and it breaks the recovery path our own message prescribes.

    When a first init finishes bell-less, the wizard tells the operator to
    re-run it and paste a pane id. On that second run bridge.env already
    exists - so the non-clobber guard skipped writing ALB_SURFACE, while the
    ring had already been marked configured from the paste. The bridge then
    started, init exited 0, and the env still had no surface.

    Piece 2's exact defect, reached through the guard that protects the token,
    on the path we documented as the fix for it. Non-clobber must protect what
    is THERE; declining to add a key that is absent is not protection, it is a
    silent failure wearing a safety's clothes."""

    def _existing_env_without_surface(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bridge.env").write_text(
            "ALB_TOKEN=123456:TOKEN\n", encoding="utf-8")
        # 0600, because that is what init writes. A fixture with looser
        # permissions is refused by the real loader - correctly, and it made
        # this test exercise the unreadable path rather than the rerun.
        (self.root / "bridge.env").chmod(0o600)

    def test_a_pasted_pane_reaches_the_env_on_a_rerun(self):
        self._existing_env_without_surface()
        started = []
        self.run_init(
            answers=["n", "print", "fixture-pane", "y"],
            secrets=("123456:TOKEN",),
            panes=[{"id": "fixture-pane", "label": "agent", "notifier": "cmux"}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: started.append(title) or "surface:9")
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_SURFACE=fixture-pane", env,
                      "the paste must reach the file it was asked for")

    def test_it_does_not_report_success_without_the_surface_it_claimed(self):
        """The control that matters: if the surface cannot be persisted, the
        install must not exit 0 having started a bell-less bridge."""
        from alb.cli import _init_status
        self._existing_env_without_surface()
        _, result = self.run_init(
            answers=["n", "print", "fixture-pane", "y"],
            panes=[{"id": "fixture-pane", "label": "agent", "notifier": "cmux"}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: "surface:9")
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        if "ALB_SURFACE" not in env:
            self.assertNotEqual(_init_status(result), 0,
                                "a ring that never reached the env is not a "
                                "configured ring")

    def test_an_existing_token_is_never_overwritten(self):
        """Non-clobber still holds for what is actually there."""
        self._existing_env_without_surface()
        self.run_init(
            answers=["n", "print", "fixture-pane", "y"],
            secrets=("999999:DIFFERENT",),
            panes=[{"id": "fixture-pane", "label": "agent", "notifier": "cmux"}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: "surface:9")
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_TOKEN=123456:TOKEN", env)
        self.assertNotIn("999999:DIFFERENT", env)


class TheEnvIsParsedNotPatternMatched(Base):
    """Pi's four counterexamples. My previous fix asked whether the file
    CONTAINED the text "ALB_SURFACE=" - a question about shape, not meaning.

    A commented-out line contains it. An empty assignment contains it. And a
    file whose last line has no newline turns an append into a JOIN: the new
    key lands on the end of the token's value, changing the one secret in the
    file while reporting success. That is the worst of the four and it is a
    config writer damaging config."""

    def _env(self, text):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bridge.env").write_text(text, encoding="utf-8")
        (self.root / "bridge.env").chmod(0o600)

    def _rerun(self):
        return self.run_init(
            answers=["n", "print", "fixture-pane", "y"],
            panes=[{"id": "fixture-pane", "label": "agent", "notifier": "cmux"}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: "surface:9")

    def _effective(self):
        from alb.bridge import run
        try:
            return run.load_config(self.root / "bridge.env")
        except Exception:  # noqa: BLE001 - an unreadable config is no config
            return {}

    def test_a_commented_surface_is_not_a_configured_surface(self):
        self._env("ALB_TOKEN=123456:TOKEN\n# ALB_SURFACE=old\n")
        self._rerun()
        self.assertEqual(self._effective().get("ALB_SURFACE"), "fixture-pane")

    def test_an_empty_surface_is_not_a_configured_surface(self):
        self._env("ALB_TOKEN=123456:TOKEN\nALB_SURFACE=\n")
        self._rerun()
        self.assertTrue(self._effective().get("ALB_SURFACE"),
                        "an empty value is not a pane")

    def test_a_file_without_a_trailing_newline_keeps_its_token(self):
        """The one that damages rather than misleads."""
        self._env("ALB_TOKEN=123456:TOKEN")   # no newline
        self._rerun()
        self.assertEqual(self._effective().get("ALB_TOKEN"), "123456:TOKEN",
                         "appending must never join onto the previous value")

    def test_a_pane_from_the_other_multiplexer_is_not_saved_as_compatible(self):
        from alb.cli import _init_status
        self._env("ALB_TOKEN=123456:TOKEN\nALB_NOTIFIER=tmux\n")
        _, result = self._rerun()
        config = self._effective()
        if config.get("ALB_NOTIFIER") == "tmux" and config.get("ALB_SURFACE"):
            self.assertNotEqual(
                _init_status(result), 0,
                "a cmux pane under a tmux notifier is not a working ring")


class TheRingClaimIsEstablishedNotAsserted(Base):
    """The gate refused two pins here as hollow - disabled, and no test
    noticed. Same class Codex found earlier: a pin naming a property its
    tests could not distinguish. These two establish them."""

    def _env(self, text):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bridge.env").write_text(text, encoding="utf-8")
        (self.root / "bridge.env").chmod(0o600)

    def _rerun(self):
        return self.run_init(
            answers=["n", "print", "fixture-pane", "y"],
            panes=[{"id": "fixture-pane", "label": "agent", "notifier": "cmux"}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: "surface:9")

    def test_a_surface_already_pinned_is_left_alone(self):
        """Non-clobber, established rather than assumed: reading the env is
        what tells us a surface is there, so a build that stops reading it
        would overwrite somebody's pinned pane."""
        self._env("ALB_TOKEN=123456:TOKEN\nALB_SURFACE=theirs\n")
        self._rerun()
        body = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_SURFACE=theirs", body,
                      "an existing pane id is not ours to replace")
        self.assertNotIn("ALB_SURFACE=fixture-pane", body)

    def test_a_ring_that_never_reached_the_file_is_not_configured(self):
        """Established by making the write not take, which is the only way to
        separate 'somebody typed a pane' from 'the runtime will find one'.
        Without this the word 'configured' survives a write that failed."""
        from unittest import mock
        from alb.cli import _init_status
        from alb.setup import wizard as wiz

        self._env("ALB_TOKEN=123456:TOKEN\n")
        real_open = open

        def refuse_append(path, mode="r", *a, **kw):
            if "a" in mode and str(path).endswith("bridge.env"):
                class _Sink:
                    def __enter__(self_inner): return self_inner
                    def __exit__(self_inner, *exc): return False
                    def write(self_inner, _): pass
                    def writelines(self_inner, _): pass
                return _Sink()
            return real_open(path, mode, *a, **kw)

        with mock.patch.object(wiz, "open", refuse_append, create=True):
            _, result = self._rerun()

        self.assertEqual(result.get("ring"), "not configured",
                         "a paste that never landed is not a configured ring")
        self.assertNotEqual(_init_status(result), 0)


class ARetainedSurfaceKeepsItsOwnType(Base):
    """Pi A: a selected pane cannot establish the type of a DIFFERENT retained
    one. With a surface already pinned and no notifier (so the runtime default
    applies), selecting a tmux pane kept the old surface and appended tmux -
    saving a pair that cannot both be true, and starting on it.

    Pi B: _effective swallowed a loader rejection and returned {}, so an
    INVALID config read as an ABSENT one and we appended a duplicate key.
    Repair the permissions later and the effective pin has silently changed.
    Unreadable is not empty - the same distinction as absence not being death."""

    def _env(self, text, mode=0o600):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bridge.env").write_text(text, encoding="utf-8")
        (self.root / "bridge.env").chmod(mode)

    def _select(self, pane_id, notifier):
        return self.run_init(
            answers=["n", "print", pane_id, "y"],
            panes=[{"id": pane_id, "label": "agent", "notifier": notifier}],
            cmux_born=lambda: True,
            start_pane=lambda title, command: "surface:9")

    def test_a_pane_of_another_type_does_not_relabel_the_retained_one(self):
        from alb.cli import _init_status
        self._env("ALB_TOKEN=123456:TOKEN\nALB_SURFACE=surface:old\n")
        _, result = self._select("%42", "tmux")
        body = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_SURFACE=surface:old", body, "non-clobber still holds")
        self.assertNotIn("ALB_NOTIFIER=tmux", body,
                         "a tmux label on a retained cmux surface is a lie")
        self.assertNotEqual(_init_status(result), 0)

    def test_an_unreadable_config_is_not_treated_as_an_empty_one(self):
        from alb.cli import _init_status
        self._env("ALB_TOKEN=123456:TOKEN\nALB_SURFACE=surface:old\n", mode=0o644)
        before = (self.root / "bridge.env").read_text(encoding="utf-8")
        _, result = self._select("fixture-pane", "cmux")
        after = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertEqual(before, after,
                         "a config we cannot validate must not be appended to")
        self.assertNotEqual(_init_status(result), 0)


class AnIntegratedInstallIsAnInstall(Base):
    """The wizard told a correct integrated install it was not an install.

    Found by installing this product on the maintainer's own seat, which was
    the first cutover that REPLACED a working bridge rather than sitting
    beside one. `_offer_resident` gated on a pane surface being configured,
    but integrated mode does not ring through a pane - `run.py` rings through
    the letterbox helper, and asks for a surface only when NOT integrated.

    So the wizard refused to finish, printed "a poller with nothing to ping
    is not an install", and told the operator to re-run from a multiplexer.
    Two seats on this fleet were already running exactly that configuration
    with a working doorbell. The wizard contradicted the runtime, and the
    runtime was right.
    """

    def _integrated(self, **kw):
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        return self.run_init(["y", str(mailbox), "agent", "print"], **kw)

    def test_the_helper_is_named_as_the_ring(self):
        _console, result = self._integrated()
        self.assertEqual(result["mode"], "integrated")
        self.assertNotEqual(result["ring"], "not configured")

    def test_it_does_not_call_a_working_install_incomplete(self):
        _console, result = self._integrated()
        self.assertNotEqual(result["resident"], "incomplete")

    def test_it_never_says_a_poller_has_nothing_to_ping(self):
        console, _result = self._integrated()
        self.assertNotIn("nothing to ping", console.transcript)

    def test_a_standalone_install_still_demands_its_surface(self):
        """The control. Without a mailbox there IS no helper to ring through,
        so the original refusal must survive untouched - this fix must not
        become a way to ship a bell-less standalone install."""
        console, result = self.run_init(["n", "print"])
        self.assertEqual(result["ring"], "not configured")
        self.assertEqual(result["resident"], "incomplete")
        self.assertIn("nothing to ping", console.transcript)


class TheExitStatusAgreesWithTheWizard(Base):
    """Two places judged whether a bell will ring, and only one was taught
    about integrated mode.

    `_offer_resident` learned that the letterbox helper IS a ring;
    `cli._init_status` kept its own copy of the same judgement and did not.
    So a correct integrated install did the right thing and then reported
    failure - exit 1 with nothing wrong. Found by a review reading it as a
    stranger, after 214 tests covering this area passed.

    The lesson is the shared constant, not the extra branch: a second
    private copy of a rule is a second place to forget.
    """

    def test_a_fresh_integrated_install_exits_zero(self):
        from alb.cli import _init_status
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        _console, result = self.run_init(["y", str(mailbox), "agent", "print"])
        self.assertEqual(result["ring"], "helper")
        self.assertEqual(_init_status(result), 0)

    def test_the_two_judgements_read_the_same_constant(self):
        """A regression guard with teeth: if either side grows its own
        literal again, this fails."""
        from alb import cli
        from alb.setup import wizard
        self.assertIs(cli._RINGS, wizard.RINGS)


class ARerunIntoIntegratedActuallyBecomesIntegrated(Base):
    """Answering "yes, integrated" on a re-run reported integrated and then
    started a bridge whose config was still standalone.

    Non-clobber protects what is THERE. It must not decline to add what is
    ABSENT - the same defect `_persist_ring` was written to close, in the
    other half of the file. Without the mailbox keys the runtime has no
    mailbox and no recipient, so the doorbell it just promised cannot ring,
    and the summary said so anyway.

    The rule this restores: claim the mode only after re-reading the file and
    finding it true. A summary is a report, never a wish.
    """

    def _standalone_then_integrated(self):
        self.run_init(["n", "print"])                      # leaves a standalone env
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        return self.run_init(["y", str(mailbox), "agent", "print"]), mailbox

    def test_the_mailbox_keys_reach_the_config(self):
        (_console, result), mailbox = self._standalone_then_integrated()
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn(f"ALB_MAIL_ROOT={mailbox}", env)
        self.assertIn("ALB_TO=agent", env)
        self.assertEqual(result["mode"], "integrated")

    def test_the_token_already_there_is_untouched(self):
        """The append must not join onto the last line and change the one
        secret in the file."""
        (_console, _result), _mailbox = self._standalone_then_integrated()
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn("ALB_TOKEN=123456:TOKEN\n", env)

    def test_a_config_it_cannot_write_is_not_reported_as_integrated(self):
        """If the keys do not land, the mode must not be claimed."""
        self.run_init(["n", "print"])
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        env_path = self.root / "bridge.env"
        env_path.write_text("this is not = a config\x00", encoding="utf-8")
        _console, result = self.run_init(["y", str(mailbox), "agent", "print"])
        self.assertNotEqual(result.get("mode"), "integrated")


class ARouteIsAPairNotTwoKeys(Base):
    """Append-if-absent, applied per key, invented routes nobody chose.

    A config carrying `ALB_TO=old-agent` and no mailbox, re-run with a NEW
    mailbox and a NEW name, kept the old name and took the new mailbox: mail
    addressed to a participant the operator had not selected, in a directory
    they had. The mirror case did the same the other way and then reported
    the mailbox the operator typed rather than the one saved.

    Non-clobber is right and per-key was the wrong grain. The mailbox and the
    participant are one route: retained values are usable only when the pair
    is empty, or when they already say exactly what was just selected.
    Anything else is refused with both values named, and nothing is written.
    """

    def _rerun(self, existing, answers_mailbox, recipient="new-agent"):
        (self.root).mkdir(parents=True, exist_ok=True)
        env = self.root / "bridge.env"
        env.write_text(existing, encoding="utf-8")
        env.chmod(0o600)
        return self.run_init(["y", str(answers_mailbox), recipient])

    def _mailbox(self, name):
        path = pathlib.Path(self.tmp.name) / name
        path.mkdir(exist_ok=True)
        return path

    def test_a_retained_participant_does_not_take_a_new_mailbox(self):
        new = self._mailbox("new-mail")
        _console, result = self._rerun(
            "ALB_TOKEN=1:T\nALB_TO=old-agent\n", new)
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertNotIn(f"ALB_MAIL_ROOT={new}", env)
        self.assertNotEqual(result.get("mode"), "integrated")

    def test_a_retained_mailbox_does_not_take_a_new_participant(self):
        old = self._mailbox("old-mail")
        new = self._mailbox("new-mail")
        _console, result = self._rerun(
            f"ALB_TOKEN=1:T\nALB_MAIL_ROOT={old}\n", new)
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertNotIn("ALB_TO=new-agent", env)
        self.assertNotEqual(result.get("mode"), "integrated")

    def test_the_operator_is_told_which_two_values_disagree(self):
        new = self._mailbox("new-mail")
        console, _result = self._rerun(
            "ALB_TOKEN=1:T\nALB_TO=old-agent\n", new)
        self.assertIn("old-agent", console.transcript)
        self.assertIn("new-agent", console.transcript)

    def test_a_retained_route_that_already_matches_is_accepted(self):
        """Re-running with the same answers must stay idempotent."""
        old = self._mailbox("old-mail")
        _console, result = self._rerun(
            f"ALB_TOKEN=1:T\nALB_MAIL_ROOT={old}\nALB_TO=same-agent\n",
            old, recipient="same-agent")
        self.assertEqual(result["mode"], "integrated")

    def test_an_empty_pair_is_still_filled_in(self):
        """The case the append existed for must keep working."""
        new = self._mailbox("new-mail")
        _console, result = self._rerun("ALB_TOKEN=1:T\n", new)
        env = (self.root / "bridge.env").read_text(encoding="utf-8")
        self.assertIn(f"ALB_MAIL_ROOT={new}", env)
        self.assertIn("ALB_TO=new-agent", env)
        self.assertEqual(result["mode"], "integrated")


class TheStartAdviceMatchesTheRing(Base):
    """It told every operator that cmux was required.

    Automatic pane creation is implemented only for cmux, and the message
    generalised that into a claim about where the bridge may run. It is wrong
    twice: a tmux ring has no born-inside rule, and an integrated install
    rings through the letterbox helper, so where the bridge runs says nothing
    about whether the bell works. A newcomer on tmux, following it literally,
    is told to install a multiplexer they do not need.

    Printing a correct command is enough; nothing here needs to create a pane.
    """

    def _advice(self, answers, **kw):
        kw.setdefault("cmux_born", lambda: False)
        console, result = self.run_init(answers, **kw)
        return console.transcript, result

    def test_a_tmux_install_is_not_told_to_use_cmux(self):
        panes = [{"id": "%3", "label": "agent", "notifier": "tmux"}]
        transcript, _result = self._advice(["n", "print", "%3"], panes=panes)
        self.assertIn("tmux", transcript)
        self.assertNotIn("cmux refuses processes", transcript)

    def test_an_integrated_install_is_not_promised_it_can_run_anywhere(self):
        """The correction to my own overreach. `_bus_ring` runs the helper in
        the BRIDGE's context - it is not a broker that reaches the pane from
        wherever the bridge happens to live. So a helper that ends up talking
        to cmux still meets cmux's born-inside rule, and telling an operator
        that where it runs 'does not change whether it rings' sells them a
        LaunchAgent that delivers mail silently forever."""
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        transcript, _result = self._advice(["y", str(mailbox), "agent", "print"])
        self.assertNotIn("does not change whether it rings", transcript)
        self.assertIn("supports", transcript)

    def test_an_integrated_install_is_told_to_prove_the_bell(self):
        mailbox = pathlib.Path(self.tmp.name) / "mail"
        mailbox.mkdir()
        transcript, _result = self._advice(["y", str(mailbox), "agent", "print"])
        self.assertIn("--status", transcript)

    def test_a_cmux_install_still_gets_the_born_inside_warning(self):
        """The control: the rule is real where it applies."""
        panes = [{"id": "PANE-1", "label": "agent", "notifier": "cmux"}]
        transcript, _result = self._advice(["n", "print", "PANE-1"], panes=panes)
        self.assertIn("cmux refuses processes", transcript)


class ARefusedRouteEndsTheInstall(Base):
    """Refusing to write the route was not enough: init carried on.

    `_persist_mailbox` returned False, the caller downgraded to standalone,
    and setup continued - offering a pane, writing a surface, and starting a
    resident. So a refusal produced a DIFFERENT install rather than none, and
    where the old config held a complete integrated route the summary said
    standalone while the runtime kept reading the old mailbox and recipient.

    A refusal has to be terminal. Nothing further is written, nothing is
    started, and the exit status says so.
    """

    PANES = [{"id": "PANE-1", "label": "agent", "notifier": "cmux"}]

    def _mailbox(self, name):
        path = pathlib.Path(self.tmp.name) / name
        path.mkdir(exist_ok=True)
        return path

    def _conflict(self, existing):
        self.root.mkdir(parents=True, exist_ok=True)
        env = self.root / "bridge.env"
        env.write_text(existing, encoding="utf-8")
        env.chmod(0o600)
        new = self._mailbox("new-mail")
        started = []
        # Enough answers to complete a standalone install if the guard leaks.
        console, result = self.run_init(
            ["y", str(new), "new-agent", "print", "PANE-1", "y"],
            panes=self.PANES, cmux_born=lambda: True,
            start_pane=lambda title, command: started.append(title) or "S-NEW")
        return console, result, started, env.read_text(encoding="utf-8")

    def test_a_partial_old_route_stops_the_install(self):
        _c, result, started, env = self._conflict("ALB_TOKEN=1:T\nALB_TO=old-agent\n")
        self.assertEqual(started, [])
        self.assertNotIn("ALB_SURFACE", env)
        from alb.cli import _init_status
        self.assertNotEqual(_init_status(result), 0)

    def test_a_full_old_route_stops_the_install(self):
        old = self._mailbox("old-mail")
        _c, result, started, env = self._conflict(
            f"ALB_TOKEN=1:T\nALB_MAIL_ROOT={old}\nALB_TO=old-agent\n")
        self.assertEqual(started, [])
        self.assertNotIn("ALB_SURFACE", env)
        from alb.cli import _init_status
        self.assertNotEqual(_init_status(result), 0)

    def test_the_old_route_is_left_exactly_as_it_was(self):
        """The runtime keeps reading this file. It must not be half-changed."""
        old = self._mailbox("old-mail")
        before = f"ALB_TOKEN=1:T\nALB_MAIL_ROOT={old}\nALB_TO=old-agent\n"
        _c, _result, _started, after = self._conflict(before)
        self.assertEqual(after, before)

    def test_it_does_not_claim_standalone_while_a_route_is_still_there(self):
        old = self._mailbox("old-mail")
        _c, result, _started, _env = self._conflict(
            f"ALB_TOKEN=1:T\nALB_MAIL_ROOT={old}\nALB_TO=old-agent\n")
        self.assertNotEqual(result.get("mode"), "standalone")
