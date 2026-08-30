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


class Cmux:
    """Ring by injecting a line into a pane, then submitting it.

    THE TARGET MUST BE A PANE NOBODY TYPES IN. This is a requirement on the
    operator, not something the code can check.

    Proven live: a doorbell injected into a pane holding half-typed text
    APPENDS to it and submits the combination as one input. Clearing first does
    not work - ctrl+u, ctrl+c and escape were all accepted by the multiplexer
    and none cleared the buffer. Acceptance of a key is not evidence of its
    effect.

    There is no fix at this layer. Input occupancy is not observable from
    outside the TTY, so "is a human about to type here?" cannot be answered by
    any amount of probing. The hazard is removed by CHOOSING a pane where the
    question never arises: a dedicated agent pane, not the prompt you work at.
    That is also the natural arrangement - if you are messaging from your
    phone, you are not sitting at that prompt.

    See docs/operations.md, "Choosing a surface".
    """

    def __init__(self, binary=CMUX):
        self._binary = binary

    def deliver(self, surface, line):
        if not surface:
            raise ring.NoTargetSurface("no surface; refusing to guess a pane")
        if "\n" in line or "\r" in line:
            raise ValueError("the doorbell payload must be a single line")
        _run([self._binary, "send", "--surface", surface, line])
        _run([self._binary, "send-key", "--surface", surface, "Enter"])
