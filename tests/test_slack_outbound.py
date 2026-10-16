"""Slack outbound: replies, initiated sends and photos, Telegram's guarantees.

The doctrine is the Telegram adapter's, restated for a second platform:
a definite refusal means nothing was posted, an ambiguous outcome may have
been posted and is never retried, and a 429 is the one provable not-sent case.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from alb import msgindex  # noqa: E402
from alb.adapters.slack import outbound as slack_out  # noqa: E402
from alb.grant import store as grants  # noqa: E402
from alb.initiate import destinations  # noqa: E402
from alb.initiate import send as initiate  # noqa: E402
from alb.letter import store  # noqa: E402
from alb.outbound import store as outbound  # noqa: E402
from alb.send import reply  # noqa: E402

# Built at runtime so nothing here is shaped like a real token.
TOKEN = "xoxb-" + "fixture"
PAIR = "D0000001/U0000001"
CHANNEL_PAIR = "C0000001/U0000001"
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde" + b"\x00" * 64)


class HttpError(Exception):
    """Shaped like web.HttpError: a status, maybe a retry-after."""

    def __init__(self, status, retry_after=None):
        super().__init__(f"slack: HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class FakeCall:
    """Scripted Web API. Each method answers from a queue, else ok:true."""

    def __init__(self, **script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, method, token, params=None):
        self.calls.append((method, token, dict(params or {})))
        queue = self.script.get(method.replace(".", "_"))
        answer = queue.pop(0) if queue else None
        if isinstance(answer, BaseException):
            raise answer
        if answer is not None:
            return answer
        if method == "files.getUploadURLExternal":
            return {"ok": True, "upload_url": "https://files.slack.com/upload/v1/x",
                    "file_id": "F0000001"}
        return {"ok": True, "ts": f"1700000000.{len(self.calls):06d}"}

    def methods(self):
        return [c[0] for c in self.calls]


class SlackSender:
    """What Slack.send / Slack.send_photo delegate to after the merge."""

    name = "slack"

    def __init__(self, call=None, upload_status=200, upload_error=None):
        self.call = call or FakeCall()
        self.upload_status = upload_status
        self.upload_error = upload_error
        self.uploads = []

    def _post_bytes(self, url, data):
        self.uploads.append((url, len(data)))
        if self.upload_error:
            raise self.upload_error
        return self.upload_status

    def send(self, chat_id, text, thread=None):
        return slack_out.send(self.call, TOKEN, chat_id, text, thread)

    def send_photo(self, chat_id, data, caption="", thread=None):
        return slack_out.send_photo(self.call, self._post_bytes, TOKEN, chat_id,
                                    data, caption, thread)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        self.outbox = self.root / "outbox"
        self.state = self.root / "state"
        self.state.mkdir()
        self.allow = self.root / "allowlist.json"
        self.allow.write_text(json.dumps({"chats": [PAIR, CHANNEL_PAIR]}),
                              encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def letter(self, pair=PAIR, reply_thread="", ts="1700000000.000100"):
        """A letter shaped as the Slack poller writes it: correspondent,
        provenance, and reply_thread only when there is one."""
        meta = store.envelope(
            sender="slack-bridge", recipient="agent",
            extra={"slack_team": "T0000001",
                   "slack_channel": pair.split("/")[0], "slack_ts": ts,
                   "correspondent": outbound.correspondent_key(
                       self.state, pair, platform="slack")})
        if reply_thread:
            meta["reply_thread"] = reply_thread
        return store.publish(self.inbox, "incoming", meta)

    def reply(self, sender, letter_id, text="a reply", photo_path=None):
        return reply.send_reply(sender, self.inbox, self.state, self.allow,
                                letter_id, text, outbox=self.outbox,
                                agent="agent", photo_path=photo_path)


class Replies(Base):
    def test_reply_goes_to_the_conversation_and_thread_on_the_stored_letter(self):
        sender = SlackSender()
        self.reply(sender, self.letter(CHANNEL_PAIR,
                                       reply_thread="1700000000.000100"))
        method, token, params = sender.call.calls[0]
        self.assertEqual(method, "chat.postMessage")
        self.assertEqual(token, TOKEN)
        self.assertEqual(params["channel"], "C0000001")
        self.assertEqual(params["thread_ts"], "1700000000.000100")

    def test_dm_reply_without_a_thread_posts_top_level(self):
        sender = SlackSender()
        self.reply(sender, self.letter(PAIR))
        params = sender.call.calls[0][2]
        self.assertEqual(params["channel"], "D0000001")
        self.assertNotIn("thread_ts", params)

    def test_reply_refused_when_pair_left_the_allowlist(self):
        letter_id = self.letter(PAIR)
        self.allow.write_text(json.dumps({"chats": [CHANNEL_PAIR]}),
                              encoding="utf-8")
        sender = SlackSender()
        with self.assertRaises(reply.NotPermitted):
            self.reply(sender, letter_id)
        self.assertEqual(sender.call.calls, [])

    def test_bare_conversation_id_on_the_allowlist_does_not_admit_a_pair(self):
        letter_id = self.letter(PAIR)
        self.allow.write_text(json.dumps({"chats": ["D0000001"]}),
                              encoding="utf-8")
        with self.assertRaises(reply.NotPermitted):
            self.reply(SlackSender(), letter_id)

    def test_reply_is_indexed_so_a_thread_reply_links_back(self):
        sender = SlackSender()
        out_id = self.reply(sender, self.letter(PAIR))
        posted_ts = "1700000000.000001"
        self.assertEqual(msgindex.lookup(self.state, "slack", PAIR, posted_ts),
                         out_id)
        self.assertIsNone(msgindex.lookup(self.state, "telegram", PAIR,
                                          posted_ts))

    def test_outbound_letter_is_addressed_to_the_slack_bridge(self):
        out_id = self.reply(SlackSender(), self.letter(PAIR))
        stored = store.resolve(self.outbox, out_id)
        self.assertEqual(stored.meta["to"], "slack-bridge")

    def test_telegram_correspondent_never_resolves_for_slack(self):
        key = outbound.correspondent_key(self.state, "42", platform="telegram")
        self.assertEqual(outbound.origin_chat_for(self.state, key), "42")
        self.assertIsNone(
            outbound.origin_chat_for(self.state, key, platform="slack"))
        slack_key = outbound.correspondent_key(self.state, PAIR, platform="slack")
        self.assertIsNone(outbound.origin_chat_for(self.state, slack_key))
        self.assertEqual(
            outbound.origin_chat_for(self.state, slack_key, platform="slack"),
            PAIR)

    def test_a_raw_chat_id_letter_is_not_a_slack_destination(self):
        # Pre-correspondent Telegram letters carry a raw chat id. That
        # fallback is Telegram's alone.
        self.assertEqual(reply.destination({"chat_id": "42"}), "42")
        self.assertIsNone(reply.destination({"chat_id": PAIR},
                                            platform="slack"))


class Escaping(Base):
    def test_send_escapes_control_characters_and_disables_markup(self):
        sender = SlackSender()
        sender.send(PAIR, "<!channel> a & b <@U0000002> *bold*")
        params = sender.call.calls[0][2]
        self.assertEqual(params["text"],
                         "&lt;!channel&gt; a &amp; b &lt;@U0000002&gt; *bold*")
        self.assertEqual(params["mrkdwn"], "false")
        self.assertEqual(params["unfurl_links"], "false")
        self.assertEqual(params["unfurl_media"], "false")

    def test_ampersand_is_escaped_once_not_twice(self):
        self.assertEqual(slack_out.escape("&lt;"), "&amp;lt;")
        self.assertEqual(slack_out.escape("a<b>c"), "a&lt;b&gt;c")

    def test_send_refuses_text_slack_would_truncate(self):
        sender = SlackSender()
        with self.assertRaises(reply.DefiniteRefusal):
            sender.send(PAIR, "a" * (slack_out.MAX_TEXT + 1))
        self.assertEqual(sender.call.calls, [])
        # Measured after escaping: that is what Slack counts.
        with self.assertRaises(reply.DefiniteRefusal):
            sender.send(PAIR, "<" * (slack_out.MAX_TEXT // 4 + 1))
        self.assertEqual(sender.call.calls, [])

    def test_a_non_slack_destination_is_refused_before_any_request(self):
        sender = SlackSender()
        for bad in ("8675309", "D0000001", "x0000001/U0000001", "../D1/U1", ""):
            with self.assertRaises(reply.DefiniteRefusal, msg=bad):
                sender.send(bad, "hi")
        self.assertEqual(sender.call.calls, [])

    def test_token_travels_only_as_the_token_argument(self):
        sender = SlackSender()
        sender.send(PAIR, "hi")
        _method, token, params = sender.call.calls[0]
        self.assertEqual(token, TOKEN)
        self.assertNotIn(TOKEN, json.dumps(params))


class Outcomes(Base):
    def _reply_outcome(self, answer):
        sender = SlackSender(FakeCall(chat_postMessage=[answer]))
        letter_id = self.letter(PAIR)
        return sender, letter_id

    def test_internal_error_is_ambiguous_and_dead_lettered_never_retried(self):
        sender, letter_id = self._reply_outcome(
            {"ok": False, "error": "internal_error"})
        with self.assertRaises(reply.AmbiguousOutcome):
            self.reply(sender, letter_id)
        self.assertEqual(sender.call.methods(), ["chat.postMessage"])
        dead = list((self.state / "dead-letters").glob("*"))
        self.assertTrue(dead, "an ambiguous Slack outcome must dead-letter")
        # The claim holds: asking again refuses rather than posting twice.
        with self.assertRaises(reply.AlreadyClaimed):
            self.reply(sender, letter_id)
        self.assertEqual(sender.call.methods(), ["chat.postMessage"])

    def test_server_error_and_network_failure_are_ambiguous(self):
        for answer in (HttpError(502), TimeoutError("read timed out"),
                       ConnectionResetError("reset"), ValueError("bad json")):
            sender = SlackSender(FakeCall(chat_postMessage=[answer]))
            with self.assertRaises(reply.AmbiguousOutcome, msg=repr(answer)):
                sender.send(PAIR, "hi")

    def test_other_slack_errors_are_definite_refusals(self):
        for error in ("channel_not_found", "not_in_channel", "is_archived",
                      "msg_too_long", "invalid_auth"):
            sender = SlackSender(FakeCall(chat_postMessage=[
                {"ok": False, "error": error}]))
            with self.assertRaises(reply.DefiniteRefusal, msg=error) as cm:
                sender.send(PAIR, "hi")
            self.assertIn(error, str(cm.exception))

    def test_unexpected_error_text_is_not_echoed(self):
        sender = SlackSender(FakeCall(chat_postMessage=[
            {"ok": False, "error": "Bearer " + TOKEN}]))
        with self.assertRaises(reply.DefiniteRefusal) as cm:
            sender.send(PAIR, "hi")
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_http_4xx_is_definite(self):
        sender = SlackSender(FakeCall(chat_postMessage=[HttpError(400)]))
        with self.assertRaises(reply.DefiniteRefusal):
            sender.send(PAIR, "hi")

    def test_rate_limit_is_throttled_and_resumable(self):
        call = FakeCall(chat_postMessage=[HttpError(429, retry_after=17)])
        sender = SlackSender(call)
        letter_id = self.letter(CHANNEL_PAIR, reply_thread="1700000000.000100")
        with self.assertRaises(reply.Throttled) as cm:
            self.reply(sender, letter_id, text="later")
        self.assertEqual(cm.exception.retry_after, 17)
        out_id = f"reply-{letter_id}"
        sent = reply.resume_throttled(sender, self.inbox, self.state,
                                      self.allow, out_id, self.outbox)
        self.assertEqual(sent, out_id)
        self.assertEqual(call.methods(), ["chat.postMessage", "chat.postMessage"])
        params = call.calls[1][2]
        self.assertEqual(params["channel"], "C0000001")
        self.assertEqual(params["thread_ts"], "1700000000.000100")
        self.assertEqual(params["text"], "later")


class Photos(Base):
    def test_photo_reply_uploads_then_shares_into_the_thread(self):
        sender = SlackSender()
        file_id = sender.send_photo(CHANNEL_PAIR, PNG, caption="look <here>",
                                    thread="1700000000.000100")
        self.assertEqual(file_id, "F0000001")
        self.assertEqual(sender.call.methods(),
                         ["files.getUploadURLExternal",
                          "files.completeUploadExternal"])
        self.assertEqual(sender.uploads,
                         [("https://files.slack.com/upload/v1/x", len(PNG))])
        complete = sender.call.calls[1][2]
        self.assertEqual(complete["channel_id"], "C0000001")
        self.assertEqual(complete["thread_ts"], "1700000000.000100")
        self.assertEqual(complete["initial_comment"], "look &lt;here&gt;")
        self.assertEqual(json.loads(complete["files"])[0]["id"], "F0000001")

    def test_photo_upload_failure_before_complete_is_definite(self):
        cases = [
            SlackSender(FakeCall(files_getUploadURLExternal=[HttpError(503)])),
            SlackSender(FakeCall(files_getUploadURLExternal=[
                {"ok": False, "error": "internal_error"}])),
            SlackSender(upload_status=500),
            SlackSender(upload_error=ConnectionResetError("reset")),
            SlackSender(FakeCall(files_getUploadURLExternal=[
                {"ok": True, "upload_url": "https://evil.example/x",
                 "file_id": "F0000001"}])),
        ]
        for sender in cases:
            with self.assertRaises(reply.DefiniteRefusal):
                sender.send_photo(PAIR, PNG)
            self.assertNotIn("files.completeUploadExternal",
                             sender.call.methods())

    def test_photo_upload_rate_limit_stays_throttled(self):
        sender = SlackSender(FakeCall(files_getUploadURLExternal=[
            HttpError(429, retry_after=5)]))
        with self.assertRaises(reply.Throttled):
            sender.send_photo(PAIR, PNG)

    def test_photo_complete_failure_is_ambiguous(self):
        for answer in (HttpError(502), TimeoutError("timed out"),
                       {"ok": False, "error": "internal_error"}):
            sender = SlackSender(FakeCall(
                files_completeUploadExternal=[answer]))
            with self.assertRaises(reply.AmbiguousOutcome, msg=repr(answer)):
                sender.send_photo(PAIR, PNG)

    def test_photo_preflight_runs_before_any_request(self):
        sender = SlackSender()
        with self.assertRaises(Exception):
            sender.send_photo(PAIR, b"not an image")
        self.assertEqual(sender.call.calls, [])


class Canary(Base):
    def test_canary_sends_through_a_slack_sender(self):
        # Found live: the canary's fixture named a raw chat id, which only
        # Telegram letters may, so a Slack canary was refused at the gate.
        from alb.canary import probe
        self.allow.write_text(json.dumps({"chats": [PAIR]}), encoding="utf-8")
        sender = SlackSender()
        probe.run(sender, self.root)
        method, _token, params = sender.call.calls[0]
        self.assertEqual(method, "chat.postMessage")
        self.assertEqual(params["channel"], "D0000001")
        self.assertIn("canary", params["text"])

    def test_telegram_canary_fixture_is_unchanged(self):
        from alb.canary import probe
        self.allow.write_text(json.dumps({"chats": ["8675309"]}), encoding="utf-8")

        class TelegramLike:
            def __init__(self):
                self.calls = []

            def send(self, chat_id, text):
                self.calls.append(chat_id)
                return "1"

        sender = TelegramLike()
        probe.run(sender, self.root)
        self.assertEqual(sender.calls, ["8675309"])
        fixture = next((self.state / "canary").glob("*.md"))
        self.assertEqual(store.resolve(fixture.parent, fixture.stem).meta["chat_id"],
                         "8675309")


class Initiated(Base):
    LABEL = "owner"

    def _grant(self, platform, chat):
        rec = grants.create(self.state, platform, chat)
        destinations.bind(self.state, self.LABEL, rec["grant_id"])
        return rec

    def _send(self, sender, intent="hello-1", text="a status line"):
        return initiate.send_initiated(
            sender, self.state, self.outbox, self.allow, label=self.LABEL,
            intent_id=intent, text=text, reason="test", seat="agent")

    def test_initiated_send_uses_a_slack_grant_and_limits(self):
        self._grant("slack", PAIR)
        sender = SlackSender()
        self._send(sender)
        params = sender.call.calls[0][2]
        self.assertEqual(params["channel"], "D0000001")
        self.assertNotIn("thread_ts", params)
        # Same intent again is the same message, never a second post.
        with self.assertRaises(initiate.TerminalExists):
            self._send(sender)
        self.assertEqual(sender.call.methods(), ["chat.postMessage"])

    def test_slack_grant_hourly_limit_is_enforced(self):
        rec = self._grant("slack", PAIR)
        grants.set_limits(self.state, rec["grant_id"], per_hour=1)
        sender = SlackSender()
        self._send(sender, intent="one")
        with self.assertRaises(Exception):
            self._send(sender, intent="two")
        self.assertEqual(sender.call.methods(), ["chat.postMessage"])

    def test_telegram_grant_is_refused_on_a_slack_bridge(self):
        self.allow.write_text(json.dumps({"chats": ["8675309", PAIR]}),
                              encoding="utf-8")
        self._grant("telegram", "8675309")
        sender = SlackSender()
        with self.assertRaises(Exception) as cm:
            self._send(sender)
        self.assertIn("platform not supported", str(cm.exception))
        self.assertEqual(sender.call.calls, [])

    def test_slack_grant_is_refused_on_a_telegram_bridge(self):
        self._grant("slack", PAIR)

        class TelegramLike:
            def __init__(self):
                self.calls = []

            def send(self, chat_id, text):
                self.calls.append(chat_id)
                return "1"

        sender = TelegramLike()
        with self.assertRaises(Exception) as cm:
            self._send(sender)
        self.assertIn("platform not supported", str(cm.exception))
        self.assertEqual(sender.calls, [])


if __name__ == "__main__":
    unittest.main()
