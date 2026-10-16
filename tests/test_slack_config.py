"""Platform selection. A Slack bridge is opted into; a Telegram one is unchanged."""
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.bridge import run  # noqa: E402


class SlackConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "bridge.env"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text, mode=0o600):
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, mode)

    def test_telegram_config_without_platform_still_loads(self):
        self._write("ALB_TOKEN=1:x\n")
        config = run.load_config(self.path)
        self.assertEqual(run.platform_of(config), "telegram")

    def test_slack_requires_both_tokens(self):
        self._write("ALB_PLATFORM=slack\nALB_TOKEN=xoxb-fixture\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("ALB_APP_TOKEN", str(caught.exception))

    def test_unknown_platform_is_refused(self):
        self._write("ALB_PLATFORM=discord\nALB_TOKEN=1:x\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        message = str(caught.exception)
        self.assertIn("telegram", message)
        self.assertIn("slack", message)

    def test_app_token_on_a_telegram_bridge_is_refused(self):
        self._write("ALB_TOKEN=1:x\nALB_APP_TOKEN=xapp-fixture\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertIn("ALB_APP_TOKEN", str(caught.exception))

    def test_slack_token_prefixes_are_checked_without_echoing_them(self):
        bot = "xapp-" + "fixture"
        app = "xapp-" + "app-fixture"
        self._write(
            f"ALB_PLATFORM=slack\nALB_TOKEN={bot}\nALB_APP_TOKEN={app}\n"
        )
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        message = str(caught.exception)
        self.assertIn("ALB_TOKEN", message)
        self.assertIn("xoxb-", message)
        self.assertNotIn(bot, message)
        self.assertNotIn(app, message)

    def test_error_classes_are_shared(self):
        from alb.adapters import errors
        from alb.adapters.telegram import api
        self.assertIs(api.TransientFailure, errors.TransientFailure)
        self.assertIs(api.FetchFailed, errors.FetchFailed)
