"""The one start test that has to cross a real shell.

Everything else in this suite calls the CLI directly, which is correct and
fast — and is exactly why the launch defect survived a full audit. The
command `init` produces is handed to a SHELL, and the shell is where the
executable is resolved, the arguments are split, and a path with a space
falls apart. None of that is observable from in-process.

So this file executes the generated command through `/bin/sh`, with a
competing `alb` first on PATH, and asks what actually ran. It is deliberately
one file and one mechanism, not a framework: the claim being defended is
narrow — that the command starts THIS installation, with THESE arguments.
"""
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from alb.setup import wizard  # noqa: E402


def _script(path, marker):
    """A stand-in launcher that records how the shell called it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "{marker}" > "$RECORD"\n'
        'for arg in "$@"; do printf "%s\\n" "$arg" >> "$RECORD"; done\n',
        encoding="utf-8")
    path.chmod(0o755)
    return path


class TheGeneratedCommandRunsTHISInstallation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.record = self.dir / "record"
        self.addCleanup(self.tmp.cleanup)
        # A different alb, first on PATH. This is the one the old bare-name
        # command found, and the one that must never be reached again.
        self.competitor = _script(self.dir / "elsewhere" / "bin" / "alb",
                                  "COMPETITOR")

    def _run(self, command):
        env = dict(os.environ,
                   PATH=f"{self.competitor.parent}:{os.environ.get('PATH','')}",
                   RECORD=str(self.record))
        result = subprocess.run(["/bin/sh", "-c", command],
                                capture_output=True, text=True, timeout=30,
                                env=env, cwd="/")
        lines = (self.record.read_text(encoding="utf-8").splitlines()
                 if self.record.exists() else [])
        return result, lines

    def _ours(self, home):
        return _script(pathlib.Path(home) / "venv" / "bin" / "alb", "OURS")

    def test_it_reaches_our_launcher_and_not_the_one_on_PATH(self):
        ours = self._ours(self.dir / "install")
        command = wizard._resident_command(
            self.dir / "root", executable=str(ours.parent / "python"))
        result, lines = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "OURS")

    def test_the_bare_name_reaches_the_competitor(self):
        """The control. Without it, the test above proves only that a script
        ran — not that the fix is what stopped the wrong one running."""
        result, lines = self._run(f"alb --root {self.dir / 'root'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "COMPETITOR")

    def test_a_path_with_spaces_arrives_as_one_argument(self):
        home = self.dir / "My Install"
        root = self.dir / "My Bridge" / "root"
        ours = self._ours(home)
        command = wizard._resident_command(
            root, executable=str(ours.parent / "python"))
        result, lines = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "OURS")
        self.assertIn(str(root), lines)
        self.assertIn(f"{root}/bridge.env", lines)

    def test_what_is_printed_is_what_runs(self):
        """Consent is to a named thing. The operator sees this string; the
        shell must do exactly it."""
        ours = self._ours(self.dir / "install")
        root = self.dir / "root"
        command = wizard._resident_command(
            root, executable=str(ours.parent / "python"))
        _result, lines = self._run(command)
        # lines[0] is the launcher's own marker; the rest is the argv the
        # shell actually delivered. It must equal the printed command's
        # arguments exactly, byte for byte.
        self.assertEqual(shlex.split(command)[1:], lines[1:])
        self.assertEqual(shlex.split(command)[0], str(ours))

    def test_a_source_checkout_is_declined_rather_than_launched(self):
        """`<interpreter> -m alb` cannot work in a fresh shell for a source
        tree: this process can import alb only because of how it was started.
        Proven by running it, not by assuming."""
        probe = subprocess.run(
            [sys.executable, "-c", "import alb"],
            capture_output=True, text=True, timeout=30,
            env={"PATH": os.environ.get("PATH", "")}, cwd="/")
        if probe.returncode == 0:
            self.skipTest("alb is installed for this interpreter; "
                          "the source-checkout shape is not reproducible here")
        self.assertIsNone(wizard._resident_command(
            self.dir / "root", script_exists=lambda p: False))


if __name__ == "__main__":
    unittest.main()
