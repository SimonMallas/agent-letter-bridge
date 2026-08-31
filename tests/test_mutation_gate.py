"""The mutation gate is the thing that proves the other tests can fail, so it
gets its own proof that IT still works.

A stale bytecode cache once let a mutation appear applied while the running
code was unchanged - the gate printed ok for a pin it had never exercised, and
separately left the tree behaving as mutated after the run. Neither is visible
from the gate's own output, which is why this is a test and not a comment.
"""
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "mutation_check.py"


class GateStillBites(unittest.TestCase):
    def test_the_gate_passes_on_a_clean_tree(self):
        result = subprocess.run([sys.executable, str(GATE)], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertIn("invariants pinned", result.stdout)

    def test_the_gate_leaves_the_tree_unmutated(self):
        """After a run, importing the code must give the ORIGINAL behaviour.

        This is the regression: a restore that left cached bytecode behind
        meant the source was right and the running code was not.
        """
        subprocess.run([sys.executable, str(GATE)], cwd=ROOT,
                       capture_output=True, text=True)
        probe = subprocess.run(
            [sys.executable, "-c",
             "from alb.send import reply; print(reply.DESTINATION_KEYS[0])"],
            cwd=ROOT, capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(probe.stdout.strip(), "telegram_chat_id",
                         "the gate left mutated behaviour behind")
