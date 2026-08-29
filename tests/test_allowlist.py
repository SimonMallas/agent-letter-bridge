"""Allowlist. Fail-closed by default, not by setting.

A correctly-working fail-closed allowlist is indistinguishable from a dead bot.
These tests pin that silence is the deny path succeeding.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from allowlist import gate  # noqa: E402


class FailClosed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "allowlist.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_missing_file_denies_everything(self):
        self.assertFalse(gate.allows(self.path, "12345"))

    def test_empty_allowlist_denies_everything(self):
        """Shipped DENY-ALL. An open default in this tool class is a
        CVE-shaped first issue."""
        self._write({"chats": []})
        self.assertFalse(gate.allows(self.path, "12345"))

    def test_malformed_file_denies_everything(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertFalse(gate.allows(self.path, "12345"))

    def test_wrong_shape_denies_everything(self):
        self._write(["12345"])
        self.assertFalse(gate.allows(self.path, "12345"))

    def test_a_string_chats_value_does_not_match_by_character(self):
        """If `chats` is a string rather than a list, iterating it yields
        characters - so "1" would match "12345". Refuse the wrong shape."""
        self._write({"chats": "12345"})
        self.assertFalse(gate.allows(self.path, "1"))
        self.assertFalse(gate.allows(self.path, "12345"))

    def test_listed_chat_is_allowed(self):
        self._write({"chats": ["12345"]})
        self.assertTrue(gate.allows(self.path, "12345"))

    def test_unlisted_chat_is_denied(self):
        self._write({"chats": ["12345"]})
        self.assertFalse(gate.allows(self.path, "99999"))

    def test_chat_id_type_does_not_bypass_the_gate(self):
        """A numeric id must not slip past a string allowlist or vice versa."""
        self._write({"chats": ["12345"]})
        self.assertTrue(gate.allows(self.path, 12345))
        self.assertFalse(gate.allows(self.path, 99999))


if __name__ == "__main__":
    unittest.main()
