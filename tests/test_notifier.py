"""Notifier: rings only after a unique letter exists, and carries no content."""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from letter import store  # noqa: E402
from notifier import ring  # noqa: E402


class FakeTransport:
    def __init__(self, surface="SURFACE-1"):
        self.surface = surface
        self.rung = []

    def deliver(self, surface, line):
        self.rung.append((surface, line))


class RingDiscipline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_ring_carries_no_letter_content(self):
        secret = "my bank pin is 4321"
        letter_id = store.publish(self.inbox, secret, {"chat_id": "111"})
        transport = FakeTransport()
        ring.notify(transport, "SURFACE-1", self.inbox, letter_id)
        _, line = transport.rung[0]
        self.assertNotIn(secret, line)
        self.assertNotIn("4321", line)

    def test_the_ring_carries_no_identifier_either(self):
        """The letter id is a capability. The ring says only 'you have mail'."""
        letter_id = store.publish(self.inbox, "body", {"chat_id": "111"})
        transport = FakeTransport()
        ring.notify(transport, "SURFACE-1", self.inbox, letter_id)
        self.assertNotIn(letter_id, transport.rung[0][1])

    def test_it_refuses_to_ring_without_a_letter_on_disk(self):
        """Rings accelerate; they never announce something that does not exist."""
        transport = FakeTransport()
        with self.assertRaises(store.NoSuchLetter):
            ring.notify(transport, "SURFACE-1", self.inbox, "20260101T000000-deadbeef")
        self.assertEqual(transport.rung, [])

    def test_it_refuses_a_path_shaped_identifier(self):
        transport = FakeTransport()
        with self.assertRaises(store.UnsafeIdentifier):
            ring.notify(transport, "SURFACE-1", self.inbox, "../escape")
        self.assertEqual(transport.rung, [])

    def test_it_refuses_without_a_target_surface(self):
        """Identity uncertainty fails closed. No surface, no ring - never a
        guess at which pane looks right."""
        letter_id = store.publish(self.inbox, "body", {"chat_id": "111"})
        transport = FakeTransport()
        with self.assertRaises(ring.NoTargetSurface):
            ring.notify(transport, None, self.inbox, letter_id)
        self.assertEqual(transport.rung, [])
