"""Black-box tests for the editorial tripwire, in disposable git repos.

The tripwire's one interesting property is WHICH BYTES it reads: the index,
because those are the bytes a commit records. With partial staging, the disk
and the index differ, and a check that reads the disk approves bytes other
than the ones being committed. These tests hold that contract in both
directions, and they run the script as a subprocess with no shared state,
the way the hook does.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "editorial_check.py"

BAD = "The doorbell is optional here.\n"
BAD_PARAPHRASES = [
    "once you treat the ring as optional\n",
    "then an optional generic ring\n",
    "and — optionally — a ring in a pane\n",
]
SAFE = "The bell is how anyone learns the letter exists.\n"


class Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.path, check=True,
                       capture_output=True)

    def write(self, name, text):
        (self.path / name).write_text(text, encoding="utf-8")

    def check(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=self.path, capture_output=True, text=True)


class TheTripwireReadsTheBytesACommitWouldRecord(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.tmp.cleanup)

    def test_a_staged_banned_phrase_is_caught(self):
        self.repo.write("doc.md", BAD)
        self.repo.git("add", "doc.md")
        result = self.repo.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("doc.md", result.stderr)

    def test_partial_staging_cannot_hide_the_staged_bytes(self):
        """Bad text staged, safe text left on disk: the commit takes the
        staged bytes, so the check must too."""
        self.repo.write("doc.md", BAD)
        self.repo.git("add", "doc.md")
        self.repo.write("doc.md", SAFE)          # disk is clean; index is not
        result = self.repo.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_a_dirty_worktree_over_a_clean_index_passes_the_commit_check(self):
        """The mirror case: the unstaged bytes are not being committed."""
        self.repo.write("doc.md", SAFE)
        self.repo.git("add", "doc.md")
        self.repo.write("doc.md", BAD)           # disk is bad; index is clean
        result = self.repo.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_worktree_mode_reads_the_disk_for_ci(self):
        self.repo.write("doc.md", BAD)
        self.repo.git("add", "doc.md")
        self.repo.write("doc.md", SAFE)
        result = self.repo.check("--worktree")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_observed_paraphrases_are_caught(self):
        """The shapes that actually survived the first pattern set, found live
        in this repository's own docs."""
        for text in BAD_PARAPHRASES:
            with self.subTest(text=text.strip()):
                self.repo.write("doc.md", text)
                self.repo.git("add", "doc.md")
                result = self.repo.check()
                self.assertEqual(result.returncode, 1, text)

    def test_a_novel_paraphrase_is_documented_as_not_caught(self):
        """Honesty pin, not a wish: the tripwire is syntactic. If this test
        ever fails, the check has grown semantic claims it cannot keep, and
        the output wording must be revisited before celebrating."""
        self.repo.write("doc.md",
                        "You can disable notifications and rely on the "
                        "durable inbox.\n")
        self.repo.git("add", "doc.md")
        result = self.repo.check()
        self.assertEqual(result.returncode, 0)
        self.assertIn("semantic review stays human", result.stdout)

    def test_a_hyphen_fused_number_is_not_a_total(self):
        self.repo.write("doc.md", "Run the Day-0 test before anything.\n")
        self.repo.git("add", "doc.md")
        self.assertEqual(self.repo.check().returncode, 0)

    def test_a_quoted_filename_is_still_scanned(self):
        """git quotes names containing a double quote (and non-ASCII, by
        default) in porcelain output; the quoted form no longer ends in .md
        and silently leaves the scan. Enumeration must be NUL-separated."""
        name = 'notes "draft".md'
        self.repo.write(name, BAD)
        self.repo.git("add", name)
        result = self.repo.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_a_non_ascii_filename_is_still_scanned(self):
        name = "notiz-übergabe.md"
        self.repo.write(name, BAD)
        self.repo.git("add", name)
        self.assertEqual(self.repo.check().returncode, 1)

    def test_a_numeric_total_is(self):
        self.repo.write("doc.md", "The release is covered by 214 tests.\n")
        self.repo.git("add", "doc.md")
        self.assertEqual(self.repo.check().returncode, 1)


class FailureToInspectIsNotAPass(unittest.TestCase):
    def test_a_missing_commit_message_file_exits_two(self):
        repo = Repo()
        result = repo.check("no-such-file")
        repo.tmp.cleanup()
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_outside_a_git_repo_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)


class CommitMessagesAreScanned(unittest.TestCase):
    def test_a_banned_phrase_in_a_message_is_refused(self):
        repo = Repo()
        # An observed phrase, deliberately: the tripwire is not a classifier,
        # and a test that requires it to catch invented wordings would push
        # the patterns toward one.
        repo.write("msg", "docs: note that the doorbell is optional\n")
        result = repo.check("msg")
        repo.tmp.cleanup()
        self.assertEqual(result.returncode, 1)

    def test_a_clean_message_passes(self):
        repo = Repo()
        repo.write("msg", "docs: tidy the install steps\n")
        result = repo.check("msg")
        repo.tmp.cleanup()
        self.assertEqual(result.returncode, 0)
