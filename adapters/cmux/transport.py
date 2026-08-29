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
    def __init__(self, binary=CMUX):
        self._binary = binary

    def deliver(self, surface, line):
        if not surface:
            raise ring.NoTargetSurface("no surface; refusing to guess a pane")
        if "\n" in line or "\r" in line:
            raise ValueError("the doorbell payload must be a single line")

        _run([self._binary, "send", "--surface", surface, line])
        _run([self._binary, "send-key", "--surface", surface, "Enter"])
