"""tmux notifier transport.

Contributed by the dogfood deployment that first ran this on Linux, where no
tmux adapter existed while the documentation promised one. Kept close to their
original.

Delivers a fixed, content-free line to ONE explicitly identified tmux pane,
then presses Enter. This adapter calls tmux; it does not modify tmux.

Use a pane id such as %1 from:

    tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'

The payload is sent with `send-keys -l`, so it is literal text - not a shell
string and not interpreted as a tmux key name.
"""
import subprocess

from alb.notifier import ring

TMUX = "tmux"


def _run(argv):
    """Separated so tests never spawn a process."""
    subprocess.run(argv, check=True, capture_output=True)


class Tmux:
    """Ring by injecting a line into a tmux pane, then submitting it.

    THE TARGET SHOULD BE A DEDICATED AGENT PANE. If a human or process has
    half-typed text there, tmux appends the doorbell to it and Enter submits the
    combination. The bridge cannot observe input occupancy from outside the TTY,
    so the safety boundary is choosing the right pane rather than detecting its
    state - the same conclusion the cmux adapter reached, by the same route.
    """

    def __init__(self, binary=TMUX):
        self._binary = binary

    def deliver(self, surface, line):
        if not surface:
            raise ring.NoTargetSurface("no tmux pane; refusing to guess")
        if "\n" in line or "\r" in line:
            raise ValueError("the doorbell payload must be a single line")
        _run([self._binary, "send-keys", "-t", surface, "-l", line])
        _run([self._binary, "send-keys", "-t", surface, "Enter"])
