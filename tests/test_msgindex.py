"""msgindex: shared-write lock so two writers cannot drop an entry."""
import fcntl
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb import msgindex  # noqa: E402


class ConcurrentWriters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_writers_do_not_lose_an_entry(self):
        errors = []

        def write(mid, letter):
            try:
                msgindex.record(self.state, "telegram", "111", mid, letter)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=write, args=("1", "letter-a")),
            threading.Thread(target=write, args=("2", "letter-b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            msgindex.lookup(self.state, "telegram", "111", "1"), "letter-a")
        self.assertEqual(
            msgindex.lookup(self.state, "telegram", "111", "2"), "letter-b")

    def test_record_waits_on_the_lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        lock = self.state / "message-index.lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        started = threading.Event()
        done = threading.Event()

        def write():
            started.set()
            msgindex.record(self.state, "telegram", "111", "1", "letter-a")
            done.set()

        t = threading.Thread(target=write)
        t.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.15)
        self.assertFalse(done.is_set())
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        self.assertTrue(done.wait(2))
        t.join(timeout=2)
        self.assertEqual(
            msgindex.lookup(self.state, "telegram", "111", "1"), "letter-a")

    def test_write_uses_a_unique_temp_not_a_shared_dot_tmp(self):
        seen = []
        orig_open = msgindex.os.open

        def spy_open(path, flags, mode=0o777, *a, **kw):
            seen.append(str(path))
            return orig_open(path, flags, mode, *a, **kw)

        with unittest.mock.patch.object(msgindex.os, "open", spy_open):
            msgindex.record(self.state, "telegram", "111", "9", "letter-z")
        self.assertFalse(
            any(pathlib.Path(p).name == "message-index.json.tmp" for p in seen),
            seen)
        self.assertTrue(
            any(".partial" in pathlib.Path(p).name for p in seen),
            seen)
        leftover = list(self.state.glob("message-index.json.tmp"))
        self.assertEqual(leftover, [])
        self.assertEqual(
            msgindex.lookup(self.state, "telegram", "111", "9"), "letter-z")


if __name__ == "__main__":
    unittest.main()
