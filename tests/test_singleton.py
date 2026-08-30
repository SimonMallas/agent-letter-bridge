"""One bridge per state directory. A 409 is a backstop, not a lock."""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bridge import singleton  # noqa: E402


class OnlyOne(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_second_bridge_on_the_same_root_refuses(self):
        """Two processes on one token race until the platform returns a
        conflict. Refusing locally is cheaper, earlier, and does not depend on
        the platform noticing."""
        with singleton.hold(self.root):
            with self.assertRaises(singleton.AlreadyRunning):
                with singleton.hold(self.root):
                    pass

    def test_the_lock_is_released_when_the_holder_exits(self):
        with singleton.hold(self.root):
            pass
        with singleton.hold(self.root):
            pass  # must not raise

    def test_the_lock_is_released_even_if_the_holder_raises(self):
        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with singleton.hold(self.root):
                raise Boom()
        with singleton.hold(self.root):
            pass

    def test_a_different_root_is_a_different_lock(self):
        other = self.root / "other"
        other.mkdir()
        with singleton.hold(self.root):
            with singleton.hold(other):
                pass  # unrelated bridges must not block each other
