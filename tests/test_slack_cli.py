"""Slack through the binary: the config picks the adapter for every command.

Telegram configs build exactly what they built before. A Slack config builds
a Slack poller holding both tokens and a Slack sender holding only the bot
token - the receiving object has no send path at all.
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
from alb.adapters.slack import api as slack_api  # noqa: E402
from alb.adapters.slack import outbound as slack_out  # noqa: E402
from alb.adapters.telegram import api as telegram_api  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.letter import store  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import registry_isolation  # noqa: E402

setUpModule, tearDownModule = registry_isolation.start, registry_isolation.stop

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"
PAIR = "D0000001/U0000001"


def write_env(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


class OneSlackMessage:
    """A Slack-named platform with one DM, as the real adapter shapes it."""

    name = "slack"

    def __init__(self):
        self.acked = []
        self.confirmed = False
        self.closed = False

    def fetch(self, offset=None):
        return [{
            "update_id": "slack:T0000001:D0000001:1700000000.000100",
            "chat_id": PAIR, "text": "hello from slack", "caption": "",
            "photo_file_id": "", "media_kind": "",
            "message_id": "1700000000.000100", "reply_to_message_id": "",
            "reply_thread": "",
            "provenance": {"slack_team": "T0000001", "slack_channel": "D0000001",
                           "slack_ts": "1700000000.000100"},
        }]

    def ack(self, update_id):
        self.acked.append(update_id)

    def confirm(self):
        self.confirmed = True

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "root"
        self.root.mkdir()
        self.env = pathlib.Path(self.tmp.name) / "bridge.env"
        (self.root / "allowlist.json").write_text(json.dumps({"chats": [PAIR]}))

    def tearDown(self):
        self.tmp.cleanup()

    def slack_env(self, extra=""):
        write_env(self.env, f"ALB_PLATFORM=slack\nALB_TOKEN={BOT}\n"
                            f"ALB_APP_TOKEN={APP}\n{extra}")
        return run.load_config(self.env)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class Factories(Base):
    def test_telegram_config_builds_telegram_objects_exactly_as_before(self):
        write_env(self.env, "ALB_TOKEN=123456:SECRET\n")
        config = run.load_config(self.env)
        built = []
        with mock.patch.object(telegram_api, "Telegram",
                               lambda *a, **k: built.append((a, k)) or "tg"):
            self.assertEqual(run.make_poller(config, self.root), "tg")
            self.assertEqual(run.make_sender(config), "tg")
        self.assertEqual(built[0], (("123456:SECRET",), {
            "offset_path": self.root / "state" / "offset.json"}))
        self.assertEqual(built[1], (("123456:SECRET",), {}))

    def test_slack_config_builds_slack_poller_and_bot_only_sender(self):
        config = self.slack_env()
        poller = run.make_poller(config, self.root)
        self.assertIsInstance(poller, slack_api.Slack)
        self.assertEqual(poller._bot, BOT)
        self.assertEqual(poller._app, APP)
        self.assertEqual(poller._allowlist_path, self.root / "allowlist.json")
        self.assertFalse(hasattr(poller, "send"),
                         "the receiving object must have no send path")
        sender = run.make_sender(config)
        self.assertIsInstance(sender, slack_out.Sender)
        self.assertEqual(sender.name, "slack")
        self.assertNotIn(APP, vars(sender).values())
        self.assertEqual(repr(sender), "<Slack sender>")


class Once(Base):
    def test_once_on_a_slack_bridge_publishes_and_exits_zero(self):
        self.slack_env()
        platform = OneSlackMessage()
        with mock.patch.object(run, "make_poller", lambda config, root: platform):
            code, out, _err = self.main("--config", str(self.env),
                                        "--root", str(self.root), "--once")
        self.assertEqual(code, 0)
        self.assertTrue(platform.confirmed)
        self.assertTrue(platform.closed, "the socket is closed on the way out")
        letters = list((self.root / "inbox").glob("*.md"))
        self.assertEqual(len(letters), 1)
        meta = store.resolve(letters[0].parent, letters[0].stem).meta
        self.assertEqual(meta["from"], "slack-bridge")
        self.assertEqual(meta["slack_channel"], "D0000001")
        self.assertNotIn("telegram_update_id", meta)

    def test_default_sender_name_follows_the_platform(self):
        self.slack_env("ALB_FROM=my-slack\n")
        with mock.patch.object(run, "make_poller",
                               lambda config, root: OneSlackMessage()):
            self.main("--config", str(self.env), "--root", str(self.root), "--once")
        letter = next((self.root / "inbox").glob("*.md"))
        self.assertEqual(store.resolve(letter.parent, letter.stem).meta["from"],
                         "my-slack")

    def test_socket_is_closed_on_yield(self):
        self.slack_env()

        class Yields(OneSlackMessage):
            def fetch(self, offset=None):
                from alb.poller import loop
                raise loop.PlatformConflict("another consumer")

        platform = Yields()
        with mock.patch.object(run, "make_poller", lambda config, root: platform):
            code, _out, _err = self.main("--config", str(self.env),
                                         "--root", str(self.root), "--once")
        self.assertEqual(code, 0)
        self.assertTrue(platform.closed)


class Sending(Base):
    def test_reply_to_uses_the_slack_sender(self):
        self.slack_env()
        state = self.root / "state"
        state.mkdir()
        (self.root / "inbox").mkdir()
        from alb.outbound import store as outbound
        letter_id = store.publish(self.root / "inbox", "hi", store.envelope(
            "slack-bridge", "agent", extra={
                "correspondent": outbound.correspondent_key(
                    state, PAIR, platform="slack")}))
        calls = []

        def fake_call(method, token, params=None):
            calls.append((method, token, params))
            return {"ok": True, "ts": "1700000000.000200"}

        with mock.patch.object(slack_out, "_web_call", lambda: fake_call):
            code, out, err = self.main("--config", str(self.env), "--root",
                                       str(self.root), "--reply-to", letter_id,
                                       "--text", "on it")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[0][0], "chat.postMessage")
        self.assertEqual(calls[0][1], BOT)
        self.assertEqual(calls[0][2]["channel"], "D0000001")

    def test_grant_create_defaults_to_the_bridge_platform(self):
        self.slack_env()
        code, out, err = self.main("--config", str(self.env), "--root",
                                   str(self.root), "--grant-create", "--as",
                                   "owner", "--chat-id", PAIR)
        self.assertEqual(code, 0, err)
        from alb.grant import store as grants
        from alb.initiate import destinations
        state = self.root / "state"
        rec = grants.load(state, destinations.resolve(state, "owner"))
        self.assertEqual(rec["platform"], "slack")

    def test_a_telegram_grant_is_refused_on_a_slack_bridge(self):
        self.slack_env()
        self.main("--config", str(self.env), "--root", str(self.root),
                  "--grant-create", "--as", "owner", "--platform", "telegram",
                  "--chat-id", "42")
        code, _out, err = self.main("--config", str(self.env), "--root",
                                    str(self.root), "--send", "--to", "owner",
                                    "--id", "x1", "--text", "hi", "--reason", "t")
        self.assertNotEqual(code, 0)
        self.assertIn("platform not supported", err)


class Flags(Base):
    def test_app_token_file_is_only_valid_with_init(self):
        self.slack_env()
        code, _out, err = self.main("--config", str(self.env), "--root",
                                    str(self.root), "--app-token-file", "x",
                                    "--once")
        self.assertEqual(code, 2)
        self.assertIn("--app-token-file is only valid with --init", err)


if __name__ == "__main__":
    unittest.main()
