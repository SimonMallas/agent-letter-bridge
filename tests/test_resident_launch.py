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

import alb  # noqa: E402
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
            self.dir / "root", executable=str(ours.parent / "python"),
            origin_of=lambda exe: alb.__file__)
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
            root, executable=str(ours.parent / "python"),
            origin_of=lambda exe: alb.__file__)
        result, lines = self._run(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "OURS")
        self.assertIn(str(root), lines)
        self.assertIn(f"{root}/bridge.env", lines)

    def test_the_argv_the_shell_delivers_matches_the_printed_string(self):
        """Consent is to a named thing: the operator sees this string, and the
        shell must deliver exactly those arguments. This checks the ARGV the
        launcher received - what init prints to the console, and what it hands
        the start callback, are checked in tests/test_setup.py."""
        ours = self._ours(self.dir / "install")
        root = self.dir / "root"
        command = wizard._resident_command(
            root, executable=str(ours.parent / "python"),
            origin_of=lambda exe: alb.__file__)
        _result, lines = self._run(command)
        # lines[0] is the launcher's own marker; the rest is the argv the
        # shell actually delivered. It must equal the printed command's
        # arguments exactly, byte for byte.
        self.assertEqual(shlex.split(command)[1:], lines[1:])
        self.assertEqual(shlex.split(command)[0], str(ours))

    def test_a_different_alb_on_that_interpreter_is_not_ours(self):
        """Importability is not identity.

        A temporary environment with SOME alb installed answers "yes, this
        interpreter can import alb" — and the command then starts that one,
        not the installation running setup. Proven by asking the candidate
        interpreter where its alb actually comes from and comparing it to
        ours, rather than by trusting that any alb is the right alb.
        """
        other = self.dir / "other-install" / "alb" / "__init__.py"
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")
        self.assertIsNone(wizard._resident_command(
            self.dir / "root",
            script_exists=lambda p: False,
            origin_of=lambda exe: str(other)))

    def test_the_same_origin_is_accepted(self):
        import alb
        command = wizard._resident_command(
            self.dir / "root", script_exists=lambda p: False,
            origin_of=lambda exe: alb.__file__, executable="/venv/bin/python")
        self.assertIsNotNone(command)
        self.assertIn("-m alb", command)

    def test_an_adjacent_script_is_not_provenance_either(self):
        """A console script beside the interpreter proves proximity, not that
        it is this codebase. The origin check applies to both branches."""
        self.assertIsNone(wizard._resident_command(
            self.dir / "root",
            script_exists=lambda p: True,
            origin_of=lambda exe: "/somewhere/else/alb/__init__.py"))

    def test_a_source_checkout_is_declined_rather_than_launched(self):
        """`<interpreter> -m alb` cannot work in a fresh shell for a source
        tree: this process can import alb only because of how it was started.

        Proven against a DISPOSABLE interpreter with no alb installed, so it
        is reproducible whether or not the suite's own runner happens to have
        one - it used to skip on a developer machine, which is exactly where
        it would have been most useful.
        """
        venv = self.dir / "bare"
        made = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                              capture_output=True, timeout=180)
        if made.returncode != 0:
            self.skipTest("could not create a disposable interpreter")
        interpreter = venv / "bin" / "python"

        # Establish the premise rather than assume it: this interpreter has
        # no alb of its own.
        probe = subprocess.run(
            [str(interpreter), "-c", "import alb"], cwd="/",
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True, timeout=60)
        self.assertNotEqual(probe.returncode, 0,
                            "the disposable interpreter already has alb")

        self.assertIsNone(wizard._resident_command(
            self.dir / "root", script_exists=lambda p: False,
            executable=str(interpreter)))


if __name__ == "__main__":
    unittest.main()
