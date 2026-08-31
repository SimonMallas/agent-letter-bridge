"""The dependency gate is a security control, so it is tested like one.

Grok's requirement: emptying project.dependencies must not be the only thing
that can fail. Disabling the ASSERTION must go red too, or the gate can be
removed silently and everything stays green.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "deps_check.py"


def run_gate(cwd):
    """Run the COPY inside cwd, not the original.

    The gate resolves its root from its own __file__, so invoking the real
    script with a different cwd checks the real repo and the fixture is
    ignored - which made every negative case pass for the wrong reason.
    """
    script = pathlib.Path(cwd) / "scripts" / "deps_check.py"
    return subprocess.run([sys.executable, str(script)], cwd=cwd,
                          capture_output=True, text=True)


class DependencyGate(unittest.TestCase):
    def test_the_real_repo_passes(self):
        self.assertEqual(run_gate(ROOT).returncode, 0)

    def test_a_runtime_dependency_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "deps_check.py").write_text(
                GATE.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "x"\ndependencies = ["requests"]\n', encoding="utf-8")
            result = run_gate(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("not empty", result.stdout)

    def test_a_build_backend_is_not_mistaken_for_a_runtime_dependency(self):
        """Install-time tooling is not imported by the daemon. Banning it would
        be the wrong lesson and would push someone to ban pyproject again."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "deps_check.py").write_text(
                GATE.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[build-system]\nrequires = ["hatchling"]\n'
                '[project]\nname = "x"\ndependencies = []\n', encoding="utf-8")
            self.assertEqual(run_gate(root).returncode, 0)

    def test_a_missing_pyproject_is_refused(self):
        """The package must be installable. An earlier gate demanded the
        opposite; this pins the correction."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "deps_check.py").write_text(
                GATE.read_text(encoding="utf-8"), encoding="utf-8")
            result = run_gate(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing", result.stdout)

    def test_a_smuggled_manifest_is_refused(self):
        for manifest in ("requirements.txt", "setup.py", "uv.lock"):
            with self.subTest(manifest=manifest):
                with tempfile.TemporaryDirectory() as tmp:
                    root = pathlib.Path(tmp)
                    (root / "scripts").mkdir()
                    (root / "scripts" / "deps_check.py").write_text(
                        GATE.read_text(encoding="utf-8"), encoding="utf-8")
                    (root / "pyproject.toml").write_text(
                        '[project]\nname = "x"\ndependencies = []\n', encoding="utf-8")
                    (root / manifest).write_text("requests\n", encoding="utf-8")
                    self.assertEqual(run_gate(root).returncode, 1)
