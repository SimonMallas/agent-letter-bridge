"""State is private by default, not private if you remember to make it so.

Letters were already 0600. Everything around them was not: directories were
created world-listable and the state files world-readable, including a canary
log that records the chat ids messages were sent to. A test root only looked
right because its author had run chmod by hand once.
"""
import json
import pathlib
import stat
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from alb.bridge import run  # noqa: E402
from alb.canary import probe  # noqa: E402
from alb.letter import store  # noqa: E402
from fake_platform import FakePlatform, update  # noqa: E402


def mode(path):
    return stat.S_IMODE(pathlib.Path(path).stat().st_mode)


class NothingIsWorldReadable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "alb"
        run.prepare_root(self.root)
        (self.root / "allowlist.json").write_text(
            json.dumps({"chats": ["111"]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_directories_are_not_listable_by_others(self):
        for name in ("", "inbox", "processed", "state"):
            with self.subTest(directory=name or "root"):
                self.assertEqual(mode(self.root / name) & 0o077, 0,
                                 f"{name or 'root'} is readable by group or other")

    def test_state_files_are_not_readable_by_others(self):
        """The canary log records which chats were messaged. The offset and
        health files describe your traffic. None of it is anyone else's."""
        class Sender:
            def send(self, chat_id, text):
                return "ok"

        probe.run(Sender(), self.root)
        run.run_once(FakePlatform([update(1, "111", "hi")]),
                     type("T", (), {"deliver": lambda *a: None})(), "", self.root)

        for path in (self.root / "state").rglob("*"):
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertEqual(mode(path) & 0o077, 0,
                                     f"{path.name} is readable by group or other")

    def test_letters_stay_private_too(self):
        letter_id = store.publish(self.root / "inbox", "body", {"chat_id": "1"})
        self.assertEqual(mode(self.root / "inbox" / f"{letter_id}.md") & 0o077, 0)
