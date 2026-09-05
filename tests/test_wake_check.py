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
