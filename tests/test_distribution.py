"""Actual archive inspection: clean controls and planted privacy failures."""
import contextlib
import io
import pathlib
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import check_distribution as gate


class DistributionPrivacy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def sdist(self, files):
        path = self.root / "example.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, payload in files.items():
                member = tarfile.TarInfo("example-1/" + name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        return path

    def wheel(self, files):
        path = self.root / "example.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        return path

    def test_clean_source_and_wheel_controls_pass(self):
        source = self.sdist({"src/alb/__init__.py": b"", "README.md": b"public",
                             "INSTALL.md": b"install", "PKG-INFO": b"Version: 1"})
        wheel = self.wheel({"alb/__init__.py": b"", "example.dist-info/METADATA": b"Version: 1"})
        self.assertEqual(gate.check_artifact(source), [])
        self.assertEqual(gate.check_artifact(wheel), [])

    def test_worktree_pointer_is_refused_in_both_formats(self):
        source = self.sdist({".git": b"gitdir: synthetic-pointer"})
        wheel = self.wheel({".git": b"gitdir: synthetic-pointer"})
        for path in (source, wheel):
            self.assertTrue(gate.check_artifact(path))

    def test_arbitrary_untracked_roots_are_not_shippable(self):
        for name in (".venv-review/bin/python", "copied-mail/letter.md", "review.log"):
            with self.subTest(name=name):
                self.assertIn("member outside sdist allow-list",
                              str(gate.check_artifact(self.sdist({name: b"synthetic"}))))

    def test_private_payload_in_an_allowed_path_is_refused_without_echo(self):
        private = "/" + "Users" + "/" + "fixture-owner" + "/" + "private"
        source = self.sdist({"docs/example.md": private.encode()})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(gate.main([str(source)]), 1)
        self.assertNotIn(private, output.getvalue())
        self.assertIn("absolute macOS home path", output.getvalue())

    def test_unsafe_names_and_links_are_refused_without_extraction(self):
        self.assertTrue(gate.check_artifact(self.wheel({"../outside": b"synthetic"})))
        path = self.root / "linked.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("example-1/src/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        self.assertTrue(gate.check_artifact(path))
        self.assertFalse((self.root / "outside").exists())

    def test_oversize_member_is_incomplete_not_clean(self):
        from unittest import mock
        path = self.sdist({"docs/example.md": b"synthetic"})
        with mock.patch.object(gate, "MAX_MEMBER_BYTES", 2):
            self.assertTrue(gate.check_artifact(path))

    def test_empty_and_invalid_archives_are_not_clean(self):
        self.assertTrue(gate.check_artifact(self.wheel({})))
        invalid = self.root / "invalid.tar.gz"
        invalid.write_bytes(b"not an archive")
        self.assertTrue(gate.check_artifact(invalid))


if __name__ == "__main__":
    unittest.main()
