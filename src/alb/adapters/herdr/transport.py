"""Herdr notifier transport.

Delivers a fixed, content-free line to ONE explicitly identified Herdr agent
pane with `herdr agent prompt`. This adapter calls Herdr; it does not modify it.

Use a pane id such as w1:p1 from:

    herdr agent list

`agent prompt` writes the line as a bracketed paste and presses Enter as one
ordered submission, so there is no second step to fail on its own. Before it
writes anything it refuses a pane that is not a detected agent
(agent_not_found) and an agent waiting at an approval or question dialog
(agent_blocked). Measured live: typing a ring into Claude Code's folder-trust
dialog with send-text + Enter chose "No, exit" and closed the agent;
`agent prompt` refused and left the dialog untouched.
"""
import json
import os
import subprocess

from alb.notifier import ring

HERDR = "herdr"


# A ring that cannot finish can stop the bridge. Pi's allowance analysis:
# while any notifier call is unbounded, no finite liveness allowance is
# provable, because the number would describe a path with no ceiling - and a
# bridge stuck in a subprocess looks exactly like the death a supervisor
# exists to detect. A bounded ring can fail; an unbounded one can hang.
RING_TIMEOUT = 10


class HerdrRefused(Exception):
    """Herdr declined the prompt before writing it. The message is Herdr's
    own error code, so ring health names the reason (agent_blocked,
    agent_not_found, server_not_running) instead of a bare exit status."""


def _run(argv, env=None):
    """Separated so tests never spawn a process."""
    subprocess.run(argv, check=True, capture_output=True,
                   timeout=RING_TIMEOUT, env=env)


def _error_code(stderr):
    try:
        return json.loads(stderr)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


class Herdr:
    """Ring by submitting a line to an agent pane through Herdr.

    THE TARGET SHOULD BE A DEDICATED AGENT PANE. Herdr cannot see the agent's
    input box: measured live, a ring into a pane holding half-typed text was
    appended to it and the combination submitted. Refusing dialogs is a real
    gain over cmux and tmux; protecting a human's draft is not one.

    A socket pins the Herdr server. Without one, `herdr` resolves the server
    from its own environment, which is right when the bridge runs inside the
    same Herdr session and wrong when several sessions are running.
    """

    def __init__(self, binary=HERDR, socket=""):
        self._binary = binary
        self._socket = socket

    def deliver(self, surface, line):
        if not surface:
            raise ring.NoTargetSurface("no herdr pane; refusing to guess")
        if "\n" in line or "\r" in line:
            raise ValueError("the doorbell payload must be a single line")
        env = None
        if self._socket:
            env = dict(os.environ, HERDR_SOCKET_PATH=self._socket)
        try:
            _run([self._binary, "agent", "prompt", surface, line], env=env)
        except subprocess.CalledProcessError as exc:
            code = _error_code(exc.stderr)
            if code:
                raise HerdrRefused(code) from exc
            raise
