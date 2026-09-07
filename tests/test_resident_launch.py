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
    """What the shell does with the string, checked by running it.

    The launcher is `<interpreter> -I -m alb`, so the interpreter is the first
    word and a stand-in for it records how the shell called it. Identity of
    the package is a separate question, below and in test_setup.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.record = self.dir / "record"
        self.addCleanup(self.tmp.cleanup)
        # A different alb, first on PATH: what the old bare-name command found.
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
        """A stand-in interpreter that records the argv it was handed."""
        return _script(pathlib.Path(home) / "venv" / "bin" / "python", "OURS")

    def _command(self, root, home):
        import alb
        return wizard._resident_command(
            root, executable=str(self._ours(home)),
            origin_of=lambda exe: alb.__file__)

    def test_it_reaches_our_interpreter_and_not_the_alb_on_PATH(self):
        result, lines = self._run(self._command(self.dir / "root",
                                                self.dir / "install"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "OURS")

    def test_the_bare_name_reaches_the_competitor(self):
        """The control. Without it, the test above proves only that a script
        ran - not that the fix is what stopped the wrong one running."""
        result, lines = self._run(f"alb --root {self.dir / 'root'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "COMPETITOR")

    def test_a_path_with_spaces_arrives_as_one_argument(self):
        root = self.dir / "My Bridge" / "root"
        result, lines = self._run(self._command(root, self.dir / "My Install"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0], "OURS")
        self.assertIn(str(root), lines)
        self.assertIn(f"{root}/bridge.env", lines)

    def test_the_argv_the_shell_delivers_matches_the_printed_string(self):
        """Consent is to a named thing: the operator sees this string, and the
        shell must deliver exactly those arguments. This checks the ARGV the
        launcher received - what init prints, and what it hands the start
        callback, are checked in tests/test_setup.py."""
        command = self._command(self.dir / "root", self.dir / "install")
        _result, lines = self._run(command)
        self.assertEqual(shlex.split(command)[1:], lines[1:])

    def test_the_isolation_flag_is_in_the_command(self):
        """Named here as well as proved below, because it is the whole
        guarantee: without it the launch honours PYTHONPATH and its own
        directory, and the identity check becomes a statement about a context
        that never happens."""
        command = self._command(self.dir / "root", self.dir / "install")
        self.assertEqual(shlex.split(command)[1], "-I")

    def test_a_different_alb_on_that_interpreter_is_not_ours(self):
        """Importability is not identity. A temporary environment with SOME
        alb answers "yes, importable" - and the command then starts that one."""
        other = self.dir / "other-install" / "alb" / "__init__.py"
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")
        self.assertIsNone(wizard._resident_command(
            self.dir / "root", origin_of=lambda exe: str(other)))

    def test_the_same_origin_is_accepted(self):
        import alb
        command = wizard._resident_command(
            self.dir / "root", origin_of=lambda exe: alb.__file__,
            executable="/venv/bin/python")
        self.assertIsNotNone(command)
        self.assertIn("-m alb", command)

    def test_an_interpreter_with_no_alb_is_declined(self):
        """Proven against a disposable interpreter rather than skipped when
        the developer's own happens to have alb - which is exactly where it
        would have mattered."""
        venv = self.dir / "bare"
        made = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                              capture_output=True, timeout=300)
        if made.returncode != 0:
            self.skipTest("could not create a disposable interpreter")
        interpreter = venv / "bin" / "python"
        probe = subprocess.run(
            [str(interpreter), "-I", "-c", "import alb"], cwd="/",
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True, timeout=60)
        self.assertNotEqual(probe.returncode, 0,
                            "the disposable interpreter already has alb")
        self.assertIsNone(wizard._resident_command(
            self.dir / "root", executable=str(interpreter)))


class TheLaunchIsBoundNotJustTheProbe(unittest.TestCase):
    """Identity was proved under isolation the launch did not apply.

    The check stripped PYTHONPATH and ran from `/`; the generated command
    enforced neither. So a shell whose PYTHONPATH named a different alb — or
    whose working directory sat beside one — launched that one, having been
    approved on evidence from a context that never existed at launch.

    These run the real command through a real shell with exactly those two
    hostile conditions in place. The decoy is inert and prints its own name,
    so "the wrong one ran" is observable rather than inferred.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls.tmp.name)
        made = subprocess.run([sys.executable, "-m", "venv", str(cls.dir / "venv")],
                              capture_output=True, timeout=300)
        if made.returncode != 0:
            raise unittest.SkipTest("could not create an interpreter")
        cls.python = cls.dir / "venv" / "bin" / "python"
        # Install the candidate the cheap way: put THIS source on the
        # interpreter's own path, which is what "installed" means for
        # sys.path purposes and is what the launcher has to reach past the
        # decoys below.
        site = subprocess.run(
            [str(cls.python), "-c",
             "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "alb"
        (pathlib.Path(site) / "alb").symlink_to(source, target_is_directory=True)

        # A decoy alb, reachable only through PYTHONPATH or a cwd.
        cls.decoy = cls.dir / "decoy"
        (cls.decoy / "alb").mkdir(parents=True)
        (cls.decoy / "alb" / "__init__.py").write_text("", encoding="utf-8")
        (cls.decoy / "alb" / "__main__.py").write_text(
            "print('WRONG ALB')\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, command, **env_extra):
        env = dict(os.environ, **env_extra)
        return subprocess.run(["/bin/sh", "-c", command], capture_output=True,
                              text=True, timeout=60, env=env,
                              cwd=env_extra.pop("_cwd", "/"))

    def _command(self):
        import alb
        command = wizard._resident_command(
            self.dir / "root", executable=str(self.python),
            origin_of=lambda exe: alb.__file__)
        self.assertIsNotNone(command, "the candidate should be recognised")
        return command

    def test_a_hostile_PYTHONPATH_does_not_substitute_another_alb(self):
        result = self._run(self._command(), PYTHONPATH=str(self.decoy))
        self.assertNotIn("WRONG ALB", result.stdout + result.stderr)

    def test_a_hostile_working_directory_does_not_either(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(["/bin/sh", "-c", self._command()],
                                capture_output=True, text=True, timeout=60,
                                env=env, cwd=str(self.decoy))
        self.assertNotIn("WRONG ALB", result.stdout + result.stderr)

    def test_the_decoy_is_reachable_without_the_isolation(self):
        """The control. Without it these prove only that a decoy nobody could
        reach was not reached."""
        result = subprocess.run(
            [str(self.python), "-m", "alb"], capture_output=True, text=True,
            timeout=60, env=dict(os.environ, PYTHONPATH=str(self.decoy)),
            cwd="/")
        self.assertIn("WRONG ALB", result.stdout + result.stderr)

    def test_the_probe_agrees_with_the_launch(self):
        """The invariant behind both: what the check asks about is what runs."""
        origin = wizard._origin_for(str(self.python))
        self.assertIsNotNone(origin)
        self.assertTrue(origin.endswith("alb/__init__.py"), origin)


if __name__ == "__main__":
    unittest.main()
