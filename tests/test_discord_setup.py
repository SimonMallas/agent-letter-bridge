"""`alb --init --platform discord`: the setup refusals, for Discord.

No invented allowlist entry, no secret echoed or in argv, no network unless
asked in that moment - plus what a first-time Discord user needs said
plainly.
"""
import base64
import pathlib
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.discord import invite  # noqa: E402
from alb.setup import discord_wizard  # noqa: E402

APP_ID = "100000000000000042"
USER = "100000000000000009"
DM = "100000000000000001"
# Shaped like a bot token so the shape check passes, built at runtime so no
# scanner can mistake it for a real one.
TOKEN = ".".join([base64.urlsafe_b64encode(APP_ID.encode()).decode().rstrip("="),
                  "fixture", "fixture"])


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
    def __init__(self, me=None, dm=None, error=None):
        self.me = me or {"id": APP_ID, "username": "letterbox"}
        self.dm = dm or {"id": DM, "type": 1}
        self.error = error
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, token, body))
        if self.error:
            raise self.error
        if (method, path) == ("GET", "/users/@me"):
            return self.me
        if (method, path) == ("POST", "/users/@me/channels"):
            return self.dm
        raise AssertionError(f"setup made a call it must not: {method} {path}")


class Invite(unittest.TestCase):
    def test_invite_url_is_built_offline_with_exact_permissions(self):
        self.assertEqual(invite.application_id(TOKEN), APP_ID)
        query = parse_qs(urlsplit(invite.url(APP_ID)).query)
        self.assertEqual(query["client_id"], [APP_ID])
        self.assertEqual(query["scope"], ["bot"])
        named = {"VIEW_CHANNEL": 10, "SEND_MESSAGES": 11, "ATTACH_FILES": 15,
                 "READ_MESSAGE_HISTORY": 16, "SEND_MESSAGES_IN_THREADS": 38}
        self.assertEqual(int(query["permissions"][0]),
                         sum(1 << bit for bit in named.values()))

    def test_no_admin_or_manage_permission_is_asked_for(self):
        administrator, manage_messages, mention_everyone = 1 << 3, 1 << 13, 1 << 17
        for bit in (administrator, manage_messages, mention_everyone):
            self.assertFalse(invite.PERMISSIONS & bit)

    def test_a_token_that_is_not_a_bot_token_gives_no_id(self):
        for bad in ("", "xoxb-" + "fixture", "abc", "a.b.c",
                    base64.b64encode(b"not-digits").decode() + ".x.y"):
            with self.subTest(bad=bad):
                self.assertIsNone(invite.application_id(bad))
                self.assertFalse(invite.looks_like_a_token(bad))

    def test_invite_step_prints_the_link_without_a_call(self):
        console = ScriptedConsole()
        discord_wizard.invite_step(console, TOKEN)
        self.assertIn(invite.url(APP_ID), console.transcript)


class Token(unittest.TestCase):
    def test_token_never_echoed(self):
        console = ScriptedConsole(secrets=[TOKEN])
        self.assertEqual(discord_wizard.token(console, consume=None), TOKEN)
        self.assertNotIn(TOKEN, console.transcript)
        self.assertNotIn("fixture", console.transcript)

    def test_a_wrong_secret_is_refused_without_echo(self):
        wrong = "xoxb-" + "fixture-pasted-here"
        console = ScriptedConsole(secrets=[wrong])
        self.assertEqual(discord_wizard.token(console, consume=None), "")
        self.assertNotIn(wrong, console.transcript)
        self.assertIn("not a Discord bot token", console.transcript)

    def test_explain_says_to_turn_off_public_bot_and_leave_intents_off(self):
        console = ScriptedConsole()
        discord_wizard.explain(console)
        self.assertLess(console.transcript.index("Install Link"),
                        console.transcript.index("turn OFF Public Bot"))
        self.assertIn("turn OFF Public Bot", console.transcript)
        self.assertIn("Privileged Gateway Intent OFF", console.transcript)


class Pairs(unittest.TestCase):
    def test_read_route_opens_dm_and_sends_nothing(self):
        call = FakeCall()
        console = ScriptedConsole(answers=["read", USER])
        self.assertEqual(discord_wizard.pairs(console, TOKEN, call),
                         [f"{DM}/{USER}"])
        self.assertEqual([(m, p) for m, p, _t, _b in call.calls],
                         [("GET", "/users/@me"), ("POST", "/users/@me/channels")])
        self.assertEqual(call.calls[1][3], {"recipient_id": USER})
        self.assertFalse(any("/messages" in p for _m, p, _t, _b in call.calls))

    def test_print_route_makes_no_call(self):
        call = FakeCall()
        # A valid ID waiting in the answers: print must not go on to use it.
        console = ScriptedConsole(answers=["print", USER])
        self.assertEqual(discord_wizard.pairs(console, TOKEN, call), [])
        self.assertEqual(call.calls, [])
        self.assertIn("Copy User ID", console.transcript)

    def test_the_default_is_print(self):
        call = FakeCall()
        self.assertEqual(discord_wizard.pairs(ScriptedConsole(), TOKEN, call), [])
        self.assertEqual(call.calls, [])

    def test_a_user_id_not_understood_writes_no_entry(self):
        for bad in ("me", "@simon", "12345", f"{USER}/x", ""):
            with self.subTest(bad=bad):
                call = FakeCall()
                console = ScriptedConsole(answers=["read", bad])
                self.assertEqual(discord_wizard.pairs(console, TOKEN, call), [])
                self.assertEqual(call.calls, [])

    def test_the_bots_own_id_is_not_accepted_as_yours(self):
        console = ScriptedConsole(answers=["read", APP_ID])
        self.assertEqual(discord_wizard.pairs(console, TOKEN, FakeCall()), [])

    def test_a_failed_call_falls_back_to_print_without_the_error_text(self):
        console = ScriptedConsole(answers=["read", USER])
        result = discord_wizard.pairs(console, TOKEN,
                                      FakeCall(error=OSError(f"boom {TOKEN}")))
        self.assertEqual(result, [])
        self.assertNotIn(TOKEN, console.transcript)
        self.assertIn("OSError", console.transcript)

    def test_without_a_token_read_falls_back_to_print(self):
        call = FakeCall()
        console = ScriptedConsole(answers=["read"])
        self.assertEqual(discord_wizard.pairs(console, "", call), [])
        self.assertEqual(call.calls, [])


if __name__ == "__main__":
    unittest.main()
