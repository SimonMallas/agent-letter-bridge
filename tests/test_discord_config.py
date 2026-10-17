"""Discord is a platform. The shared socket reads a bare deadline on monotonic."""
import os
import pathlib
import struct
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters import websocket  # noqa: E402
from alb.bridge import run  # noqa: E402

TOKEN = "Bot." + "fixture"


class DiscordConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "bridge.env"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text, mode=0o600):
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, mode)

    def test_discord_platform_is_accepted(self):
        self._write(f"ALB_PLATFORM=discord\nALB_TOKEN={TOKEN}\n")
        config = run.load_config(self.path)
        self.assertEqual(run.platform_of(config), "discord")
        self.assertIn("discord", run.PLATFORMS)

    def test_app_token_on_a_discord_bridge_is_refused(self):
        app = "xapp-" + "fixture"
        self._write(
            f"ALB_PLATFORM=discord\nALB_TOKEN={TOKEN}\nALB_APP_TOKEN={app}\n"
        )
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        message = str(caught.exception)
        self.assertIn("discord", message)
        self.assertIn("ALB_APP_TOKEN", message)
        self.assertNotIn(app, message)
        self.assertNotIn(TOKEN, message)

    def test_telegram_and_slack_messages_unchanged(self):
        self._write("ALB_TOKEN=1:x\nALB_APP_TOKEN=xapp-fixture\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertEqual(
            str(caught.exception),
            "ALB_APP_TOKEN is set on a telegram bridge, where nothing "
            "reads it. Remove it, or set ALB_PLATFORM=slack.",
        )
        self._write("ALB_PLATFORM=slack\nALB_TOKEN=xoxb-fixture\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertEqual(
            str(caught.exception), "missing required settings: ALB_APP_TOKEN",
        )
        bot = "xapp-" + "not-a-bot"
        app = "xapp-" + "app-fixture"
        self._write(f"ALB_PLATFORM=slack\nALB_TOKEN={bot}\nALB_APP_TOKEN={app}\n")
        with self.assertRaises(run.ConfigError) as caught:
            run.load_config(self.path)
        self.assertEqual(
            str(caught.exception),
            "ALB_TOKEN on a slack bridge must start with xoxb-.",
        )
        self.assertNotIn(bot, str(caught.exception))

    def test_slack_or_telegram_shaped_token_is_refused_on_discord(self):
        for token in ("xoxb-" + "fixture", "1:abc"):
            self._write(f"ALB_PLATFORM=discord\nALB_TOKEN={token}\n")
            with self.assertRaises(run.ConfigError) as caught:
                run.load_config(self.path)
            message = str(caught.exception)
            self.assertIn("ALB_TOKEN", message)
            self.assertNotIn(token, message)

    def test_recv_deadline_without_clock_uses_monotonic(self):
        frame = struct.pack("!BB", 0x81, 5) + b"hello"

        class Sock:
            def __init__(self):
                self.buf = frame

            def recv(self, count):
                out, self.buf = self.buf[:count], self.buf[count:]
                return out

            def settimeout(self, seconds):
                self.seconds = seconds

        client = websocket.WebSocket(Sock())
        kind, text = client.recv(time.monotonic() + 30)
        self.assertEqual((kind, text), ("text", "hello"))

    def test_old_slack_websocket_import_still_works(self):
        from alb.adapters.slack import websocket as slack_socket
        self.assertIs(slack_socket.WebSocket, websocket.WebSocket)
        self.assertIs(slack_socket.connect, websocket.connect)
        self.assertEqual(slack_socket.MAX_FRAME, websocket.MAX_FRAME)


if __name__ == "__main__":
    unittest.main()
