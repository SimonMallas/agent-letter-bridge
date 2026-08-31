"""The offset state machine under failure, proved end to end.

Pi's required behavioural proof. The parts were tested separately - staging,
transmitting, persisting, dedup - and separate tests cannot show that the ORDER
survives a failure in the middle. This walks the whole sequence.

The failure being guarded against is silent loss: a local mark that says
"consumed" while the platform still holds the updates. On the next start that
mark is transmitted, the platform skips messages nobody ever received, and
nothing anywhere reports a problem.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.telegram import api  # noqa: E402
from alb.bridge import run  # noqa: E402


class Platform:
    """A real adapter against a scripted transport, so the state machine under
    test is the shipped one rather than a double of it."""

    def __init__(self, offset_path, updates):
        self.client = api.Telegram("123:FAKE", offset_path=offset_path)
        self.updates = updates
        self.sent_offsets = []

    def _payload(self, params):
        self.sent_offsets.append(params.get("offset"))
        low = params.get("offset", 0)
        return {"ok": True, "result": [
            {"update_id": u, "message": {"chat": {"id": 111}, "text": f"m{u}"}}
            for u in self.updates if u >= low]}


class TheOrderSurvivesFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = run.prepare_root(pathlib.Path(self.tmp.name) / "alb")
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")
        self.offset = self.root / "state" / "offset.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _cycle(self, platform, confirm_fails=False):
        def request(base, method, params, token, timeout=30):
            if confirm_fails and params.get("timeout") == 0:
                raise TimeoutError("confirm failed")
            return platform._payload(params)

        with mock.patch.object(api, "_request", side_effect=request):
            return run.run_once(platform.client,
                                type("T", (), {"deliver": lambda *a: None})(),
                                "", self.root)

    def test_a_failed_confirm_leaves_the_old_offset_and_redelivery_is_safe(self):
        """PI'S SEQUENCE, in order.

        letters durable -> offset staged -> confirm fails -> local offset
        UNCHANGED -> restart re-fetches -> dedup prevents duplicates.
        """
        platform = Platform(self.offset, [7])
        with self.assertRaises(api.TransientFailure):
            self._cycle(platform, confirm_fails=True)

        # 1. the letter is durable
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
        # 2. the mark was NOT persisted: nothing claims consumption
        self.assertFalse(self.offset.exists(),
                         "persisted a mark the platform never accepted")

        # 3. a restart re-fetches the same update
        restarted = Platform(self.offset, [7])
        self._cycle(restarted)
        self.assertIsNone(restarted.sent_offsets[0],
                          "a restart skipped updates it had never confirmed")

        # 4. and does NOT duplicate the letter
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1,
                         "redelivery produced a duplicate letter")
        # 5. only now is the mark persisted
        self.assertTrue(self.offset.exists())
        self.assertEqual(json.loads(self.offset.read_text())["acked"], 7)

    def test_a_successful_cycle_persists_and_the_next_fetch_moves_past(self):
        platform = Platform(self.offset, [7])
        self._cycle(platform)
        self.assertEqual(json.loads(self.offset.read_text())["acked"], 7)

        nxt = Platform(self.offset, [7, 8])
        self._cycle(nxt)
        self.assertEqual(nxt.sent_offsets[0], 8,
                         "did not resume from the confirmed mark")

    def test_a_failed_confirm_does_not_claim_liveness(self):
        """A cycle that did not complete must not leave a fresh heartbeat.

        The heartbeat is what a supervisor reads as 'this process is working'.
        Writing it before the cycle finishes means a bridge that consumed
        nothing looks healthy.
        """
        platform = Platform(self.offset, [7])
        with self.assertRaises(api.TransientFailure):
            self._cycle(platform, confirm_fails=True)
        self.assertFalse((self.root / "state" / "health.json").exists(),
                         "claimed liveness for a cycle that failed to confirm")

    def test_a_completed_cycle_does_claim_liveness(self):
        """The other half. Asserting only that a FAILED cycle writes no
        heartbeat is satisfied by never writing one at all - which would make
        the bridge permanently look dead to a supervisor."""
        platform = Platform(self.offset, [7])
        self._cycle(platform)
        health = self.root / "state" / "health.json"
        self.assertTrue(health.exists(), "a completed cycle left no heartbeat")
        self.assertIn("heartbeat", json.loads(health.read_text()))

    def test_a_quiet_cycle_still_claims_liveness(self):
        """No mail is not the same as no bridge."""
        platform = Platform(self.offset, [])
        self._cycle(platform)
        self.assertTrue((self.root / "state" / "health.json").exists(),
                        "a quiet cycle read as a dead one")

    def test_a_transient_confirm_failure_is_not_a_consumer_conflict(self):
        """Yielding on a network blip hands the token away for no reason."""
        from alb.poller import loop
        platform = Platform(self.offset, [7])
        with self.assertRaises(api.TransientFailure) as caught:
            self._cycle(platform, confirm_fails=True)
        self.assertNotIsInstance(caught.exception, loop.PlatformConflict)
