"""Doctor: local diagnostics only. No token, no platform calls, no getUpdates."""
import ast
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.doctor import checks  # noqa: E402


class DoctorBoundary(unittest.TestCase):
    def test_it_asserts_its_own_environment_holds_no_token(self):
        """Hermes' constraint, promoted from policy to a testable invariant.

        Scoped to THIS TOOL's variables. A TELEGRAM_BOT_TOKEN exported by the
        operator's shell for some other purpose is not evidence about the
        doctor, and reporting it as our failure is the wolf that teaches an
        operator to ignore the report.
        """
        self.assertTrue(checks.env_is_token_free({"HOME": "/x", "PATH": "/bin"}))
        self.assertTrue(checks.env_is_token_free({"TELEGRAM_BOT_TOKEN": "123:abc"}))
        self.assertFalse(checks.env_is_token_free({"ALB_TOKEN": "123:abc"}))

    def test_an_operators_own_shell_secrets_are_not_reported_as_our_failure(self):
        """Found by running the doctor in a normal shell, where it reported a
        credential and alarmed about nothing.

        The claim is that THIS TOOL did not load the bot token - not that the
        surrounding shell is free of every secret its owner happens to export.
        A check that fails in almost every real environment is a wolf, and an
        operator who sees one learns to ignore the report.
        """
        shell_env = {"AWS_SECRET_ACCESS_KEY": "x", "GITHUB_TOKEN": "y", "PATH": "/bin"}
        self.assertTrue(checks.env_is_token_free(shell_env))

    def test_a_credential_this_tool_loaded_is_reported(self):
        """The claim that matters: alb itself is not holding the bot token."""
        self.assertFalse(checks.env_is_token_free({"ALB_TOKEN": "1:abc"}))
        self.assertFalse(checks.env_is_token_free({"ALB_BOT_SECRET": "x"}))

    def test_it_prints_the_webhook_command_rather_than_running_it(self):
        """The one remote read is performed by the operator's own shell."""
        command = checks.webhook_check_command()
        self.assertIn("getWebhookInfo", command)
        self.assertIn("<YOUR_TOKEN>", command)

    def test_the_doctor_package_cannot_reach_the_platform(self):
        """No network capability at all: a doctor that polls is the very thing
        it exists to detect."""
        forbidden = ("urllib", "http", "socket", "requests", "getupdates")
        for path in (ROOT / "src" / "alb" / "doctor").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.add((getattr(node, "module", "") or "").lower().split(".")[0])
                    for alias in node.names:
                        names.add(alias.name.lower().split(".")[0])
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr.lower())
            for banned in forbidden:
                self.assertNotIn(banned, names, f"{path.name} can reach the network")

    def test_an_incompatible_helper_is_named_not_silent(self):
        with mock.patch(
                "alb.helper_probe.inspect_helper",
                return_value=("incompatible",
                              "helper does not emit doorbell-outcome v=1")):
            text = checks.summary(
                [], 1, pathlib.Path("/no-such-root"),
                {"ALB_BUS_BINARY": "bus.sh", "PATH": "/bin"})
        self.assertIn("helper does not emit doorbell-outcome v=1", text)
        self.assertIn("DOORBELL HELPER", text)

    def test_unloadable_config_is_not_reported_as_standalone(self):
        """A mode-0644 bridge.env is ConfigError, not 'no helper'."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        env = root / "bridge.env"
        env.write_text(
            "ALB_TOKEN=1:secret\nALB_MAIL_ROOT=/tmp/mail\n", encoding="utf-8")
        os.chmod(env, 0o644)
        text = checks.summary([], 1, root, {"PATH": "/bin"})
        self.assertIn("config not loadable:", text)
        self.assertIn("helper compatibility UNKNOWN", text)
        self.assertNotIn("standalone: no letterbox helper (own notifier)", text)

    def test_unknown_config_key_is_not_reported_as_standalone(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        env = root / "bridge.env"
        env.write_text(
            "ALB_TOKEN=1:secret\nALB_NOTIFIER=cmux\nALB_MADE_UP=1\n",
            encoding="utf-8")
        os.chmod(env, 0o600)
        text = checks.summary([], 1, root, {"PATH": "/bin"})
        self.assertIn("config not loadable:", text)
        self.assertIn("helper compatibility UNKNOWN", text)
        self.assertNotIn("standalone: no letterbox helper (own notifier)", text)

    def test_loaded_standalone_config_keeps_the_standalone_line(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        env = root / "bridge.env"
        env.write_text("ALB_TOKEN=1:secret\nALB_NOTIFIER=cmux\n", encoding="utf-8")
        os.chmod(env, 0o600)
        text = checks.summary([], 1, root, {"PATH": "/bin"})
        self.assertIn("standalone: no letterbox helper (own notifier)", text)
        self.assertNotIn("helper compatibility UNKNOWN", text)
