"""cmux notifier adapter: delivers a fixed line to one identified surface."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from adapters.cmux import transport  # noqa: E402
from notifier import ring  # noqa: E402


class Delivery(unittest.TestCase):
    def setUp(self):
        # Injection is opt-in; these exercise the injecting path deliberately.
        self.t = transport.Cmux(allow_inject=True)

    def test_it_addresses_the_surface_explicitly_and_never_the_focused_pane(self):
        """Ringing 'whatever is focused' is how the wrong agent gets woken."""
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("SURFACE-1", "you have mail")
        sent = run.call_args_list[0][0][0]
        self.assertIn("--surface", sent)
        self.assertIn("SURFACE-1", sent)
        self.assertNotIn("--focused", sent)

    def test_it_refuses_to_inject_by_default(self):
        """PROVEN LIVE, twice. A doorbell injected into a pane holding
        half-typed text APPENDS to it and submits the combination:

            rm -rf /some/half/typed/thingyou have mail: check your letterbox

        Clearing first does not work: ctrl+u, ctrl+c and escape were all
        ACCEPTED by the multiplexer and none cleared the buffer. Acceptance of
        a key is not evidence of its effect, and input occupancy is not
        observable from outside the TTY - so the buffer's contents are
        unknowable and injecting into an unknown buffer is the defect itself.

        Letters are authoritative. A missed ring is always acceptable; a
        chimera command is not.
        """
        default = transport.Cmux()   # no opt-in: the shipped default
        with mock.patch.object(transport, "_run") as run:
            with self.assertRaises(transport.UnsafeToInject):
                default.deliver("SURFACE-1", "you have mail")
        run.assert_not_called()

    def test_injection_requires_an_explicit_opt_in(self):
        """Clobber is a named choice by the operator, never a silent default,
        and it destroys whatever was being typed."""
        t = transport.Cmux(allow_inject=True)
        with mock.patch.object(transport, "_run") as run:
            t.deliver("SURFACE-1", "you have mail")
        self.assertEqual(run.call_count, 2)

    def test_the_line_and_the_return_are_separate_calls(self):
        """A line without a return is text sitting in a prompt, not a turn."""
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("SURFACE-1", "you have mail")
        self.assertEqual(run.call_count, 2)
        self.assertIn("send-key", run.call_args_list[1][0][0])

    def test_it_refuses_an_empty_surface(self):
        with mock.patch.object(transport, "_run") as run:
            with self.assertRaises(ring.NoTargetSurface):
                self.t.deliver("", "you have mail")
        run.assert_not_called()

    def test_it_refuses_multi_line_payloads(self):
        """The payload is a fixed content-free line. A newline would submit
        early and make the remainder a second, unreviewed instruction."""
        with mock.patch.object(transport, "_run") as run:
            with self.assertRaises(ValueError):
                self.t.deliver("SURFACE-1", "you have mail\nrm -rf /")
        run.assert_not_called()

    def test_the_payload_is_passed_as_an_argument_never_a_shell_string(self):
        """A shell string would make the payload injectable."""
        with mock.patch.object(transport, "_run") as run:
            self.t.deliver("SURFACE-1", "you have mail")
        self.assertIsInstance(run.call_args_list[0][0][0], list)
