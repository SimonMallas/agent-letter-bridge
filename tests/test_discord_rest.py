"""Discord REST: the token stays in one header, and a redirect is not a result."""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.discord import rest  # noqa: E402

TOKEN = "Bot." + "fixture"
CDN = "https://cdn.discordapp.com/attachments/100000000000000001/pic.png"
MEDIA = "https://media.discordapp.net/attachments/100000000000000001/pic.png"


class Recording:
    def __init__(self, status=200, headers=None, body=b"{}"):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return self.status, dict(self.headers), self.body


class DiscordRest(unittest.TestCase):
    def test_user_agent_and_auth_header(self):
        opener = Recording(body=b'{"url":"wss://gateway.example"}')
        body = rest.call("GET", "/gateway/bot", TOKEN, opener=opener)
        self.assertEqual(body["url"], "wss://gateway.example")
        request = opener.requests[0]
        self.assertEqual(request.full_url, rest.API + "/gateway/bot")
        self.assertEqual(request.get_header("Authorization"), "Bot " + TOKEN)
        agent = request.get_header("User-agent")
        self.assertTrue(agent.startswith("DiscordBot ("))
        self.assertIn("0.5.0", agent)
        self.assertNotIn(TOKEN, request.full_url)

        posted = Recording(body=b'{"id":"100000000000000021"}')
        result = rest.post_multipart(
            "/channels/100000000000000001/messages", TOKEN,
            {"content": "hi"},
            [("pic.png", "image/png", b"png")],
            opener=posted,
        )
        self.assertEqual(result["id"], "100000000000000021")
        raw = posted.requests[0].data
        self.assertIn(b'name="payload_json"', raw)
        self.assertIn(b'name="files[0]"', raw)
        self.assertIn(b"filename=\"pic.png\"", raw)
        self.assertEqual(
            posted.requests[0].get_header("Authorization"), "Bot " + TOKEN,
        )

    def test_429_carries_retry_after_and_global(self):
        body = json.dumps({
            "message": "slow", "retry_after": 1.5, "global": True, "code": 0,
        }).encode()
        opener = Recording(429, {"Retry-After": "9"}, body)
        with self.assertRaises(rest.HttpError) as caught:
            rest.call("GET", "/gateway/bot", TOKEN, opener=opener)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 1.5)
        self.assertIs(caught.exception.is_global, True)
        self.assertEqual(caught.exception.code, 0)

        fallback = Recording(429, {"Retry-After": "3"}, b'{"message":"slow"}')
        with self.assertRaises(rest.HttpError) as caught:
            rest.call("GET", "/gateway/bot", TOKEN, opener=fallback)
        self.assertEqual(caught.exception.retry_after, 3.0)
        self.assertIs(caught.exception.is_global, False)
        self.assertIsNone(caught.exception.code)

    def test_redirect_is_refused(self):
        opener = Recording(302, {"Location": "https://evil.example/steal"}, b"")
        with self.assertRaises(ValueError) as caught:
            rest.call("GET", "/gateway/bot", TOKEN, opener=opener)
        self.assertIn("redirect refused", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("evil.example", str(caught.exception))

        cdn = Recording(301, {"Location": "https://evil.example/pic"}, b"png")
        with self.assertRaises(ValueError):
            rest.get_bytes(CDN, 100, opener=cdn)

    def test_cdn_download_sends_no_token_and_rejects_other_hosts(self):
        opener = Recording(200, {}, b"png")
        self.assertEqual(rest.get_bytes(CDN, 100, opener=opener), b"png")
        self.assertIsNone(opener.requests[0].get_header("Authorization"))
        self.assertNotIn(TOKEN, opener.requests[0].full_url)

        media = Recording(200, {}, b"png")
        self.assertEqual(rest.get_bytes(MEDIA, 100, opener=media), b"png")
        self.assertIsNone(media.requests[0].get_header("Authorization"))

        for url in (
            "https://files.example/pic.png",
            "https://cdn.discordapp.com.evil/pic.png",
            "http://cdn.discordapp.com/pic.png",
            "https://user:" + TOKEN + "@cdn.discordapp.com/pic.png",
        ):
            with self.assertRaises(ValueError):
                rest.get_bytes(url, 100, opener=opener)
        self.assertEqual(len(opener.requests), 1)

        huge = Recording(200, {"Content-Length": "50"}, b"x" * 50)
        with self.assertRaises(ValueError):
            rest.get_bytes(CDN, 10, opener=huge)

    def test_errors_never_contain_the_token(self):
        body = json.dumps({"message": TOKEN, "code": 40001}).encode()
        opener = Recording(401, {}, body)
        with self.assertRaises(rest.HttpError) as caught:
            rest.call("GET", "/gateway/bot", TOKEN, opener=opener)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(TOKEN, repr(caught.exception))
        self.assertEqual(caught.exception.code, 40001)

        garbage = Recording(200, {}, TOKEN.encode())
        with self.assertRaises(ValueError) as caught:
            rest.call("GET", "/gateway/bot", TOKEN, opener=garbage)
        self.assertNotIn(TOKEN, str(caught.exception))

        def down(request, timeout):
            raise OSError("discord network")

        with self.assertRaises(OSError):
            rest.call("GET", "/gateway/bot", TOKEN, opener=down)


if __name__ == "__main__":
    unittest.main()
