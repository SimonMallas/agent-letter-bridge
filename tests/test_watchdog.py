"""Watchdog: independent monitoring. Reports; restarts nothing."""
import ast
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.watchdog import health  # noqa: E402


class FreshnessIsLiveness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "health.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, age_seconds):
        self.path.write_text(json.dumps({"heartbeat": health.now() - age_seconds}),
                             encoding="utf-8")

    def test_a_fresh_heartbeat_reads_as_alive(self):
        self._write(5)
        self.assertEqual(health.status(self.path, max_age=60).state, "ok")

    def test_a_stale_heartbeat_reads_as_dead(self):
        """Written after EVERY poll, so freshness equals liveness and a
        supervisor needs no cooperation from the process to judge it."""
        self._write(600)
        self.assertEqual(health.status(self.path, max_age=60).state, "stale")

    def test_a_missing_health_file_reports_rather_than_crashes(self):
        self.assertEqual(health.status(self.path, max_age=60).state, "unknown")

    def test_a_corrupt_health_file_reports_rather_than_crashes(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(health.status(self.path, max_age=60).state, "unknown")

    def test_the_reason_is_always_stated(self):
        """A monitor that reports 'unhealthy' with no reason wastes the 3am."""
        self._write(600)
        self.assertIn("stale", health.status(self.path, max_age=60).reason.lower())


class WatchdogCannotRestartAnything(unittest.TestCase):
    """Supervision belongs to the service manager. Conflating them gives the
    monitor authority over the thing it monitors."""

    def test_the_watchdog_package_has_no_process_control(self):
        forbidden = ("subprocess", "popen", "system", "kill", "execv", "signal")
        for path in (ROOT / "src" / "alb" / "watchdog").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.add((getattr(node, "module", "") or "").lower().split(".")[0])
                    for alias in node.names:
                        names.add(alias.name.lower().split(".")[0])
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr.lower())
            for banned in forbidden:
                self.assertNotIn(banned, names,
                                 f"{path.name} can control processes - it must only report")
