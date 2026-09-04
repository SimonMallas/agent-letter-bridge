"""Telegram adapter: the only place that talks to the platform."""
import json
import pathlib
import tempfile
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.telegram import api  # noqa: E402
from alb.poller import loop  # noqa: E402
from alb.send import reply  # noqa: E402


def http_error(code):
    """HTTPError holds a file object, so an unclosed one warns at teardown.
    Warnings in a suite train you to ignore its output."""
    err = urllib.error.HTTPError("u", code, "err", {}, None)
    err.close()
    return err


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
        self.assertEqual(got, [{"update_id": 5, "chat_id": "111", "text": "hi",
                                "message_id": "", "reply_to_message_id": ""}])

    def test_a_non_message_update_is_surfaced_so_it_can_be_consumed(self):
        """Edits, reactions and channel posts must not be silently dropped.

        Dropping them means the poller never acks them, so the mark never
        advances past them and they re-arrive on EVERY poll indefinitely - the
        same queue-wedge the deny fix already closed once. They are surfaced
        with no chat, so the fail-closed allowlist denies them and the poller
        consumes them without writing a letter.
        """
        with self._reply({"ok": True, "result": [{"update_id": 5}]}):
            got = self.client.fetch(offset=None)
        self.assertEqual(len(got), 1, "a non-message update was dropped, not consumed")
        self.assertEqual(got[0]["update_id"], 5)
        self.assertEqual(got[0]["chat_id"], "", "a non-message update carried a chat")

    def test_a_non_message_update_does_not_hide_a_real_one(self):
        with self._reply({"ok": True, "result": [
            {"update_id": 5},
            {"update_id": 6, "message": {"chat": {"id": 111}, "text": "hi"}},
        ]}):
            got = self.client.fetch(offset=None)
        self.assertEqual([u["update_id"] for u in got], [5, 6])
        self.assertEqual(got[1]["text"], "hi")

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

    def test_a_transient_network_failure_is_not_fatal(self):
        """A connection reset is normal on a long poll. It must be a condition
        the caller can ride out, not a traceback that kills the bridge.

        Found by a real reset during a live run, not by reasoning: fetch
        classified HTTPError and let URLError escape raw.
        """
        with mock.patch.object(api, "_request", side_effect=urllib.error.URLError("reset")):
            with self.assertRaises(api.TransientFailure):
                self.client.fetch(offset=None)

    def test_a_bare_timeout_is_transient_not_a_crash(self):
        """Found by running the INSTALLED binary against a real bot on a slow
        network. A read timeout deep in the SSL layer raises TimeoutError
        directly - it is not wrapped in URLError - so classifying only URLError
        let it escape as a traceback and kill the bridge.

        Same shape as the last two: I classified the error I had thought of.
        """
        with mock.patch.object(api, "_request", side_effect=TimeoutError("timed out")):
            with self.assertRaises(api.TransientFailure):
                self.client.fetch(offset=None)

    def test_a_connection_error_is_transient(self):
        with mock.patch.object(api, "_request", side_effect=ConnectionResetError("reset")):
            with self.assertRaises(api.TransientFailure):
                self.client.fetch(offset=None)

    def test_a_bare_timeout_on_send_is_ambiguous(self):
        """The POST may have arrived. Never auto-retried."""
        with mock.patch.object(api, "_request", side_effect=TimeoutError("timed out")):
            with self.assertRaises(reply.AmbiguousOutcome):
                self.client.send("111", "hello")

    def test_a_transient_failure_is_not_mistaken_for_a_conflict(self):
        """Yielding on a network blip would hand the token away for no reason."""
        with mock.patch.object(api, "_request", side_effect=urllib.error.URLError("reset")):
            with self.assertRaises(Exception) as caught:
                self.client.fetch(offset=None)
        self.assertNotIsInstance(caught.exception, loop.PlatformConflict)

    def test_a_transient_confirm_failure_does_not_persist_the_offset(self):
        client = self._client() if hasattr(self, "_client") else api.Telegram("123:FAKE")
        client.ack(7)
        with mock.patch.object(api, "_request", side_effect=urllib.error.URLError("reset")):
            with self.assertRaises(api.TransientFailure):
                client.confirm()

    def test_the_offset_is_sent_as_last_acked_plus_one(self):
        """The platform consumes everything below the mark, so the mark must be
        the next wanted id - not the last seen one."""
        self.client.ack(7)
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}) as req:
            self.client.fetch(offset=None)
        self.assertEqual(req.call_args[0][2]["offset"], 8)


class OffsetIsDurableAndTransmitted(unittest.TestCase):
    """Acking in memory is not consuming.

    The platform only forgets an update when the advanced offset is SENT. A
    process that acks internally and exits has consumed nothing, and a process
    that keeps the mark only in memory re-reads everything after a restart.
    Both were true of the first version and neither was visible to a fake that
    treated ack() as immediately effective.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.offset_path = pathlib.Path(self.tmp.name) / "offset.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self):
        return api.Telegram("123:FAKE", offset_path=self.offset_path)

    def test_the_offset_survives_a_restart(self):
        client = self._client()
        client.ack(7)
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}):
            client.confirm()
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}) as req:
            self._client().fetch(offset=None)
        self.assertEqual(req.call_args[0][2]["offset"], 8,
                         "a restarted process re-read consumed updates")

    def test_confirming_transmits_the_offset_to_the_platform(self):
        """A run that ends without transmitting has consumed nothing, however
        many letters it wrote."""
        client = self._client()
        client.ack(7)
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}) as req:
            client.confirm()
        self.assertTrue(req.called, "exited without telling the platform anything")
        self.assertEqual(req.call_args[0][2]["offset"], 8)

    def test_a_failed_confirm_does_not_persist_the_offset(self):
        """PI'S BLOCK. Persisting before the platform has been told is silent
        loss: the mark says consumed, the platform still holds the updates, and
        the next start transmits an offset that makes the platform skip
        messages nobody ever received.

        Persist only what the platform has ACCEPTED.
        """
        client = self._client()
        client.ack(7)
        with mock.patch.object(api, "_request", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(Exception):
                client.confirm()
        self.assertFalse(self.offset_path.exists(),
                         "persisted a high-water mark the platform never accepted")

    def test_the_offset_persists_only_after_a_successful_confirm(self):
        client = self._client()
        client.ack(7)
        self.assertFalse(self.offset_path.exists(), "persisted before confirming")
        with mock.patch.object(api, "_request", return_value={"ok": True, "result": []}):
            client.confirm()
        self.assertTrue(self.offset_path.exists(), "did not persist after confirming")

    def test_confirming_with_nothing_acked_makes_no_call(self):
        client = self._client()
        with mock.patch.object(api, "_request") as req:
            client.confirm()
        req.assert_not_called()


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
