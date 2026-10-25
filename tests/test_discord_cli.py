"""Discord through the binary: factories, --once, sending, doctor, status, init.

A Discord config builds a Discord poller and a Discord sender; the receiving
object has no send path. The doctor names a Discord bridge by its application
ID, read from the token offline, and never keeps the secret part.
"""
import base64
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
from alb.adapters.discord import api as discord_api  # noqa: E402
from alb.adapters.discord import outbound as discord_out  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.doctor import checks  # noqa: E402
from alb.letter import store  # noqa: E402
from alb.setup import wizard  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import registry_isolation  # noqa: E402

setUpModule, tearDownModule = registry_isolation.start, registry_isolation.stop

APP_ID = "100000000000000042"
TOKEN = ".".join([base64.urlsafe_b64encode(APP_ID.encode()).decode().rstrip("="),
                  "fixture", "fixture"])
DM = "100000000000000001"
USER = "100000000000000009"
PAIR = f"{DM}/{USER}"


def write_env(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


class OneDiscordMessage:
    """A Discord-named platform with one DM, as the real adapter shapes it."""

    name = "discord"

    def __init__(self):
        self.confirmed = False
        self.closed = False

    def fetch(self, offset=None):
        return [{
            "update_id": f"discord:{DM}:100000000000000050",
            "chat_id": PAIR, "text": "hello from discord", "caption": "",
            "photo_file_id": "", "media_kind": "",
            "message_id": "100000000000000050", "reply_to_message_id": "",
            "reply_thread": "",
            "provenance": {"discord_channel": DM,
                           "discord_message": "100000000000000050"},
        }]

    def ack(self, update_id):
        pass

    def confirm(self):
        self.confirmed = True

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "root"
        (self.root / "state").mkdir(parents=True)
        self.env = self.root / "bridge.env"
        (self.root / "allowlist.json").write_text(json.dumps({"chats": [PAIR]}))

    def tearDown(self):
        self.tmp.cleanup()

    def discord_env(self, root=None, token=TOKEN, chats=None):
        root = root or self.root
        (root / "state").mkdir(parents=True, exist_ok=True)
        write_env(root / "bridge.env", f"ALB_PLATFORM=discord\nALB_TOKEN={token}\n")
        if chats is not None:
            (root / "allowlist.json").write_text(json.dumps({"chats": list(chats)}))
        return run.load_config(root / "bridge.env")

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class Factories(Base):
    def test_discord_config_builds_discord_poller_and_sender(self):
        config = self.discord_env()
        poller = run.make_poller(config, self.root)
        self.assertIsInstance(poller, discord_api.Discord)
        self.assertEqual(poller._allowlist_path, self.root / "allowlist.json")
        self.assertFalse(hasattr(poller, "send"),
                         "the receiving object must have no send path")
        sender = run.make_sender(config)
        self.assertIsInstance(sender, discord_out.Sender)
        self.assertEqual(sender.name, "discord")
        self.assertEqual(repr(sender), "<Discord sender>")


class Once(Base):
    def test_once_on_a_discord_bridge_publishes_and_exits_zero(self):
        self.discord_env()
        platform = OneDiscordMessage()
        with mock.patch.object(run, "make_poller", lambda config, root: platform):
            code, _out, err = self.main("--config", str(self.env),
                                        "--root", str(self.root), "--once")
        self.assertEqual(code, 0, err)
        self.assertTrue(platform.confirmed)
        self.assertTrue(platform.closed)
        letter = next((self.root / "inbox").glob("*.md"))
        meta = store.resolve(letter.parent, letter.stem).meta
        self.assertEqual(meta["from"], "discord-bridge")
        self.assertEqual(meta["discord_channel"], DM)


class Sending(Base):
    def test_reply_to_uses_the_discord_sender(self):
        self.discord_env()
        (self.root / "inbox").mkdir()
        from alb.outbound import store as outbound
        letter_id = store.publish(self.root / "inbox", "hi", store.envelope(
            "discord-bridge", "agent", extra={
                "correspondent": outbound.correspondent_key(
                    self.root / "state", PAIR, platform="discord")}))
        calls = []

        def fake_call(method, path, token, body=None):
            calls.append((method, path, token, body))
            return {"id": "100000000000000099"}

        with mock.patch.object(discord_out, "_rest_call", lambda: fake_call):
            code, _out, err = self.main("--config", str(self.env), "--root",
                                        str(self.root), "--reply-to", letter_id,
                                        "--text", "on it")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls[0][:3], ("POST", f"/channels/{DM}/messages", TOKEN))
        self.assertEqual(calls[0][3]["allowed_mentions"], {"parse": []})

    def test_grant_create_defaults_to_the_bridge_platform(self):
        self.discord_env()
        code, _out, err = self.main("--config", str(self.env), "--root",
                                    str(self.root), "--grant-create", "--as",
                                    "owner", "--chat-id", PAIR)
        self.assertEqual(code, 0, err)
        from alb.grant import store as grants
        from alb.initiate import destinations
        state = self.root / "state"
        rec = grants.load(state, destinations.resolve(state, "owner"))
        self.assertEqual(rec["platform"], "discord")

    def test_a_slack_grant_is_refused_on_a_discord_bridge(self):
        self.discord_env()
        self.main("--config", str(self.env), "--root", str(self.root),
                  "--grant-create", "--as", "owner", "--platform", "slack",
                  "--chat-id", "D0000001/U0000001")
        code, _out, err = self.main("--config", str(self.env), "--root",
                                    str(self.root), "--send", "--to", "owner",
                                    "--id", "x1", "--text", "hi", "--reason", "t")
        self.assertNotEqual(code, 0)
        self.assertIn("platform not supported", err)


class Doctor(Base):
    def test_doctor_names_the_application_without_a_network_call(self):
        self.discord_env()

        def no_network(*a, **k):
            raise AssertionError("the doctor made a network call")

        with mock.patch("urllib.request.urlopen", no_network), \
                mock.patch("socket.create_connection", no_network):
            self.assertEqual(checks.bot_of_root(self.root), f"discord:{APP_ID}")
            report = checks.summary([], os.getpid(), self.root, {})
        self.assertNotIn("fixture", report)
        self.assertIn("same Discord bot", report)
        self.assertNotIn("getWebhookInfo", report)

    def test_two_bridges_on_one_bot_are_reported_as_one(self):
        other = pathlib.Path(self.tmp.name) / "other"
        self.discord_env()
        self.discord_env(other)
        self.assertEqual(checks.bot_of_root(self.root), checks.bot_of_root(other))


class Advisories(Base):
    def test_check_flags_bare_ids_on_a_discord_bridge(self):
        self.discord_env(chats=(PAIR, USER))
        _code, out, _err = self.main("--root", str(self.root), "--check")
        self.assertIn(f'allowlist entry "{USER}" is not a channel/user pair', out)
        self.assertNotIn(f'"{PAIR}" is not', out)

    def test_status_with_only_bare_ids_says_nothing_will_be_delivered(self):
        self.discord_env(chats=(DM, USER))
        code, out, _err = self.main("--root", str(self.root), "--status")
        self.assertIn("NOTHING WILL BE DELIVERED", out)
        self.assertIn("bare Discord IDs", out)
        self.assertNotEqual(code, 0)

    def test_telegram_allowlists_never_trigger_discord_advice(self):
        write_env(self.env, "ALB_TOKEN=123456:SECRET\n")
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["8675309", "-100123456"]}))
        self.assertEqual(checks.discord_advisories(self.root), [])
        self.assertTrue(checks.deliverability(self.root)["can_deliver"])


class Init(Base):
    class Console:
        def __init__(self, answers, secrets):
            self.answers, self.secrets, self.said = list(answers), list(secrets), []

        def say(self, text=""):
            self.said.append(str(text))

        def ask(self, question, default=""):
            return self.answers.pop(0) if self.answers else default

        def ask_secret(self, question):
            return self.secrets.pop(0) if self.secrets else ""

    def test_init_writes_a_discord_config_and_the_pair_it_was_given(self):
        root = pathlib.Path(self.tmp.name) / "fresh"
        calls = []

        def call(method, path, token, body=None):
            calls.append((method, path))
            if path == "/users/@me":
                return {"id": APP_ID, "username": "letterbox"}
            return {"id": DM, "type": 1}

        console = self.Console(answers=["n", "read", USER, ""], secrets=[TOKEN])
        wizard.init(root, console, platform="discord", panes=[],
                    discord_call=call, bridge_running=lambda r: False,
                    cmux_born=lambda: False)
        env = (root / "bridge.env").read_text()
        self.assertIn("ALB_PLATFORM=discord\n", env)
        self.assertIn(f"ALB_TOKEN={TOKEN}\n", env)
        self.assertNotIn("ALB_APP_TOKEN", env)
        self.assertEqual(oct((root / "bridge.env").stat().st_mode & 0o777), "0o600")
        self.assertEqual(json.loads((root / "allowlist.json").read_text())["chats"],
                         [PAIR])
        transcript = "\n".join(console.said)
        self.assertNotIn(TOKEN, transcript)
        self.assertIn(f"client_id={APP_ID}", transcript)
        self.assertEqual(calls, [("GET", "/users/@me"), ("POST", "/users/@me/channels")])
        run.load_config(root / "bridge.env")


if __name__ == "__main__":
    unittest.main()
