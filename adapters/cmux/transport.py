"""cmux notifier transport.

Delivers a fixed, content-free line to ONE explicitly identified surface, then
a return to submit it. This adapter calls cmux; it does not modify it.

Two rules carry the safety here:
  - the surface is always explicit. Ringing whatever pane happens to be focused
    is how the wrong agent gets woken, and identity uncertainty must fail
    closed rather than guess.
  - the payload is a single line, passed as an argument rather than a shell
    string. Injected text arrives at an agent as authoritative user input, so a
    newline would submit early and turn the remainder into a second, unreviewed
    instruction.
"""
import subprocess

from notifier import ring

CMUX = "cmux"


def _run(argv):
    """Separated so tests never spawn a process."""
    subprocess.run(argv, check=True, capture_output=True)


class UnsafeToInject(Exception):
    """The pane's input buffer contents are unknown, so injecting is unsafe.

    Proven live, twice: a doorbell injected into a pane holding half-typed text
    appends to it and submits the combination as one input. Clearing first does
    not work - ctrl+u, ctrl+c and escape were all ACCEPTED by the multiplexer
    and none cleared the buffer. Acceptance of a key is not evidence of its
    effect.

    Input occupancy is not observable from outside the TTY, so "is it safe to
    inject" cannot be answered. Letters are authoritative and every accelerator
    may fail, so a missed ring is always acceptable. A chimera command built
    from someone's unfinished typing plus our line is not.
    """


class Cmux:
    def __init__(self, binary=CMUX, allow_inject=False):
        self._binary = binary
        # Clobber is a named choice by the operator, never a silent default.
        # It destroys whatever was being typed in the target pane.
        self._allow_inject = allow_inject

    def deliver(self, surface, line):
        if not surface:
            raise ring.NoTargetSurface("no surface; refusing to guess a pane")
        if "\n" in line or "\r" in line:
            raise ValueError("the doorbell payload must be a single line")
        if not self._allow_inject:
            raise UnsafeToInject(
                "refusing to inject: the pane's input buffer may hold unfinished "
                "typing, and appending to it would submit a command nobody chose. "
                "Enable explicitly only for a pane you accept clobbering."
            )

        _run([self._binary, "send", "--surface", surface, line])
        _run([self._binary, "send-key", "--surface", surface, "Enter"])
