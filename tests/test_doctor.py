"""Doctor: local diagnostics only. No token, no platform calls, no getUpdates."""
import ast
import pathlib
import sys
import unittest

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
