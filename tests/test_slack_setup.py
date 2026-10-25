"""`alb --init --platform slack`: Telegram's setup refusals, for Slack.

Everything test_setup.py pins still holds - no invented allowlist entry, no
secret in argv, no network unless asked in that moment, nothing overwritten -
plus what a first-time Slack user needs said plainly.
"""
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.slack import manifest  # noqa: E402
from alb.setup import slack_wizard, wizard  # noqa: E402

BOT = "xoxb-" + "fixture"
APP = "xapp-" + "fixture"


class ScriptedConsole:
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


class FakeCall:
    """The three read-only calls setup may make, and a record of them."""

    def __init__(self, ims=None, history=None, auth=None, socket=None):
        self.ims = ims if ims is not None else [
            {"id": "D0000001", "user": "U0000001"},
            {"id": "D0000002", "user": "USLACKBOT"},
        ]
        self.history = history if history is not None else {
            "D0000001": [{"type": "message", "user": "U0000001",
                          "text": "hello bot", "ts": "1700000000.000100"}],
            "D0000002": [],
        }
        self.auth = auth or {"ok": True, "team": "Example", "team_id": "T0000001",
                             "user_id": "U00000BOT"}
        self.socket = socket or {"ok": True, "url": "wss://example.invalid/x"}
        self.calls = []

    def __call__(self, method, token, params=None):
        self.calls.append((method, token, dict(params or {})))
        if method == "auth.test":
            return self.auth
        if method == "apps.connections.open":
            return self.socket
        if method == "conversations.list":
            return {"ok": True, "channels": self.ims}
        if method == "conversations.history":
            return {"ok": True,
                    "messages": self.history.get(params["channel"], [])}
        raise AssertionError(f"setup must not call {method}")


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class Manifest(unittest.TestCase):
    def test_manifest_has_socket_mode_least_scopes_and_no_always_online(self):
        m = manifest.MANIFEST
        self.assertTrue(m["settings"]["socket_mode_enabled"])
        self.assertFalse(m["settings"]["token_rotation_enabled"])
        self.assertEqual(sorted(m["settings"]["event_subscriptions"]["bot_events"]),
                         ["app_mention", "message.im"])
        self.assertEqual(sorted(m["oauth_config"]["scopes"]["bot"]), sorted([
            "app_mentions:read", "channels:history", "groups:history",
            "im:history", "im:read", "chat:write", "files:read",
            "files:write"]))
        # Shown online while the bridge is stopped is a lie to the person
        # deciding whether to send.
        self.assertNotIn("always_online", m["features"]["bot_user"])
        home = m["features"]["app_home"]
        self.assertTrue(home["messages_tab_enabled"])
        self.assertFalse(home["messages_tab_read_only_enabled"])

    def test_render_is_the_manifest_as_json(self):
        self.assertEqual(json.loads(manifest.render()), manifest.MANIFEST)

    def test_the_bot_is_named_after_its_participant(self):
        """Two apps both called "letterbox" left the person guessing which
        agent each reached."""
        m = json.loads(manifest.render("builder"))
        self.assertEqual(m["features"]["bot_user"]["display_name"], "letterbox-builder")
        self.assertEqual(m["oauth_config"], manifest.MANIFEST["oauth_config"])
        self.assertEqual(manifest.MANIFEST["features"]["bot_user"]["display_name"],
                         "letterbox", "the shared default is not mutated")

    def test_a_name_slack_would_refuse_keeps_the_default(self):
        for name in ("", "Builder", "has space", "x" * 71):
            m = json.loads(manifest.render(name))
            self.assertEqual(m["features"]["bot_user"]["display_name"], "letterbox")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "alb"
        self._probe = unittest.mock.patch(
            "alb.helper_probe.inspect_helper",
            return_value=("ok", "helper emits doorbell-outcome v=1"))
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_init(self, answers=None, secrets=(APP, BOT), call=None, **kw):
        kw.setdefault("helper_found", lambda name: "/usr/local/bin/bus.sh")
        kw.setdefault("cmux_born", lambda: False)
        kw.setdefault("bridge_running", lambda root: False)
        kw.setdefault("start_pane", lambda title, command: (_ for _ in ()).throw(
            AssertionError("start_pane reached without an explicit test override")))
        kw.setdefault("resident_command",
                      "/opt/alb/venv/bin/alb --config /opt/r/bridge.env --root /opt/r")
        self.call = call or FakeCall()
        console = ScriptedConsole(answers, list(secrets))
        result = wizard.init(self.root, console, platform="slack",
                             slack_call=self.call, **kw)
        return console, result

    def env(self):
        lines = (self.root / "bridge.env").read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def allowlist(self):
        return json.loads((self.root / "allowlist.json").read_text())["chats"]


class Tokens(Base):
    def test_tokens_are_asked_secretly_and_never_echoed(self):
        console, _ = self.run_init(answers=["n", "print"])
        self.assertEqual(len([q for q in console.asked if "token" in q]), 2)
        self.assertNotIn(BOT, console.transcript)
        self.assertNotIn(APP, console.transcript)

    def test_env_is_0600_with_platform_and_both_tokens(self):
        self.run_init(answers=["n", "print"])
        env_path = self.root / "bridge.env"
        self.assertEqual(mode(env_path), 0o600)
        env = self.env()
        self.assertEqual(env["ALB_PLATFORM"], "slack")
        self.assertEqual(env["ALB_TOKEN"], BOT)
        self.assertEqual(env["ALB_APP_TOKEN"], APP)

    def test_token_files_are_consumed_and_removed(self):
        tmp = pathlib.Path(self.tmp.name)
        files = {}
        for name, value in (("bot", BOT), ("app", APP)):
            path = tmp / f"{name}.token"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, value.encode())
            os.close(fd)
            files[name] = path
        console, _ = self.run_init(answers=["n", "print"], secrets=(),
                                   token_file=files["bot"],
                                   app_token_file=files["app"])
        self.assertFalse(files["bot"].exists())
        self.assertFalse(files["app"].exists())
        self.assertEqual(self.env()["ALB_TOKEN"], BOT)
        self.assertEqual(self.env()["ALB_APP_TOKEN"], APP)
        self.assertEqual([q for q in console.asked if "token" in q], [])

    def test_wrong_prefix_is_explained_not_written(self):
        # Swapped: the app token pasted where the bot token goes, and back.
        console, _ = self.run_init(answers=["n", "print"], secrets=(BOT, APP))
        env = self.env()
        self.assertNotIn(BOT, env.get("ALB_APP_TOKEN", ""))
        self.assertNotIn(APP, env.get("ALB_TOKEN", ""))
        self.assertIn("xapp-", console.transcript)
        self.assertIn("xoxb-", console.transcript)
        self.assertNotIn(BOT, console.transcript)
        self.assertNotIn(APP, console.transcript)

    def test_a_token_file_with_the_wrong_token_is_refused(self):
        path = pathlib.Path(self.tmp.name) / "app.token"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, BOT.encode())
        os.close(fd)
        with self.assertRaises(wizard.SetupError) as cm:
            self.run_init(answers=["n", "print"], secrets=(), app_token_file=path)
        self.assertNotIn(BOT, str(cm.exception))


class Discovery(Base):
    def test_print_route_never_touches_the_network(self):
        console, _ = self.run_init(answers=["n", "print"])
        self.assertEqual(self.call.calls, [])
        self.assertEqual(self.allowlist(), [])
        # The manual route names both halves of the pair.
        self.assertIn("member ID", console.transcript)
        self.assertIn("/", console.transcript)

    def test_dm_discovery_writes_the_chosen_pair_only(self):
        console, _ = self.run_init(answers=["n", "read", "", "1"])
        self.assertEqual(self.allowlist(), ["D0000001/U0000001"])
        self.assertIn("hello bot", console.transcript)
        methods = [c[0] for c in self.call.calls]
        self.assertEqual(methods[:2], ["auth.test", "apps.connections.open"])
        self.assertIn("conversations.list", methods)
        # Each call carries the token it needs, and only as the token.
        for method, token, params in self.call.calls:
            expected = APP if method == "apps.connections.open" else BOT
            self.assertEqual(token, expected, method)
            self.assertNotIn(token, json.dumps(params))

    def test_blank_or_nonsense_choice_writes_no_entry(self):
        for pick in ("", "x", "9", "0"):
            with self.subTest(pick=pick):
                self.tearDown()
                self.setUp()
                self.run_init(answers=["n", "read", "", pick])
                self.assertEqual(self.allowlist(), [])

    def test_bots_and_quiet_dms_are_not_offered(self):
        console, _ = self.run_init(answers=["n", "read", "", ""])
        self.assertNotIn("USLACKBOT", console.transcript)
        self.assertNotIn("D0000002", console.transcript)

    def test_bad_tokens_are_explained_and_nothing_is_allowlisted(self):
        call = FakeCall(auth={"ok": False, "error": "invalid_auth"})
        console, _ = self.run_init(answers=["n", "read", "", "1"], call=call)
        self.assertIn("invalid_auth", console.transcript)
        self.assertEqual(self.allowlist(), [])
        self.assertEqual([c[0] for c in call.calls], ["auth.test"])

    def test_socket_mode_off_is_explained(self):
        call = FakeCall(socket={"ok": False, "error": "not_allowed_token_type"})
        console, _ = self.run_init(answers=["n", "read", "", "1"], call=call)
        self.assertIn("not_allowed_token_type", console.transcript)
        self.assertEqual(self.allowlist(), [])

    def test_a_failed_read_falls_back_to_the_manual_route(self):
        class Broken(FakeCall):
            def __call__(self, method, token, params=None):
                raise OSError("network unreachable")
        console, _ = self.run_init(answers=["n", "read", "", "1"], call=Broken())
        self.assertIn("member ID", console.transcript)
        self.assertEqual(self.allowlist(), [])


class Words(Base):
    def test_the_printed_manifest_names_the_bot_after_the_participant(self):
        mailbox = pathlib.Path(self.tmp.name) / "box" / "builder"
        (mailbox / "inbox").mkdir(parents=True)
        console, _ = self.run_init(answers=["y", str(mailbox), "builder", "print", ""])
        self.assertIn('"display_name": "letterbox-builder"', console.transcript)

    def test_first_time_user_is_told_what_matters(self):
        console, _ = self.run_init(answers=["n", "print"])
        text = console.transcript
        self.assertIn("https://api.slack.com/apps?new_app=1", text)
        self.assertIn(manifest.render().splitlines()[1], text)
        self.assertIn("before you take any screenshot", text)
        self.assertIn("Basic Information", text)
        self.assertIn("connections:write", text)
        self.assertIn("In a channel, @mention the bot every time", text)
        self.assertIn("every message in channels it", text)
        self.assertIn("One Slack app serves one bridge", text)

    def test_existing_env_and_allowlist_are_kept(self):
        self.root.mkdir(parents=True)
        (self.root / "bridge.env").write_text("ALB_TOKEN=old\n")
        os.chmod(self.root / "bridge.env", 0o600)
        (self.root / "allowlist.json").write_text('{"chats": ["D9/U9"]}')
        self.run_init(answers=["n", "read", "", "1"])
        self.assertEqual((self.root / "bridge.env").read_text(), "ALB_TOKEN=old\n")
        self.assertEqual(self.allowlist(), ["D9/U9"])


class TelegramUnchanged(unittest.TestCase):
    def test_telegram_init_never_mentions_slack(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with unittest.mock.patch("alb.helper_probe.inspect_helper",
                                 return_value=("ok", "x")):
            console = ScriptedConsole(["n", "print"], ["123456:TOKEN"])
            wizard.init(pathlib.Path(tmp.name) / "alb", console,
                        helper_found=lambda n: "/usr/local/bin/bus.sh",
                        cmux_born=lambda: False,
                        bridge_running=lambda r: False,
                        resident_command="/opt/alb --config x --root y")
        self.assertNotIn("Slack", console.transcript)
        env = (pathlib.Path(tmp.name) / "alb" / "bridge.env").read_text()
        self.assertEqual(env, "ALB_TOKEN=123456:TOKEN\n")


if __name__ == "__main__":
    unittest.main()
