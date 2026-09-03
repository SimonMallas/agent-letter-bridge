import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConformanceVendorTests(unittest.TestCase):
    def run_checker(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/check_conformance_vendor.py"],
            cwd=root,
            text=True,
            capture_output=True,
        )

    def isolated_vendor(self, directory: str) -> Path:
        root = Path(directory)
        (root / "scripts").mkdir()
        shutil.copy2(
            ROOT / "scripts/check_conformance_vendor.py", root / "scripts"
        )
        shutil.copytree(ROOT / "vendor", root / "vendor")
        return root

    def test_vendored_snapshot_matches_reviewed_manifest(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check_conformance_vendor.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conformance vendor: PASS", result.stdout)

    def test_vendored_snapshot_rejects_drift(self) -> None:
        cases = ("digest", "inventory", "unsafe-path")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = self.isolated_vendor(tmp)
                snapshot = root / "vendor/letterbox-conformance-v1"
                expected = ""
                if case == "digest":
                    with (snapshot / "accepted/basic.md").open("ab") as fixture:
                        fixture.write(b"drift\n")
                    expected = "conformance fixture mismatch"
                elif case == "inventory":
                    (snapshot / "extra.md").write_text("extra\n", encoding="utf-8")
                    expected = "inventory mismatch"
                else:
                    manifest = snapshot / "SHA256SUMS"
                    lines = manifest.read_text(encoding="utf-8").splitlines()
                    digest, _ = lines[0].split("  ", 1)
                    lines[0] = f"{digest}  ../README.md"
                    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    source_path = root / "vendor/letterbox-conformance-source.json"
                    source = json.loads(source_path.read_text(encoding="utf-8"))
                    source["manifest_sha256"] = hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest()
                    source_path.write_text(
                        json.dumps(source, indent=2) + "\n", encoding="utf-8"
                    )
                    expected = "unsafe conformance manifest path"
                result = self.run_checker(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
