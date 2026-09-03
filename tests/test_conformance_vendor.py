import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConformanceVendorTests(unittest.TestCase):
    def test_vendored_snapshot_matches_reviewed_manifest(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check_conformance_vendor.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conformance vendor: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
