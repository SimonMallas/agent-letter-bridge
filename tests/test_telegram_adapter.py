"""Telegram adapter: the only place that talks to the platform."""
import json
import pathlib
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from adapters.telegram import api  # noqa: E402
from poller import loop  # noqa: E402
from send import reply  # noqa: E402


def http_error(code, body=b'{"description":"x"}'):
    return urllib.error.HTTPError("u", code, "err", {}, None)


class Fetch(unittest.TestCase):
    def setUp(self):
        self.client = api.Telegram("123:FAKE")

    def _reply(self, payload):
        return mock.patch.object(api, "_request", return_value=payload)

    def test_updates_are_mapped_to_the_pollers_shape(self):
        with self._reply({"ok": True, "result": [
            {"update_id": 5, "message": {"chat": {"id": 111}, "text": "hi"}}
        ]}):
            got = self.client.fetch(offset=None)
        self.assertEqual(got, [{"update_id": 5, "chat_id": "111", "text": "hi"}])

    def test_an_update_without_a_message_is_skipped_not_crashed(self):
        """Edited messages, reactions and channel posts all arrive here."""
        with self._reply({"ok": True, "result": [{"update_id": 5}]}):
            self.assertEqual(self.client.fetch(offset=None), [])

    def test_a_conflict_becomes_a_yield(self):
        with mock.patch.object(api, "_request", side_effect=http_error(409)):
            with self.assertRaises(loop.PlatformConflict):
                self.client.fetch(offset=None)

    def test_only_a_conflict_is_reported_as_a_conflict(self):
        """A bad token is not a second consumer. Reporting every HTTP failure
        as a conflict sends a 3am operator hunting a phantom poller instead of
        reading the auth error in front of them."""
        for code in (401, 404, 500):
            with self.subTest(code=code):
                with mock.patch.object(api, "_request", side_effect=http_error(code)):
                    with self.assertRaises(api.FetchFailed):
                        self.client.fetch(offset=None)

    def test_a_fetch_failure_is_not_mistaken_for_a_conflict(self):
        with mock.patch.object(api, "_request", side_effect=http_error(401)):
            with self.assertRaises(Exception) as caught:
                self.client.fetch(offset=None)
        self.assertNotIsInstance(caught.exception, loop.PlatformConflict)

    def test_the_offset_is_sent_as_last_acked_plus_one(self):
        """The platform consumes everything below the mark, so the mark must be
        the next wanted id - not the last seen one."""
        self.client.ack(7)
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}) as req:
            self.client.fetch(offset=None)
        self.assertEqual(req.call_args[0][2]["offset"], 8)


class Send(unittest.TestCase):
    def setUp(self):
        self.client = api.Telegram("123:FAKE")

    def test_a_rate_limit_is_a_definite_refusal(self):
        with mock.patch.object(api, "_request", side_effect=http_error(429)):
            with self.assertRaises(reply.DefiniteRefusal):
                self.client.send("111", "hello")

    def test_a_network_failure_is_ambiguous(self):
        """The POST may have arrived and only the response been lost. There is
        no idempotency key, so this can never be retried automatically."""
        with mock.patch.object(api, "_request", side_effect=urllib.error.URLError("reset")):
            with self.assertRaises(reply.AmbiguousOutcome):
                self.client.send("111", "hello")

    def test_a_server_error_is_ambiguous_not_refused(self):
        with mock.patch.object(api, "_request", side_effect=http_error(500)):
            with self.assertRaises(reply.AmbiguousOutcome):
                self.client.send("111", "hello")

    def test_a_client_error_is_a_definite_refusal(self):
        with mock.patch.object(api, "_request", side_effect=http_error(400)):
            with self.assertRaises(reply.DefiniteRefusal):
                self.client.send("111", "hello")


class TokenHygiene(unittest.TestCase):
    def test_the_token_never_appears_in_the_repr(self):
        self.assertNotIn("FAKE", repr(api.Telegram("123:FAKE")))

    def test_the_token_never_appears_in_a_raised_error(self):
        client = api.Telegram("123:FAKE")
        with mock.patch.object(api, "_request", side_effect=http_error(500)):
            with self.assertRaises(reply.AmbiguousOutcome) as caught:
                client.send("111", "hello")
        self.assertNotIn("FAKE", str(caught.exception))
