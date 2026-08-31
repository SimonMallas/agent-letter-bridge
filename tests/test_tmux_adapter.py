"""tmux notifier adapter: delivers a fixed line to one identified pane.

The adapter is contributed; these tests mirror the cmux adapter's, so both
transports are held to the same contract rather than each to its own.
"""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.tmux import transport  # noqa: E402
from alb.notifier import ring  # noqa: E402


class Delivery(unittest.TestCase):
    def setUp(self):
        self.t = transport.Tmux()

    def test_it_addresses_the_pane_explicitly(self):
        """Ringing whatever pane is active is how the wrong agent gets woken."""
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("%1", "you have mail")
        sent = run.call_args_list[0][0][0]
        self.assertIn("-t", sent)
        self.assertIn("%1", sent)

    def test_the_payload_is_sent_literally_not_as_a_key_name(self):
        """Without -l, tmux interprets the text as key names, so a payload
        containing a word like Enter would be pressed rather than typed."""
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("%1", "you have mail")
        self.assertIn("-l", run.call_args_list[0][0][0])

    def test_the_line_and_the_return_are_separate_calls(self):
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("%1", "you have mail")
        self.assertEqual(run.call_count, 2)
        self.assertIn("Enter", run.call_args_list[1][0][0])

    def test_it_refuses_an_empty_pane_id(self):
        with mock.patch.object(transport, "_run") as run:
            with self.assertRaises(ring.NoTargetSurface):
                self.t.deliver("", "you have mail")
        run.assert_not_called()

    def test_it_refuses_multi_line_payloads(self):
        """A newline would submit early and make the remainder a second,
        unreviewed instruction."""
        with mock.patch.object(transport, "_run") as run:
            with self.assertRaises(ValueError):
                self.t.deliver("%1", "you have mail\nrm -rf /")
        run.assert_not_called()

    def test_the_payload_is_passed_as_an_argument_never_a_shell_string(self):
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("%1", "you have mail")
        self.assertIsInstance(run.call_args_list[0][0][0], list)
