"""Slack Web API calls. The token stays in the header, and errors never echo it."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.slack import web  # noqa: E402

TOKEN = "xoxb-" + "fixture"


class SlackWeb(unittest.TestCase):
    def test_token_travels_only_in_the_authorization_header(self):
        seen = {}

        def opener(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return 200, {}, b'{"ok": false, "error": "not_in_channel"}'

        result = web.call(
            "conversations.history", TOKEN, {"channel": "D0000001"},
            timeout=7, opener=opener,
        )
        request = seen["request"]
        self.assertEqual(result["error"], "not_in_channel")
        self.assertEqual(seen["timeout"], 7)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, request.full_url)
        self.assertNotIn(TOKEN, request.data.decode())
        self.assertTrue(request.full_url.startswith(
            "https://slack.com/api/conversations.history"
        ))

    def test_http_error_text_never_contains_the_token(self):
        def opener(request, timeout):
            return 500, {}, TOKEN.encode()

        with self.assertRaises(web.HttpError) as caught:
            web.call("auth.test", TOKEN, opener=opener)
        self.assertEqual(str(caught.exception), "slack auth.test: HTTP 500")
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_429_carries_retry_after(self):
        def opener(request, timeout):
            return 429, {"Retry-After": "17"}, b""

        with self.assertRaises(web.HttpError) as caught:
            web.call("apps.connections.open", TOKEN, opener=opener)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 17)
        self.assertEqual(
            str(caught.exception), "slack apps.connections.open: HTTP 429"
        )

    def test_get_bytes_refuses_other_hosts_and_redirects(self):
        def boom(request, timeout):
            raise AssertionError("opener was called")

        with self.assertRaises(ValueError):
            web.get_bytes("https://evil.example/file", TOKEN, 100, opener=boom)
        with self.assertRaises(ValueError):
            web.get_bytes("http://files.slack.com/file", TOKEN, 100, opener=boom)

        def redirect(request, timeout):
            return 302, {"Location": "https://evil.example/steal"}, b"no"

        with self.assertRaises(ValueError):
            web.get_bytes(
                "https://files.slack.com/files/F0000001", TOKEN, 100,
                opener=redirect,
            )

    def test_get_bytes_stops_at_max_bytes(self):
        body = b"abcdefghij"

        def opener(request, timeout):
            self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, request.full_url)
            return 200, {}, body

        with self.assertRaises(ValueError) as caught:
            web.get_bytes(
                "https://files.slack.com/files/F0000001", TOKEN, 4, opener=opener,
            )
        self.assertEqual(str(caught.exception), "file too large")
        got = web.get_bytes(
            "https://files.slack.com/files/F0000001", TOKEN, len(body),
            opener=opener,
        )
        self.assertEqual(got, body)

    def test_get_bytes_does_not_read_past_the_limit(self):
        class Stream:
            def __init__(self):
                self.pos = 0

            def read(self, size):
                self.pos += size
                return b"x" * size

            def close(self):
                pass

        stream = Stream()

        def opener(request, timeout):
            return 200, {}, stream

        with self.assertRaises(ValueError) as caught:
            web.get_bytes(
                "https://files.slack.com/files/F0000001", TOKEN, 4,
                opener=opener,
            )
        self.assertEqual(str(caught.exception), "file too large")
        self.assertLessEqual(stream.pos, 5)

    def test_post_bytes_returns_status_without_a_token(self):
        seen = {}

        def opener(request, timeout):
            seen["request"] = request
            return 201, {}, b""

        status = web.post_bytes(
            "https://uploads.example/put", b"png-bytes", opener=opener,
        )
        self.assertEqual(status, 201)
        self.assertIsNone(seen["request"].get_header("Authorization"))
        self.assertEqual(seen["request"].data, b"png-bytes")
        with self.assertRaises(ValueError):
            web.post_bytes("http://uploads.example/put", b"x", opener=opener)
