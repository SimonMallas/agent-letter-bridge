"""Directories and container metadata must reach the same checks as files."""
import contextlib
import gzip
import io
import pathlib
import stat
import struct
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import check_distribution as gate


class ArchiveMetadata(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.marker = "/" + "Users" + "/" + "fixture-owner" + "/private-canary"

    def tar(self, name="example-1/docs/", kind=tarfile.DIRTYPE, pax=None,
            uname="", gname="", payload=b"", globals_before=None):
        path = self.root / "example.tar.gz"
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for value in globals_before or []:
                record = " comment=" + value + "\n"
                size = len(record.encode()) + 1
                while size != len(str(size)) + len(record.encode()):
                    size = len(str(size)) + len(record.encode())
                data = (str(size) + record).encode()
                header = tarfile.TarInfo("pax-global")
                header.type = tarfile.XGLTYPE
                header.size = len(data)
                archive.addfile(header, io.BytesIO(data))
            ordinary = tarfile.TarInfo("example-1/README.md")
            ordinary.size = 6
            archive.addfile(ordinary, io.BytesIO(b"public"))
            member = tarfile.TarInfo(name)
            member.type = kind
            member.pax_headers = pax or {}
            member.uname, member.gname = uname, gname
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        return path

    def wheel(self, name="alb/docs/", mode=stat.S_IFDIR | 0o755, payload=b"",
              comment=b"", member_comment=b"", extra=b""):
        path = self.root / "example.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("alb/__init__.py", b"")
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            member.external_attr = mode << 16
            member.comment, member.extra = member_comment, extra
            archive.writestr(member, payload)
            archive.comment = comment
        return path

    def check(self, path, expected):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = gate.main([str(path)])
        self.assertEqual(result, expected, output.getvalue())
        self.assertNotIn(self.marker, output.getvalue())

    def test_benign_directories_metadata_and_supported_zip_types(self):
        self.check(self.tar(pax={"comment": "public"}, uname="fixture", gname="fixture"), 0)
        self.check(self.tar(name="example-1/"), 0)
        self.check(self.wheel(comment=b"public", member_comment=b"public",
                              extra=struct.pack("<HH", 0xCAFE, 4) + b"safe"), 0)
        for mode in (0, stat.S_IFREG | 0o600):
            with self.subTest(mode=mode):
                self.check(self.wheel(name="alb/example.py", mode=mode, payload=b"public"), 0)

    def test_tar_directory_name_and_metadata_refusals(self):
        for kwargs in (
            {"name": self.marker}, {"pax": {"comment": self.marker}},
            {"uname": self.marker}, {"gname": self.marker},
            {"name": "example-1/../synthetic"}, {"name": "example-1/.hg"},
            {"name": "example-1/.svn"}, {"name": "example-1/.venv"},
            {"name": "example-1/copied-mail"}, {"name": "example-1/docs//bad/"},
        ):
            with self.subTest(case=tuple(kwargs)):
                self.check(self.tar(**kwargs), 1)

    def test_zip_directory_names_metadata_and_payload_refusals(self):
        for kwargs in (
            {"name": self.marker + "/"}, {"name": "../synthetic/"},
            {"name": ".hg/"}, {"name": ".svn/"}, {"name": "alb/.git/"},
            {"name": "unrelated/"}, {"name": "alb/docs//bad/"},
            {"member_comment": self.marker.encode()},
            {"extra": struct.pack("<HH", 0xCAFE, len(self.marker)) + self.marker.encode()},
            {"payload": b"directories must be empty"},
            {"mode": stat.S_IFREG | 0o600},
        ):
            with self.subTest(case=tuple(kwargs)):
                self.check(self.wheel(**kwargs), 1)

    def test_zip_special_types_are_refused(self):
        for kind in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFLNK):
            with self.subTest(kind=kind):
                self.check(self.wheel(name="alb/synthetic", mode=kind | 0o600, payload=b"public"), 1)

    def test_zip_archive_and_regular_member_comments_and_extras(self):
        for kwargs in (
            {"comment": self.marker.encode()},
            {"member_comment": self.marker.encode()},
            {"extra": struct.pack("<HH", 0xCAFE, len(self.marker)) + self.marker.encode()},
        ):
            with self.subTest(case=tuple(kwargs)):
                self.check(self.wheel(name="alb/example.py", mode=stat.S_IFREG | 0o600,
                                      payload=b"public", **kwargs), 1)

    def test_local_zip_extra_is_scanned_even_if_central_extra_is_clean(self):
        safe = struct.pack("<HH", 0xCAFE, len(self.marker)) + b"x" * len(self.marker)
        private = struct.pack("<HH", 0xCAFE, len(self.marker)) + self.marker.encode()
        path = self.wheel(extra=safe)
        raw = path.read_bytes()
        self.assertEqual(raw.count(safe), 2, "local and central extras must both exist")
        path.write_bytes(raw.replace(safe, private, 1))
        self.check(path, 1)

    def test_overridden_tar_global_metadata_is_still_inspected(self):
        path = self.tar(globals_before=[self.marker, "public"])
        self.assertNotIn(self.marker.encode(), path.read_bytes(), "control must require decompression")
        with tarfile.open(path) as archive:
            self.assertTrue(all(m.pax_headers.get("comment") == "public" for m in archive))
        self.check(path, 1)

    def test_gzip_container_comment_is_inspected(self):
        path = self.tar()
        raw = gzip.compress(gzip.decompress(path.read_bytes()), mtime=0)
        self.assertEqual(raw[3], 0)
        path.write_bytes(raw[:3] + b"\x10" + raw[4:10] + self.marker.encode() + b"\0" + raw[10:])
        self.check(path, 1)

    def test_wheel_membership_refuses_top_level_pth_and_foreign_packages(self):
        for name in ("sitecustomize.pth", "foreign/example.py", "unrelated.dist-info/METADATA"):
            with self.subTest(name=name):
                self.check(self.wheel(name=name, mode=stat.S_IFREG | 0o600, payload=b"public"), 1)

    def test_archive_and_member_count_limits_fail_closed(self):
        path = self.wheel()
        with mock.patch.object(gate, "MAX_ARCHIVE_BYTES", 2):
            self.check(path, 1)
        with mock.patch.object(gate, "MAX_MEMBERS", 1):
            self.check(path, 1)
        path = self.tar()
        with mock.patch.object(gate, "MAX_ARCHIVE_BYTES", 2048):
            self.assertLess(path.stat().st_size, 2048)
            self.check(path, 1)


if __name__ == "__main__":
    unittest.main()
