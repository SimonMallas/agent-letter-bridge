"""Discord outbound: replies, initiated sends and photos, Telegram's guarantees.

The doctrine is the same on every platform: a definite refusal means nothing
was posted, an ambiguous outcome may have been posted and is never retried,
and a 429 is the one provable not-sent case.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alb.adapters.discord import outbound as discord_out  # noqa: E402
from alb.letter import store  # noqa: E402
from alb.outbound import store as outbound  # noqa: E402
from alb.send import reply  # noqa: E402

# Built at runtime so nothing here is shaped like a real token.
TOKEN = "Bot." + "fixture"
DM = "100000000000000001"
CHANNEL = "100000000000000002"
THREAD = "100000000000000003"
USER = "100000000000000009"
MESSAGE = "100000000000000050"
PAIR = f"{DM}/{USER}"
CHANNEL_PAIR = f"{CHANNEL}/{USER}"
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde" + b"\x00" * 64)


class HttpError(Exception):
    """Shaped like rest.HttpError: a status, maybe a retry-after."""

    def __init__(self, status, retry_after=None, code=None):
        super().__init__(f"discord: HTTP {status}")
        self.status = status
        self.retry_after = retry_after
        self.code = code


class FakeCall:
    """Scripted REST. Answers from a queue, else a posted message."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, token, json.loads(json.dumps(body))))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        return {"id": f"1000000000000001{len(self.calls):02d}"}


class FakeMultipart:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, path, token, payload, files):
        self.calls.append((path, token, json.loads(json.dumps(payload)), files))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        return {"id": "100000000000000201"}


class Destinations(unittest.TestCase):
    def test_conversation_is_the_channel_half_of_the_pair(self):
        self.assertEqual(discord_out.conversation_of(PAIR), DM)

    def test_a_non_discord_destination_is_refused_before_any_request(self):
        call = FakeCall()
        for bad in ("12345", "D0000001/U0000001", f"{DM}/", f"/{USER}",
                    f"{DM}/{USER}/x", "-1001234567890", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(reply.DefiniteRefusal):
                    discord_out.send(call, TOKEN, bad, "hi")
        self.assertEqual(call.calls, [])

    def test_a_malformed_thread_is_refused_before_any_request(self):
        call = FakeCall()
        for bad in ("1700000000.000100", f"{CHANNEL}", f"{CHANNEL}:", "x:y"):
            with self.subTest(bad=bad):
                with self.assertRaises(reply.DefiniteRefusal):
                    discord_out.send(call, TOKEN, CHANNEL_PAIR, "hi", thread=bad)
        self.assertEqual(call.calls, [])


class Sending(unittest.TestCase):
    def test_dm_reply_has_no_reference(self):
        call = FakeCall()
        discord_out.send(call, TOKEN, PAIR, "hello")
        method, path, token, body = call.calls[0]
        self.assertEqual((method, path, token), ("POST", f"/channels/{DM}/messages", TOKEN))
        self.assertEqual(body["content"], "hello")
        self.assertNotIn("message_reference", body)

    def test_reply_goes_to_the_recorded_channel_with_a_reference(self):
        call = FakeCall()
        discord_out.send(call, TOKEN, CHANNEL_PAIR, "done",
                         thread=f"{THREAD}:{MESSAGE}")
        _method, path, _token, body = call.calls[0]
        self.assertEqual(path, f"/channels/{THREAD}/messages")
        self.assertEqual(body["message_reference"],
                         {"message_id": MESSAGE, "channel_id": THREAD,
                          "fail_if_not_exists": False})
        # The person who asked is told; nothing else is.
        self.assertIs(body["allowed_mentions"]["replied_user"], True)

    def test_send_never_pings_and_suppresses_embeds(self):
        call = FakeCall()
        text = "@everyone @here <@100000000000000009> <@&100000000000000010> https://example.com"
        discord_out.send(call, TOKEN, CHANNEL_PAIR, text,
                         thread=f"{CHANNEL}:{MESSAGE}")
        body = call.calls[0][3]
        self.assertEqual(body["content"], text, "text arrives as written")
        self.assertEqual(body["allowed_mentions"]["parse"], [])
        self.assertNotIn("users", body["allowed_mentions"])
        self.assertNotIn("roles", body["allowed_mentions"])
        self.assertEqual(body["flags"] & discord_out.SUPPRESS_EMBEDS,
                         discord_out.SUPPRESS_EMBEDS)

    def test_initiated_send_pings_nobody_at_all(self):
        call = FakeCall()
        discord_out.send(call, TOKEN, PAIR, "@everyone")
        self.assertEqual(call.calls[0][3]["allowed_mentions"], {"parse": []})

    def test_send_refuses_over_2000(self):
        call = FakeCall()
        with self.assertRaises(reply.DefiniteRefusal):
            discord_out.send(call, TOKEN, PAIR, "x" * 2001)
        # Counted as Discord counts: an astral character is two units.
        with self.assertRaises(reply.DefiniteRefusal):
            discord_out.send(call, TOKEN, PAIR, "\U0001F600" * 1001)
        self.assertEqual(call.calls, [])
        discord_out.send(call, TOKEN, PAIR, "x" * 2000)
        self.assertEqual(len(call.calls), 1)

    def test_identical_replies_are_both_posted(self):
        call = FakeCall()
        discord_out.send(call, TOKEN, PAIR, "ok")
        discord_out.send(call, TOKEN, PAIR, "ok")
        self.assertEqual(len(call.calls), 2)
        for _m, _p, _t, body in call.calls:
            self.assertNotIn("nonce", body)
            self.assertNotIn("enforce_nonce", body)

    def test_returns_the_posted_message_id(self):
        self.assertEqual(discord_out.send(FakeCall({"id": "100000000000000777"}),
                                          TOKEN, PAIR, "hi"),
                         "100000000000000777")

    def test_token_travels_only_as_the_token_argument(self):
        call = FakeCall()
        discord_out.send(call, TOKEN, PAIR, "hi")
        _m, path, token, body = call.calls[0]
        self.assertEqual(token, TOKEN)
        self.assertNotIn(TOKEN, path + json.dumps(body))


class Outcomes(unittest.TestCase):
    def outcome(self, answer):
        return discord_out.send(FakeCall(answer), TOKEN, PAIR, "hi")

    def test_outcomes_classified_like_slack(self):
        cases = [
            (HttpError(500), reply.AmbiguousOutcome),
            (HttpError(503), reply.AmbiguousOutcome),
            (OSError("connection reset"), reply.AmbiguousOutcome),
            (ValueError("not json"), reply.AmbiguousOutcome),
            (HttpError(400, code=50006), reply.DefiniteRefusal),
            (HttpError(403, code=50001), reply.DefiniteRefusal),
            (HttpError(404, code=10003), reply.DefiniteRefusal),
        ]
        for answer, expected in cases:
            with self.subTest(answer=answer):
                with self.assertRaises(expected):
                    self.outcome(answer)

    def test_rate_limit_is_throttled_and_resumable(self):
        with self.assertRaises(reply.Throttled) as caught:
            self.outcome(HttpError(429, retry_after=1.5))
        self.assertEqual(caught.exception.retry_after, 1.5)

    def test_a_200_without_a_message_id_is_ambiguous(self):
        for answer in ({}, {"id": ""}, ["not", "a", "message"]):
            with self.subTest(answer=answer):
                with self.assertRaises(reply.AmbiguousOutcome):
                    self.outcome(answer)

    def test_error_text_is_never_echoed(self):
        exc = HttpError(400, code="<script>")
        with self.assertRaises(reply.DefiniteRefusal) as caught:
            self.outcome(exc)
        self.assertNotIn("<script>", str(caught.exception))


class Photos(unittest.TestCase):
    def test_photo_is_multipart_with_payload_json(self):
        post = FakeMultipart()
        message_id = discord_out.send_photo(post, TOKEN, CHANNEL_PAIR, PNG,
                                            caption="@here the van",
                                            thread=f"{CHANNEL}:{MESSAGE}")
        self.assertEqual(message_id, "100000000000000201")
        path, token, payload, files = post.calls[0]
        self.assertEqual(path, f"/channels/{CHANNEL}/messages")
        self.assertEqual(token, TOKEN)
        self.assertEqual(payload["content"], "@here the van")
        self.assertEqual(payload["allowed_mentions"]["parse"], [])
        self.assertEqual(payload["message_reference"]["message_id"], MESSAGE)
        self.assertEqual(payload["attachments"], [{"id": 0, "filename": files[0][0]}])
        filename, content_type, data = files[0]
        self.assertEqual(content_type, "image/png")
        self.assertEqual(data, PNG)
        self.assertTrue(filename.endswith(".png"))

    def test_photo_preflight_runs_before_any_request(self):
        post = FakeMultipart()
        with self.assertRaises(Exception):
            discord_out.send_photo(post, TOKEN, PAIR, b"not an image")
        self.assertEqual(post.calls, [])

    def test_photo_outcomes_follow_the_same_doctrine(self):
        for answer, expected in ((OSError("reset"), reply.AmbiguousOutcome),
                                 (HttpError(429, retry_after=2), reply.Throttled),
                                 (HttpError(413, code=40005), reply.DefiniteRefusal)):
            with self.subTest(answer=answer):
                with self.assertRaises(expected):
                    discord_out.send_photo(FakeMultipart(answer), TOKEN, PAIR, PNG)


class TheSender(unittest.TestCase):
    def test_sender_is_named_discord_and_never_shows_its_token(self):
        sender = discord_out.Sender(TOKEN, call=FakeCall(), post_multipart=FakeMultipart())
        self.assertEqual(sender.name, "discord")
        self.assertNotIn(TOKEN, repr(sender))

    def test_sender_delegates_with_its_token(self):
        call, post = FakeCall(), FakeMultipart()
        sender = discord_out.Sender(TOKEN, call=call, post_multipart=post)
        sender.send(PAIR, "hi")
        sender.send_photo(CHANNEL_PAIR, PNG, caption="c", thread=f"{CHANNEL}:{MESSAGE}")
        self.assertEqual(call.calls[0][2], TOKEN)
        self.assertEqual(post.calls[0][1], TOKEN)


class OverLongReply(unittest.TestCase):
    """Found live: a refused over-long reply used to take the letter's one
    reply, so the agent could never answer that letter with shorter text."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.inbox, self.outbox, self.state = (root / "inbox", root / "outbox",
                                               root / "state")
        self.inbox.mkdir()
        self.state.mkdir()
        self.allow = root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [PAIR]}), encoding="utf-8")
        self.call = FakeCall()
        self.sender = discord_out.Sender(TOKEN, call=self.call,
                                         post_multipart=FakeMultipart())
        self.letter_id = store.publish(self.inbox, "incoming", store.envelope(
            sender="discord-bridge", recipient="agent",
            extra={"discord_channel": DM, "discord_message": MESSAGE,
                   "correspondent": outbound.correspondent_key(
                       self.state, PAIR, platform="discord")}))

    def tearDown(self):
        self.tmp.cleanup()

    def reply(self, text):
        return reply.send_reply(self.sender, self.inbox, self.state, self.allow,
                                self.letter_id, text, outbox=self.outbox)

    def test_an_over_long_reply_leaves_the_letter_answerable(self):
        with self.assertRaises(reply.DefiniteRefusal):
            self.reply("x" * (discord_out.MAX_TEXT + 1))
        self.assertEqual(self.call.calls, [], "nothing reached Discord")
        self.assertEqual(list(self.outbox.glob("*.md")), [], "no reply composed")
        self.reply("shorter")
        self.assertEqual([c[3]["content"] for c in self.call.calls], ["shorter"])

    def test_exactly_the_limit_is_sent(self):
        self.reply("x" * discord_out.MAX_TEXT)
        self.assertEqual(len(self.call.calls), 1)


if __name__ == "__main__":
    unittest.main()
