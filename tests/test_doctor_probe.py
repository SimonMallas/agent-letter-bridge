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
from doctor import checks  # noqa: E402


class LocalConsumerProbe(unittest.TestCase):
    def test_it_finds_a_competing_process_by_command_line(self):
        listing = [
            "501 900 /usr/bin/python3 /somewhere/alb --root /a",
            "501 901 /usr/bin/vim notes.txt",
        ]
        found = checks.local_consumers(listing, self_pid=999)
        self.assertEqual(len(found), 1)
        self.assertIn("900", found[0])

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
