"""Herdr notifier adapter: submits a fixed line to one identified agent pane.

Held to the same contract as the cmux and tmux adapters, plus the two things
only this transport does: one call rather than two, and Herdr's own refusal
code carried into ring health.
"""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_platform import FakePlatform  # noqa: E402
from alb import cli  # noqa: E402
from alb.adapters.herdr import transport  # noqa: E402
from alb.adapters.telegram import api  # noqa: E402
from alb.bridge import run  # noqa: E402
from alb.notifier import ring  # noqa: E402


def refused(code):
    stderr = json.dumps({"error": {"code": code, "message": "x"}}).encode()
    return subprocess.CalledProcessError(1, ["herdr"], b"", stderr)


class Delivery(unittest.TestCase):
    def setUp(self):
        self.t = transport.Herdr()

    def test_it_prompts_the_pane_explicitly(self):
        """Ringing whatever pane is focused is how the wrong agent gets woken."""
        with mock.patch.object(transport, "_run") as run_:
            self.t.deliver("w1:p1", "you have mail")
        self.assertEqual(run_.call_args[0][0],
                         ["herdr", "agent", "prompt", "w1:p1", "you have mail"])

    def test_it_is_one_call_so_there_is_no_half_fired_ring(self):
        """agent prompt pastes and presses Enter as one ordered submission."""
        with mock.patch.object(transport, "_run") as run_:
            self.t.deliver("w1:p1", "you have mail")
        self.assertEqual(run_.call_count, 1)

    def test_it_refuses_an_empty_pane_id(self):
        with mock.patch.object(transport, "_run") as run_:
            with self.assertRaises(ring.NoTargetSurface):
                self.t.deliver("", "you have mail")
        run_.assert_not_called()

    def test_it_refuses_multi_line_payloads(self):
        """A newline would submit early and make the remainder a second,
        unreviewed instruction."""
        with mock.patch.object(transport, "_run") as run_:
            with self.assertRaises(ValueError):
                self.t.deliver("w1:p1", "you have mail\rrm -rf /")
        run_.assert_not_called()

    def test_the_payload_is_passed_as_an_argument_never_a_shell_string(self):
        with mock.patch.object(transport, "_run") as run_:
            self.t.deliver("w1:p1", "you have mail")
        self.assertIsInstance(run_.call_args[0][0], list)


class ServerPinning(unittest.TestCase):
    def test_a_pinned_socket_selects_the_server(self):
        with mock.patch.object(transport, "_run") as run_:
            transport.Herdr(socket="/s/herdr.sock").deliver("w1:p1", "x")
        env = run_.call_args[1]["env"]
        self.assertEqual(env["HERDR_SOCKET_PATH"], "/s/herdr.sock")
        self.assertIn("PATH", env, "pinning must not drop the rest of the environment")

    def test_no_socket_leaves_herdr_to_resolve_its_own(self):
        with mock.patch.object(transport, "_run") as run_:
            transport.Herdr().deliver("w1:p1", "x")
        self.assertIsNone(run_.call_args[1]["env"])


class Refusals(unittest.TestCase):
    """Herdr refuses before writing anything. The code is the reason."""

    def test_a_blocked_agent_is_named_not_counted_as_a_failure_code(self):
        with mock.patch.object(transport, "_run", side_effect=refused("agent_blocked")):
            with self.assertRaises(transport.HerdrRefused) as caught:
                transport.Herdr().deliver("w1:p1", "x")
        self.assertEqual(str(caught.exception), "agent_blocked")

    def test_ring_health_carries_the_herdr_code(self):
        with mock.patch.object(transport, "_run", side_effect=refused("agent_not_found")):
            try:
                transport.Herdr().deliver("w1:p1", "x")
            except Exception as exc:  # noqa: BLE001
                reason = run._ring_failure_reason(exc)
        self.assertIn("agent_not_found", reason)

    def test_an_unparseable_failure_is_not_invented_into_a_code(self):
        bare = subprocess.CalledProcessError(1, ["herdr"], b"", b"segfault")
        with mock.patch.object(transport, "_run", side_effect=bare):
            with self.assertRaises(subprocess.CalledProcessError):
                transport.Herdr().deliver("w1:p1", "x")

    def test_a_timeout_is_not_retried_and_stays_unconfirmed(self):
        calls = []

        def timing_out(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

        with mock.patch.object(subprocess, "run", timing_out):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                transport.Herdr().deliver("w1:p1", "x")
        self.assertEqual(len(calls), 1)
        self.assertEqual(run._ring_failure_reason(caught.exception), "unconfirmed")


class SelectionIsHonoured(unittest.TestCase):
    """Through cli.main. A dogfood install once selected a notifier that
    nothing read; accepting the key is not the same as using it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = run.prepare_root(base / "alb")
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.env = base / "bridge.env"
        self.env.write_text("ALB_TOKEN=1:x\nALB_NOTIFIER=herdr\n"
                            "ALB_SURFACE=w1:p1\nALB_HERDR_SOCKET=/s/herdr.sock\n",
                            encoding="utf-8")
        os.chmod(self.env, 0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_herdr_config_rings_through_herdr(self):
        updates = [{"update_id": 1, "chat_id": "111", "text": "hello"}]
        with mock.patch.object(api, "Telegram", lambda *a, **k: FakePlatform(updates)), \
                mock.patch.object(transport, "_run") as run_, \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["--config", str(self.env), "--root", str(self.root),
                             "--once"])
        self.assertEqual(code, 0)
        self.assertEqual(run_.call_count, 1)
        self.assertEqual(run_.call_args[0][0],
                         ["herdr", "agent", "prompt", "w1:p1", ring.DOORBELL_LINE])
        self.assertEqual(run_.call_args[1]["env"]["HERDR_SOCKET_PATH"], "/s/herdr.sock")


if __name__ == "__main__":
    unittest.main()
