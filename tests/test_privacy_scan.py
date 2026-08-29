"""The privacy scan is a security control, so it gets tested like one.

A scanner that cannot fail is worse than no scanner: it produces confidence.
Each case below plants a violation and asserts the scan rejects it.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "privacy_scan.py"

# Assembled at runtime so no literal violation exists in this file. The scanner
# has NO exemptions by design — an exempted file is a place to hide a real leak.
VIOLATIONS = {
    "macos_home": 'p = "' + "/Users" + '/someone/secret"',
    "linux_home": 'p = "' + "/home" + '/someone/secret"',
    "volume": 'p = "' + "/Volumes" + '/SomeDisk/state"',
    "launchd_domain": 'c = "launchctl print ' + "gui/" + '501/com.example.job"',
    "bot_token": 'TOKEN = "1234567890' + ":" + 'AAFakeValueForTestingOnly123456789"',
    "assistant_trailer": "Co-Authored" + "-By: Claude <noreply@example.com>",
    "session_trailer": "Claude-" + "Session: https://example.com/s",
    "uppercase_uuid": 'sid = "AAAAAAAA-BBBB-CCCC' + "-DDDD-EEEEEEEEEEEE" + '"',
}

TRAILER_MSG = "feat: a change\n\n" + "Co-Authored" + "-By: Claude <x@example.com>\n"


def run_scan(cwd, arg=None):
    cmd = [sys.executable, str(SCAN)] + ([arg] if arg else [])
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


class PrivacyScanTests(unittest.TestCase):
    def test_clean_tree_passes(self):
        """The real repository must pass, or the gate is theatre."""
        self.assertEqual(run_scan(ROOT).returncode, 0)

    def test_each_violation_is_caught(self):
        for name, content in VIOLATIONS.items():
            with self.subTest(violation=name):
                with tempfile.TemporaryDirectory() as tmp:
                    (pathlib.Path(tmp) / "planted.py").write_text(content, encoding="utf-8")
                    result = run_scan(tmp)
                    self.assertEqual(
                        result.returncode, 1, f"{name} was not caught:\n{result.stdout}"
                    )

    def test_commit_message_trailer_is_caught(self):
        """Trailers must be blocked in the message, not only in the tree."""
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(TRAILER_MSG)
            msg_path = fh.name
        self.assertEqual(run_scan(ROOT, msg_path).returncode, 1)

    def test_scanner_scans_itself(self):
        """NO EXEMPTIONS means the scanner is subject to its own rules.

        Regression: an earlier version skipped its own file because a pattern
        matched its own regex literal, while the docs and commit message claimed
        no exemptions existed. A planted violation inside the scanner sailed
        through. Patterns are now assembled from fragments so no skip is needed.
        """
        planted = pathlib.Path(tempfile.mkdtemp()) / "privacy_scan.py"
        planted.write_text(
            (SCAN.read_text(encoding="utf-8") + "\n" + VIOLATIONS["bot_token"] + "\n"),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(SCAN)], cwd=planted.parent,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, "scanner did not scan its own filename")

    def test_findings_never_echo_the_match(self):
        """A caught secret must not be copied into CI logs, a second store."""
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "planted.py").write_text(
                VIOLATIONS["bot_token"], encoding="utf-8"
            )
            result = run_scan(tmp)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("AAFakeValue", result.stdout)
            self.assertIn("bot-token-shaped", result.stdout)

    def test_scan_does_not_pass_vacuously(self):
        """An empty directory has nothing to scan; it must not report success
        on a tree it never read. Regression: git ls-files is empty in a fresh
        repo, which made the first version of this scanner pass on zero files."""
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "planted.py").write_text(
                VIOLATIONS["bot_token"], encoding="utf-8"
            )
            self.assertEqual(run_scan(tmp).returncode, 1)


if __name__ == "__main__":
    unittest.main()
