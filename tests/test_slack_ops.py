"""Doctor, --status and --check for a Slack bridge. Local files only.

The doctor boundary holds for Slack: no platform call, no credential of its
own. Telling one Slack bridge from another needs only the app id, which is
the public half of the app-level token - the secret half is never kept.
"""
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alb import cli  # noqa: E402
from alb.doctor import checks  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-1-A0000001-1-" + "fixture"


def write_env(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "root"
        (self.root / "state").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def slack_root(self, root=None, app=APP, chats=("D0000001/U0000001",)):
        root = root or self.root
        (root / "state").mkdir(parents=True, exist_ok=True)
        write_env(root / "bridge.env",
                  f"ALB_PLATFORM=slack\nALB_TOKEN={BOT}\nALB_APP_TOKEN={app}\n")
        (root / "allowlist.json").write_text(json.dumps({"chats": list(chats)}))
        return root

    def connections(self, count):
        (self.root / "state" / "slack-connection.json").write_text(
            json.dumps({"connections": count, "at": 1700000000.0}))

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class AppIdentity(Base):
    def test_app_id_is_the_public_half_of_the_app_token(self):
        self.assertEqual(checks.slack_app_id(APP), "A0000001")
        for bad in ("", "xapp-", "xoxb-1-A0000001-1-x", "xapp-1-a0000001-1-x",
                    None, 42):
            self.assertIsNone(checks.slack_app_id(bad), bad)

    def test_doctor_reads_the_app_id_without_a_network_call(self):
        self.slack_root()

        def no_network(*a, **k):
            raise AssertionError("the doctor made a network call")

        with mock.patch("urllib.request.urlopen", no_network), \
                mock.patch("socket.create_connection", no_network):
            self.assertEqual(checks.bot_of_root(self.root), "slack:A0000001")
            report = checks.summary([], os.getpid(), self.root, {})
        self.assertNotIn("fixture", report)

    def test_two_bridges_on_one_slack_app_are_reported_as_one_bot(self):
        other = self.slack_root(pathlib.Path(self.tmp.name) / "other")
        self.slack_root()
        self.assertEqual(checks.bot_of_root(self.root), checks.bot_of_root(other))
        third = self.slack_root(pathlib.Path(self.tmp.name) / "third",
                                app="xapp-1-A0000002-1-" + "fixture")
        self.assertNotEqual(checks.bot_of_root(self.root),
                            checks.bot_of_root(third))

    def test_an_unparseable_app_token_is_unknown_not_cleared(self):
        self.slack_root(app="xapp-" + "fixture")
        self.assertIsNone(checks.bot_of_root(self.root))

    def test_the_app_token_counts_as_a_credential_in_the_environment(self):
        self.assertFalse(checks.env_is_token_free({"ALB_APP_TOKEN": "x"}))

    def test_doctor_webhook_hint_is_for_telegram_only(self):
        self.slack_root()
        report = checks.summary([], os.getpid(), self.root, {})
        self.assertNotIn("getWebhookInfo", report)
        self.assertIn("same Slack app", report)
        tg = pathlib.Path(self.tmp.name) / "tg"
        (tg / "state").mkdir(parents=True)
        write_env(tg / "bridge.env", "ALB_TOKEN=123456:SECRET\n")
        self.assertIn("getWebhookInfo",
                      checks.summary([], os.getpid(), tg, {}))


class Advisories(Base):
    def test_status_warns_on_multiple_connections(self):
        self.slack_root()
        self.connections(2)
        _code, out, _err = self.main("--root", str(self.root), "--status")
        self.assertIn("Slack reports 2 connections for this app", out)
        self.assertIn("One Slack app serves one bridge", out)

    def test_one_connection_is_quiet(self):
        self.slack_root()
        self.connections(1)
        _code, out, _err = self.main("--root", str(self.root), "--status")
        self.assertNotIn("connections for this app", out)

    def test_check_flags_bare_ids_on_a_slack_bridge(self):
        self.slack_root(chats=("D0000001/U0000001", "U0000002"))
        _code, out, _err = self.main("--root", str(self.root), "--check")
        self.assertIn('allowlist entry "U0000002" is not a conversation/member '
                      'pair', out)
        self.assertNotIn("D0000001/U0000001\" is not", out)

    def test_status_with_only_bare_ids_says_nothing_will_be_delivered(self):
        self.slack_root(chats=("D0000001", "U0000001"))
        code, out, _err = self.main("--root", str(self.root), "--status")
        self.assertIn("NOTHING WILL BE DELIVERED", out)
        self.assertNotEqual(code, 0)

    def test_telegram_allowlists_never_trigger_slack_advice(self):
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["8675309", "-100123456"]}))
        self.assertEqual(checks.slack_advisories(self.root), [])
        self.assertTrue(checks.deliverability(self.root)["can_deliver"])
        _code, out, _err = self.main("--root", str(self.root), "--status")
        self.assertNotIn("Slack", out)
        self.assertNotIn("slack", out)


if __name__ == "__main__":
    unittest.main()
