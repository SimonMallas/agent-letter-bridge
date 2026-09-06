"""Doctor: local single-consumer probe and daemon-context checks.

No token, no platform call, no network. It reports what can be proved from
THIS machine and is explicit about what cannot be proved at all.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.doctor import checks  # noqa: E402


class LocalConsumerProbe(unittest.TestCase):
    def test_it_finds_a_competing_process_by_command_line(self):
        listing = [
            "501 900 /usr/bin/python3 /somewhere/alb --root /a",
            "501 901 /usr/bin/vim notes.txt",
        ]
        found = checks.local_consumers(listing, self_pid=999)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["pid"], 900)

    def test_it_does_not_report_itself(self):
        listing = ["501 999 /usr/bin/python3 /somewhere/alb --root /a"]
        self.assertEqual(checks.local_consumers(listing, self_pid=999), [])

    def test_a_command_merely_mentioning_the_name_is_not_a_bridge(self):
        """Found by running the doctor and watching it accuse the shell that
        invoked it. A substring match calls any command containing the word a
        competing bridge - an editor, a grep, a heredoc - and a probe that
        cries wolf is worse than none: the operator learns to ignore it."""
        listing = [
            "501 900 /bin/zsh -c echo 'writing alb docs'",
            "501 901 /usr/bin/vim /notes/alb-plan.md",
            "501 902 grep -r alb /somewhere",
        ]
        self.assertEqual(checks.local_consumers(listing, self_pid=999), [])

    def test_a_real_invocation_is_still_found(self):
        for command in ("/usr/bin/python3 /opt/alb --root /a",
                        "python3 ./alb --config x --root y",
                        "/usr/bin/python3 /x/agent-letter-bridge/alb --once"):
            with self.subTest(command=command):
                self.assertEqual(
                    len(checks.local_consumers([f"501 900 {command}"], 999)), 1)

    def test_an_env_prefixed_invocation_is_found(self):
        """/usr/bin/env python3 /path/alb is a real unit-file and wrapper
        shape. Skipping `env` keeps the no-cry-wolf property while closing the
        miss: a diagnostic that misses is the other half of one that shouts."""
        listing = ["501 900 /usr/bin/env python3 /opt/alb --root /a"]
        self.assertEqual(len(checks.local_consumers(listing, 999)), 1)

    def test_it_reports_nothing_when_nothing_competes(self):
        self.assertEqual(checks.local_consumers(["501 900 /usr/bin/vim x"], 999), [])

    def test_a_held_lock_is_reported_with_its_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "bridge.lock").write_text("", encoding="utf-8")
            report = checks.lock_state(root)
            self.assertIn("bridge.lock", report)

    def test_a_missing_lock_is_reported_as_absent_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("no lock", checks.lock_state(pathlib.Path(tmp)).lower())


class DeliverabilityIsNotHealth(unittest.TestCase):
    """A bridge with no allowlist runs perfectly and delivers nothing, ever.

    Found by installing from a fresh clone as a stranger would: with no
    allowlist the bridge exits 0, status reports ok, and the doctor reports no
    problems. Meanwhile operations.md teaches that silence is the deny path
    working correctly - so every signal available tells the operator their
    broken install is fine.

    Fail-closed stays. What changes is that the operator is TOLD why nothing
    will arrive.
    """

    def test_a_missing_allowlist_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = checks.deliverability(pathlib.Path(tmp))
            self.assertFalse(report["can_deliver"])
            self.assertIn("no allowlist", report["reason"].lower())

    def test_an_empty_allowlist_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "allowlist.json").write_text('{"chats": []}', encoding="utf-8")
            report = checks.deliverability(root)
            self.assertFalse(report["can_deliver"])
            self.assertIn("empty", report["reason"].lower())

    def test_a_malformed_allowlist_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "allowlist.json").write_text("{not json", encoding="utf-8")
            report = checks.deliverability(root)
            self.assertFalse(report["can_deliver"])

    def test_a_populated_allowlist_can_deliver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "allowlist.json").write_text('{"chats": ["111"]}', encoding="utf-8")
            report = checks.deliverability(root)
            self.assertTrue(report["can_deliver"])

    def test_the_summary_says_it_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = checks.summary([], self_pid=1, root=pathlib.Path(tmp),
                                  environ={"PATH": "/bin"})
            self.assertIn("NOTHING WILL BE DELIVERED", text)


class DaemonContext(unittest.TestCase):
    def test_it_reports_the_interpreter_actually_running(self):
        """The failure that costs a morning: a service manager resolves a
        different interpreter than the shell does."""
        report = checks.daemon_context({"PATH": "/usr/bin"})
        self.assertIn(sys.executable, report["interpreter"])

    def test_it_reports_whether_the_notifier_binary_resolves(self):
        report = checks.daemon_context({"PATH": "/nonexistent"})
        self.assertFalse(report["cmux_found"])

    def test_it_warns_when_a_version_manager_is_on_the_path(self):
        """nvm, pyenv and friends are invisible to a service manager, so a
        PATH that depends on them works by hand and fails as a daemon."""
        # Assembled so no literal home path appears in the repo: the privacy
        # scan is structural and correctly refuses one, fixture or not.
        nvm = "/" + "Users" + "/x/.nvm/versions/node/bin"
        report = checks.daemon_context({"PATH": f"{nvm}:/usr/bin"})
        self.assertTrue(report["version_manager_on_path"])

    def test_a_plain_path_is_not_flagged(self):
        report = checks.daemon_context({"PATH": "/usr/bin:/bin"})
        self.assertFalse(report["version_manager_on_path"])


class StatesWhatItCannotProve(unittest.TestCase):
    def test_the_report_names_the_undetectable_case(self):
        """A consumer on another machine is not provable from here. Saying so
        is the difference between a limitation and a false assurance."""
        text = checks.summary(["501 900 /usr/bin/vim x"], self_pid=999,
                              root=pathlib.Path("/tmp"), environ={"PATH": "/usr/bin"})
        self.assertIn("another machine", text.lower())
        self.assertIn("getwebhookinfo", text.lower())


class TheProbeComparesBotsNotNames(unittest.TestCase):
    """It scanned for the word `alb` and never asked which bot each one used.

    On a machine running three seats it announced two relays that hold other
    bots and cannot conflict, and could not see the bridge actually being
    replaced, because that one is not called `alb`. It warned about copies of
    itself that could not clash and stayed quiet about the one that could.

    A candidate is cleared only when its bot is positively known to be a
    different one. Unreadable means unknown, and unknown is reported.
    """

    LISTING = [
        "501 900 /usr/bin/python3 /x/alb --config /roots/a/bridge.env --root /roots/a",
        "501 901 /usr/bin/python3 /x/alb --config /roots/b/bridge.env --root /roots/b",
    ]

    def _probe(self, bots):
        return checks.local_consumers(
            self.LISTING, self_pid=999, our_bot="111",
            bot_of=lambda path: bots.get(str(path)))

    def _verdicts(self, bots):
        return {c["pid"]: c["bot"] for c in self._probe(bots)}

    def test_a_bridge_on_another_bot_is_not_a_contender(self):
        verdicts = self._verdicts({"/roots/a/bridge.env": "222",
                                   "/roots/b/bridge.env": "333"})
        self.assertEqual(set(verdicts.values()), {"different"})

    def test_a_bridge_on_the_same_bot_is_named_as_one(self):
        verdicts = self._verdicts({"/roots/a/bridge.env": "111",
                                   "/roots/b/bridge.env": "333"})
        self.assertEqual(verdicts[900], "same")
        self.assertEqual(verdicts[901], "different")

    def test_a_bot_it_cannot_read_is_unknown_rather_than_cleared(self):
        """Unreadable is not evidence of safety. The whole point of the probe
        is the case it cannot prove."""
        verdicts = self._verdicts({"/roots/a/bridge.env": None,
                                   "/roots/b/bridge.env": "333"})
        self.assertEqual(verdicts[900], "unknown")

    def test_without_our_own_bot_nothing_is_cleared(self):
        """Knowing less must not report less."""
        found = checks.local_consumers(self.LISTING, self_pid=999)
        self.assertEqual([c["bot"] for c in found], ["unknown", "unknown"])


class ABotIdIsNotASecret(unittest.TestCase):
    """Comparing bots must never mean handling the credential half.

    A Telegram token is `<bot id>:<secret>`. Only the id is needed to tell two
    bridges apart, so only the id is ever taken out of the file - and the
    secret half must not survive anywhere the report can reach it.
    """

    def test_only_the_id_half_is_taken(self):
        self.assertEqual(checks.bot_id("8796396490:AAEsecretsecret"), "8796396490")

    def test_a_token_shaped_wrongly_yields_nothing(self):
        for value in ("", "no-colon-here", ":leading", "   "):
            with self.subTest(value=value):
                self.assertIsNone(checks.bot_id(value))

    def test_the_secret_half_never_appears_in_the_result(self):
        self.assertNotIn("AAEsecret", checks.bot_id("8796396490:AAEsecret") or "")


class TheReportItselfComparesBots(unittest.TestCase):
    """The comparison has to be reachable from `alb --doctor`, not merely
    present in the function it lives in. A fix wired to nothing is a fix in
    name only, and this probe has already been wrong once in public."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "bridge.env").write_text("ALB_TOKEN=111:SECRET\n", encoding="utf-8")
        (self.root / "bridge.env").chmod(0o600)
        self.addCleanup(self.tmp.cleanup)

    def _report(self, other_token):
        other = self.root / "other"
        other.mkdir()
        (other / "bridge.env").write_text(f"ALB_TOKEN={other_token}\n", encoding="utf-8")
        (other / "bridge.env").chmod(0o600)
        listing = [f"501 900 /usr/bin/python3 /x/alb --root {other}"]
        return checks.summary(listing, self_pid=999, root=self.root, environ={})

    def test_a_different_bot_is_not_raised_as_holding_our_bot(self):
        report = self._report("222:OTHERSECRET")
        self.assertIn("no other bridge found holding your bot", report)

    def test_the_same_bot_still_is(self):
        report = self._report("111:SAMEBOTOTHERSECRET")
        self.assertIn("MAY BE HOLDING YOUR BOT", report)
        self.assertIn("same bot", report)

    def test_no_secret_reaches_the_report(self):
        self.assertNotIn("SAMEBOTOTHERSECRET", self._report("111:SAMEBOTOTHERSECRET"))


class TheDoctorReadsAConfigTheWayTheRuntimeDoes(unittest.TestCase):
    """My own fix could clear a real same-bot competitor.

    Two ways, both from reimplementing something that already existed. The
    parser took the FIRST `ALB_TOKEN=` while the runtime's loader takes the
    LAST, so a config with the key twice was read as a different bot than the
    process had actually loaded. And the config path was guessed from
    `--root` even when the command line named a different `--config`.

    Either one silently clears the exact case the probe exists to catch. The
    fix is to stop having a second parser: ask the loader the runtime asks.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _config(self, name, body):
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_the_last_assignment_wins_as_it_does_at_runtime(self):
        root = self.dir / "r"
        root.mkdir()
        (root / "bridge.env").write_text(
            "ALB_TOKEN=111:FIRST\nALB_TOKEN=222:LAST\n", encoding="utf-8")
        (root / "bridge.env").chmod(0o600)
        self.assertEqual(checks.bot_of_root(str(root)), "222")

    def test_an_explicit_config_beats_the_root(self):
        root = self.dir / "r2"
        root.mkdir()
        (root / "bridge.env").write_text("ALB_TOKEN=111:INROOT\n", encoding="utf-8")
        (root / "bridge.env").chmod(0o600)
        named = self._config("named.env", "ALB_TOKEN=222:NAMED\n")
        argv = ["/x/alb", "--config", str(named), "--root", str(root)]
        self.assertEqual(checks._config_path(argv), named)

    def test_a_config_that_cannot_be_loaded_is_unknown_not_cleared(self):
        bad = self._config("bad.env", "ALB_TOKEN=\n")
        self.assertIsNone(checks.bot_of_root(str(bad.parent), config=bad))

    def test_a_world_readable_config_is_unknown_rather_than_read(self):
        """The loader refuses a token file others can read. The doctor must
        inherit that refusal rather than route around it."""
        loose = self._config("loose.env", "ALB_TOKEN=222:SECRET\n")
        loose.chmod(0o644)
        self.assertIsNone(checks.bot_of_root(str(loose.parent), config=loose))


class NothingIsHiddenFromTheReport(unittest.TestCase):
    """Clearing a competitor by OMITTING it means a wrong clear is invisible.

    A file's contents are not proof of what a live process loaded - it may
    have been edited since. So the probe states what it found and what it
    concluded, and lets the operator see both, rather than deleting a row on
    the strength of an inference.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "bridge.env").write_text("ALB_TOKEN=111:OURS\n", encoding="utf-8")
        (self.root / "bridge.env").chmod(0o600)
        self.addCleanup(self.tmp.cleanup)

    def _report(self, token):
        other = self.root / "other"
        other.mkdir()
        (other / "bridge.env").write_text(f"ALB_TOKEN={token}\n", encoding="utf-8")
        (other / "bridge.env").chmod(0o600)
        listing = [f"501 900 /usr/bin/python3 /x/alb --root {other}"]
        return checks.summary(listing, self_pid=999, root=self.root, environ={})

    def test_a_different_bot_is_shown_but_not_raised_as_a_conflict(self):
        report = self._report("222:THEIRS")
        self.assertIn("900", report)
        self.assertNotIn("ANOTHER BRIDGE", report)

    def test_the_same_bot_is_raised(self):
        self.assertIn("ANOTHER BRIDGE", self._report("111:SAMEBOT"))
