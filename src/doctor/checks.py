"""Local diagnostics. No token, no platform calls, no getUpdates - ever.

The doctor is a diagnostic, not a second consumer: a doctor that polls is the
very thing it exists to detect. That boundary is not a policy note here, it is
asserted by test, including that this package cannot even reach the network.

A getUpdates conflict probe is FORBIDDEN and uninterpretable: an "ok" may mean
it just terminated another consumer's in-flight request, and telling which side
of the conflict you were on requires repeating it - the loop the boundary
forbids.
"""

import pathlib
import shutil
import sys

_TOKEN_HINTS = ("token", "secret", "api_key", "apikey")


def env_is_token_free(environ):
    """True if no credential-shaped variable is present in this process."""
    return not any(hint in key.lower() for key in environ for hint in _TOKEN_HINTS)


def webhook_check_command():
    """Return the command for the OPERATOR to run in their own shell.

    A webhook set on the bot conflicts with polling forever and is invisible
    locally, so it must be checked - but getWebhookInfo is read-only, consumes
    nothing and conflicts with nothing. The doctor prints it; it never runs it,
    because running it would require holding the token.

    If a webhook is set, the remedy is deleteWebhook or a token re-issue:
    polling cannot coexist with one.
    """
    return (
        "curl -s 'https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo'"
    )


# -- local single-consumer probe -------------------------------------------
#
# What this CAN prove: nothing else on this machine is running a bridge, and
# nothing holds the local lock. What it CANNOT prove: that no consumer exists
# on another machine. That case is not provable pre-flight from here, and the
# report says so rather than implying an all-clear.

# Match the bridge as an EXECUTABLE, never as a substring. "alb" appears in
# plenty of innocent command lines - an editor opening alb-plan.md, a grep, a
# heredoc writing this very file. A probe that cries wolf is worse than none,
# because the operator learns to ignore it.
_BRIDGE_EXECUTABLES = ("alb",)


def local_consumers(process_listing, self_pid):
    """Return lines that look like another bridge on this machine.

    Takes the listing rather than shelling out, so this is testable and so the
    doctor holds no process-control capability of its own.
    """
    found = []
    for line in process_listing:
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            pid = int(fields[1])
        except ValueError:
            continue
        if pid == self_pid:
            continue
        argv = fields[2:]
        # Only the first two arguments: a real invocation is either `alb ...`
        # or `python3 /path/alb ...`. Anything further along is the executable
        # being MENTIONED - inside a shell wrapper, an editor argument, a
        # heredoc - not run. Scanning the whole line makes the probe accuse
        # the shell that invoked it, which is how a diagnostic teaches an
        # operator to ignore it.
        head = argv[:2]
        if any(pathlib.PurePath(arg).name in _BRIDGE_EXECUTABLES for arg in head):
            found.append(f"pid {pid}: {' '.join(argv[:6])}")
    return found


def lock_state(root):
    """Report the singleton lock without taking it.

    Deliberately does not try to acquire: a diagnostic that grabs the lock it
    is reporting on would evict the very process it exists to observe.
    """
    path = pathlib.Path(root) / "bridge.lock"
    if not path.exists():
        return "no lock file present (no bridge has run against this directory)"
    return f"lock file present at {path} (a bridge may hold it)"


# -- daemon context ---------------------------------------------------------

_VERSION_MANAGERS = ("/.nvm/", "/.pyenv/", "/.rbenv/", "/fnm/", "/.volta/", "/asdf/")


def daemon_context(environ):
    """Report the things that differ between a shell and a service manager.

    This is the failure that costs a morning: the same command resolves a
    different interpreter, or cannot find a binary at all, because a service
    manager does not share the operator's PATH.
    """
    path = environ.get("PATH", "")
    return {
        "interpreter": sys.executable,
        "path": path,
        "cmux_found": bool(shutil.which("cmux", path=path)),
        "version_manager_on_path": any(m in path for m in _VERSION_MANAGERS),
    }


def summary(process_listing, self_pid, root, environ):
    """The operator-facing report. States limits as plainly as findings."""
    competing = local_consumers(process_listing, self_pid)
    context = daemon_context(environ)

    lines = ["agent-letter-bridge doctor", ""]
    lines.append("TOKEN")
    lines.append(f"  this process holds no credential : {env_is_token_free(environ)}")
    lines.append("")
    lines.append("LOCAL SINGLE-CONSUMER PROBE")
    if competing:
        lines.append("  ANOTHER BRIDGE APPEARS TO BE RUNNING:")
        lines.extend(f"    {c}" for c in competing)
    else:
        lines.append("  no other bridge process found on this machine")
    lines.append(f"  {lock_state(root)}")
    lines.append("")
    lines.append("DAEMON CONTEXT")
    lines.append(f"  interpreter now : {context['interpreter']}")
    lines.append(f"  cmux resolves   : {context['cmux_found']}")
    if context["version_manager_on_path"]:
        lines.append("  WARNING: a version manager is on PATH. A service manager")
        lines.append("           will not see it. Pin absolute paths in the unit file.")
    lines.append("")
    lines.append("WHAT THIS CANNOT PROVE")
    lines.append("  A consumer on ANOTHER MACHINE is not detectable from here.")
    lines.append("  A webhook is not detectable from here either - run this")
    lines.append("  yourself, it is read-only and consumes nothing:")
    lines.append(f"    {webhook_check_command()}")
    lines.append("  If the token's history is unknown, revoke and re-issue it:")
    lines.append("  that makes single-consumer true by construction, which no")
    lines.append("  amount of probing can.")
    return "\n".join(lines)
