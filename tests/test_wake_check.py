"""What an agent asks its own relay when it wakes.

Simon's ruling: the agent is the supervisor. That only works if the question
"is my relay dead" has one answer the agent can trust, rather than each seat
hand-rolling a staleness rule - which is how four seats ended up with four
arrangements in the first place.

The hard part is not detecting silence. It is not mistaking a correct silence
for death: a bridge waiting out a rate limit is quiet BECAUSE it is behaving,
and restarting it would be the fix causing the fault.
"""
import json
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.watchdog import health  # noqa: E402


class TheVerdictAnAgentActsOn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "health.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, age=0, state="running", reason=None):
        payload = {"heartbeat": time.time() - age, "state": state}
        if reason:
            payload["reason"] = reason
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_working_bridge_needs_no_action(self):
        self.write(age=5)
        self.assertEqual(health.verdict(self.path).action, "none")

    def test_a_silent_bridge_asks_to_be_restarted(self):
        self.write(age=600)
        v = health.verdict(self.path)
        self.assertEqual(v.action, "restart")
        self.assertEqual(v.state, "dead")

    def test_a_bridge_waiting_out_a_rate_limit_is_left_alone(self):
        """The one that matters. It is quiet because it is behaving, and the
        wait can legitimately run into minutes - so the allowance has to
        follow the state, not one number for everything."""
        self.write(age=200, state="degraded", reason="throttled_429")
        v = health.verdict(self.path)
        self.assertEqual(v.action, "none")
        self.assertEqual(v.state, "degraded")

    def test_a_degraded_bridge_that_never_returns_is_still_dead(self):
        """Patience is not infinite: a backoff that outlives any real retry
        window is a hung process wearing a legitimate reason."""
        self.write(age=7200, state="degraded", reason="throttled_429")
        self.assertEqual(health.verdict(self.path).action, "restart")

    def test_a_starting_bridge_is_not_killed_mid_start(self):
        """It has not completed a poll yet by definition. Restarting here is
        the loop that a fix for restarts would otherwise create."""
        self.write(age=20, state="starting", reason="starting")
        self.assertEqual(health.verdict(self.path).action, "none")

    def test_a_bridge_stuck_starting_is_not_given_forever(self):
        self.write(age=1800, state="starting", reason="starting")
        self.assertEqual(health.verdict(self.path).action, "restart")

    def test_no_health_file_is_reported_not_guessed(self):
        v = health.verdict(self.path)
        self.assertEqual(v.state, "unknown")
        self.assertEqual(v.action, "investigate",
                         "absence is not death - the bridge may never have run here")

    def test_a_corrupt_file_does_not_crash_the_asking_agent(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(health.verdict(self.path).state, "unknown")

    def test_an_older_bridge_without_state_still_gets_a_verdict(self):
        """Seats upgrade at different times; a file written by the previous
        version must not read as corrupt."""
        self.path.write_text(json.dumps({"heartbeat": time.time()}),
                             encoding="utf-8")
        self.assertEqual(health.verdict(self.path).action, "none")


class TheVerdictKnowsWhatItDoesNotKnow(unittest.TestCase):
    """Codex's block. A supervisor that decides needs enough validated state
    to be authoritative, and this one was deciding on four things it had not
    checked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "health.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, **payload):
        payload.setdefault("heartbeat", time.time())
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_a_wait_the_platform_itself_asked_for_is_never_called_dead(self):
        """Telegram may ask for up to an hour, and our own classifier accepts
        it. Declaring death at half that makes the supervisor break the retry
        contract the bridge is correctly keeping."""
        from alb.adapters.telegram import api
        self.write(heartbeat=time.time() - (api.MAX_RETRY_AFTER - 60),
                   state="degraded", reason="throttled_429")
        self.assertEqual(health.verdict(self.path).action, "none")

    def test_a_state_we_do_not_recognise_is_investigated_not_accepted(self):
        """A future version, a typo or a corrupted field is exactly what an
        operator should look at - and silence is the one answer that hides it."""
        self.write(state="new_state")
        v = health.verdict(self.path)
        self.assertEqual(v.action, "investigate")
        self.assertNotEqual(v.action, "restart", "never act on what we cannot read")

    def test_a_degraded_record_with_no_reason_is_investigated(self):
        self.write(state="degraded")
        self.assertEqual(health.verdict(self.path).action, "investigate")

    def test_a_timestamp_from_the_future_is_investigated(self):
        """Clock rollback or a foreign writer. Left alone it reads as fresh
        forever, so the deader it gets the healthier it looks."""
        self.write(heartbeat=time.time() + 3600, state="running")
        self.assertEqual(health.verdict(self.path).action, "investigate")

    def test_small_clock_skew_is_tolerated_rather_than_alarmed_about(self):
        self.write(heartbeat=time.time() + 2, state="running")
        self.assertEqual(health.verdict(self.path).action, "none")

    def test_a_bridge_that_yielded_the_token_is_never_restarted(self):
        """The sharpest one. A 409 means another consumer holds the token and
        the bridge deliberately stood down. Restarting it would make the
        supervisor fight for a token the bridge refused to fight for - undoing
        the yield-never-fight rule from the outside."""
        self.write(heartbeat=time.time() - 99999, state="yielded",
                   reason="conflict")
        v = health.verdict(self.path)
        self.assertEqual(v.action, "investigate")
        self.assertNotEqual(v.action, "restart")
