"""Doctor: local diagnostics only. No token, no platform calls, no getUpdates."""
import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doctor import checks  # noqa: E402


class DoctorBoundary(unittest.TestCase):
    def test_it_asserts_its_own_environment_holds_no_token(self):
        """Hermes' constraint, promoted from policy to a testable invariant."""
        self.assertTrue(checks.env_is_token_free({"HOME": "/x", "PATH": "/bin"}))
        self.assertFalse(checks.env_is_token_free({"TELEGRAM_BOT_TOKEN": "123:abc"}))
        self.assertFalse(checks.env_is_token_free({"ALB_TOKEN": "123:abc"}))

    def test_it_prints_the_webhook_command_rather_than_running_it(self):
        """The one remote read is performed by the operator's own shell."""
        command = checks.webhook_check_command()
        self.assertIn("getWebhookInfo", command)
        self.assertIn("<YOUR_TOKEN>", command)

    def test_the_doctor_package_cannot_reach_the_platform(self):
        """No network capability at all: a doctor that polls is the very thing
        it exists to detect."""
        forbidden = ("urllib", "http", "socket", "requests", "getupdates")
        for path in (ROOT / "src" / "doctor").rglob("*.py"):
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
