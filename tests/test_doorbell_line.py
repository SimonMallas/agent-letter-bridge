"""Dual-capable receiver permitted-lines for the doorbell line."""
from __future__ import annotations

import unittest
from pathlib import Path

from alb.doorbell_line import is_permitted_doorbell


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "vendor" / "doorbell-outcome-v1"


def _rows(name: str):
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        yield line.split("\t")


class VendoredDoorbellLines(unittest.TestCase):
    def test_accepted_shapes_are_permitted(self):
        count = 0
        for line, shape, _note in _rows("doorbell-line-accepted.tsv"):
            count += 1
            self.assertTrue(is_permitted_doorbell(line), f"{shape}: {line}")
        self.assertEqual(count, 5)

    def test_rejected_shapes_are_excluded(self):
        count = 0
        for line, _why in _rows("doorbell-line-rejected.tsv"):
            count += 1
            self.assertFalse(is_permitted_doorbell(line), line)
        self.assertEqual(count, 9)


class DualCapableLiveShapes(unittest.TestCase):
    def test_old_bus_path_with_token(self):
        self.assertTrue(is_permitted_doorbell(
            "📬 bus doorbell: unacked delegate in inbox/ — please check · deadbeef"))

    def test_from_sender_bus_path(self):
        self.assertTrue(is_permitted_doorbell(
            "📬 bus doorbell: unacked info from agent in inbox/ — please check"))

    def test_outcome_line_is_excluded(self):
        self.assertFalse(is_permitted_doorbell(
            "doorbell-outcome v=1 outcome=submitted reason=- target=surface:1"))

    def test_standalone_bridge_mail_line_is_excluded(self):
        self.assertFalse(is_permitted_doorbell(
            "you have new mail: check the bridge inbox"))


if __name__ == "__main__":
    unittest.main()
