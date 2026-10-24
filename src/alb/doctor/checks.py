"""Local diagnostics. No token, no platform calls, no getUpdates - ever.

The doctor is a diagnostic, not a second consumer: a doctor that polls is the
very thing it exists to detect. That boundary is not a policy note here, it is
asserted by test, including that this package cannot even reach the network.

A getUpdates conflict probe is FORBIDDEN and uninterpretable: an "ok" may mean
it just terminated another consumer's in-flight request, and telling which side
of the conflict you were on requires repeating it - the loop the boundary
forbids.
"""

import json
import re
import pathlib
import shutil
import sys

# Scoped to THIS TOOL'S variables. The claim being made is "alb is not holding
# the bot token", not "the surrounding shell contains no secrets" - an operator
# exports AWS keys and GitHub tokens as a matter of course, and a check that
# fails in almost every real environment is a wolf. An operator who sees one
# learns to ignore the whole report.
_OUR_PREFIX = "ALB_"
_TOKEN_HINTS = ("token", "secret", "api_key", "apikey")


def env_is_token_free(environ):
    """True if THIS TOOL is not holding a credential.

    Only ALB_-prefixed variables count. Whatever else the operator's shell
    exports is their business and not evidence about the doctor.
    """
    return not any(
        key.upper().startswith(_OUR_PREFIX)
        and any(hint in key.lower() for hint in _TOKEN_HINTS)
        for key in environ
    )


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


def bot_id(token):
    """The identifying half of a Telegram token, or None.

    A token is `<bot id>:<secret>`. Telling two bridges apart needs only the
    id, so only the id is ever lifted out of a config file - the secret half
    is dropped here and never reaches a caller, a report or a log.
    """
    if not isinstance(token, str) or ":" not in token:
        return None
    head = token.split(":", 1)[0].strip()
    return head or None


# The app-level token is `xapp-<n>-<app id>-<n>-<secret>`. The app id is what
# "one Slack app serves one bridge" is about, so it is the identity two Slack
# bridges are compared on - lifted out locally, the secret half discarded.
_SLACK_APP_ID = re.compile(r"xapp-\d+-(A[A-Z0-9]+)-")

# A Slack ID written alone, as a Telegram chat id would be. On a Slack bridge
# an allowlist entry is `<conversation>/<member>`, so this matches nothing. A
# Telegram id is numeric, so no Telegram allowlist can look like this.
_BARE_SLACK_ID = re.compile(r"[CDGU][A-Z0-9]{6,}")


def slack_app_id(app_token):
    """The app id from a Slack app-level token, or None. A local parse."""
    if not isinstance(app_token, str):
        return None
    match = _SLACK_APP_ID.match(app_token)
    return match.group(1) if match else None


def _bridge_identity(config):
    """What tells this bridge's bot apart: a Telegram bot id, a Slack app, or
    a Discord application."""
    platform = config.get("ALB_PLATFORM", "telegram")
    if platform == "slack":
        app = slack_app_id(config.get("ALB_APP_TOKEN", ""))
        return f"slack:{app}" if app else None
    if platform == "discord":
        from alb.adapters.discord import invite
        app = invite.application_id(config.get("ALB_TOKEN", ""))
        return f"discord:{app}" if app else None
    return bot_id(config.get("ALB_TOKEN", ""))


def _platform_of_root(root):
    """The platform a root's config names; telegram when it cannot be read."""
    from alb.bridge import run
    try:
        return run.platform_of(run.load_config(pathlib.Path(root) / "bridge.env"))
    except Exception:  # noqa: BLE001 - unreadable reads as the default
        return "telegram"


def _bare_slack_ids(root):
    path = pathlib.Path(root) / "allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    chats = data.get("chats") if isinstance(data, dict) else None
    if not isinstance(chats, list):
        return []
    return [str(c) for c in chats
            if isinstance(c, str) and _BARE_SLACK_ID.fullmatch(c)]


# A Discord ID written alone. On a Discord bridge an entry is
# `<channel>/<user>`, so this matches nothing. Telegram ids are far shorter,
# but the check runs only on a Discord bridge all the same.
_BARE_DISCORD_ID = re.compile(r"[0-9]{17,20}")


def _bare_discord_ids(root):
    if _platform_of_root(root) != "discord":
        return []
    path = pathlib.Path(root) / "allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    chats = data.get("chats") if isinstance(data, dict) else None
    if not isinstance(chats, list):
        return []
    return [str(c) for c in chats
            if isinstance(c, str) and _BARE_DISCORD_ID.fullmatch(c)]


def discord_advisories(root):
    """Lines an operator of a Discord bridge needs, from local files only."""
    return [f'allowlist entry "{entry}" is not a channel/user pair; on '
            f"Discord it matches nothing." for entry in _bare_discord_ids(root)]


def slack_advisories(root):
    """Lines an operator of a Slack bridge needs, from local files only."""
    lines = []
    for entry in _bare_slack_ids(root):
        lines.append(f'allowlist entry "{entry}" is not a conversation/member '
                     f"pair (for example D0123ABC/U0456DEF); on Slack it "
                     f"matches nothing.")
    path = pathlib.Path(root) / "state" / "slack-connection.json"
    try:
        count = int(json.loads(path.read_text(encoding="utf-8"))["connections"])
    except (OSError, ValueError, KeyError, TypeError):
        count = 0
    if count > 1:
        lines.append(f"Slack reports {count} connections for this app. One "
                     f"Slack app serves one bridge: stop the other one, or "
                     f"give each bridge its own app.")
    return lines


def _flag(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _config_path(argv):
    """The config file a candidate actually loaded.

    Resolved the way the CLI resolves it: an explicit `--config` wins, and
    `--root` supplies `bridge.env` only when no config was named. Preferring
    the root meant reading a DIFFERENT file from the one the process loaded,
    and a bot id read from the wrong file can clear a real competitor.
    """
    named = _flag(argv, "--config")
    if named:
        return pathlib.Path(named)
    root = _flag(argv, "--root")
    return pathlib.Path(root) / "bridge.env" if root else None


def bot_of_root(root, config=None):
    """The bot id from a candidate's config, or None when it cannot be known.

    ASKS THE LOADER THE RUNTIME ASKS. A second parser here read the FIRST
    `ALB_TOKEN=` while `run.load_config` takes the LAST, so a file with the
    key twice was attributed to a bot the process had never loaded - and the
    probe then cleared a genuine same-bot competitor on the strength of it.
    Reimplementing a parser is how the two drift; there is only one now.

    Inheriting the loader also inherits its refusals: a config whose
    permissions let others read a token is not read here either. Refusing
    yields None, which is reported rather than cleared.
    """
    from alb.bridge import run

    path = pathlib.Path(config) if config else (
        pathlib.Path(root) / "bridge.env" if root else None)
    if path is None:
        return None
    try:
        return _bridge_identity(run.load_config(path))
    except Exception:  # noqa: BLE001 - any failure to read is "unknown"
        return None


def local_consumers(process_listing, self_pid, our_bot=None, bot_of=None):
    """Return lines for another bridge that may be holding OUR bot.

    Takes the listing rather than shelling out, so this is testable and so the
    doctor holds no process-control capability of its own.

    MATCHING ON THE NAME ALONE WAS BOTH WRONG WAYS AT ONCE. On a machine
    running several seats it announced relays that hold different bots and
    therefore cannot conflict, while the bridge actually being replaced was
    invisible because it is not called `alb`. It warned about copies of itself
    that could not clash and stayed silent about the one that could.

    So a candidate is cleared only when its bot is POSITIVELY KNOWN to be a
    different one. An unreadable config, an unknown root, or no bot of our own
    to compare against all leave it reported: not being able to prove a
    conflict is not evidence of safety, and the case the probe exists for is
    exactly the one it cannot prove.
    """
    bot_of = bot_of or (lambda path: bot_of_root(None, config=path))
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
        # Skip a leading `env`: /usr/bin/env python3 /path/alb is a real
        # unit-file and wrapper shape, and it pushes the executable out of the
        # first two arguments. Skipping it closes the miss without widening
        # the window - a diagnostic that MISSES is the other half of one that
        # shouts, and both teach an operator to distrust the report.
        if argv and pathlib.PurePath(argv[0]).name == "env":
            argv_for_match = argv[1:]
        else:
            argv_for_match = argv
        head = argv_for_match[:2]
        if not any(pathlib.PurePath(arg).name in _BRIDGE_EXECUTABLES for arg in head):
            continue
        verdict = "unknown"
        if our_bot:
            theirs = bot_of(_config_path(argv))
            if theirs == our_bot:
                verdict = "same"
            elif theirs:
                # One consumer per TOKEN, so a different bot cannot compete
                # for ours. Reported anyway, never deleted: a file's contents
                # are not proof of what a live process loaded, and a clear
                # made by omission is a wrong clear nobody can see.
                verdict = "different"
        found.append({"pid": pid, "command": " ".join(argv[:6]),
                      "bot": verdict})
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


def deliverability(root):
    """Can this bridge deliver anything at all?

    A missing or empty allowlist is CORRECT fail-closed behaviour and also the
    state in which the bridge runs perfectly and delivers nothing forever. Every
    other signal - exit code, status, this doctor - reports health, while the
    operations doc teaches that silence is the deny path working. The operator
    is then told, by everything available, that their broken install is fine.

    So say it. The security posture does not change; the silence stops being
    unexplained.
    """
    path = pathlib.Path(root) / "allowlist.json"
    if not path.is_file():
        return {"can_deliver": False,
                "reason": f"no allowlist at {path}: nothing will ever be delivered"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"can_deliver": False,
                "reason": f"allowlist at {path} is unreadable or malformed"}
    chats = data.get("chats") if isinstance(data, dict) else None
    if not isinstance(chats, list) or not chats:
        return {"can_deliver": False,
                "reason": f"allowlist at {path} is empty: every sender is denied"}
    if len(_bare_slack_ids(root)) == len(chats):
        return {"can_deliver": False,
                "reason": f"allowlist at {path} holds only bare Slack IDs; a "
                          f"Slack entry is <conversation>/<member>"}
    if len(_bare_discord_ids(root)) == len(chats):
        return {"can_deliver": False,
                "reason": f"allowlist at {path} holds only bare Discord IDs; a "
                          f"Discord entry is <channel>/<user>"}
    return {"can_deliver": True, "reason": f"{len(chats)} chat(s) permitted"}


def summary(process_listing, self_pid, root, environ):
    """The operator-facing report. States limits as plainly as findings."""
    # Our own bot id comes from the config, not the environment. The
    # doctor carries no credential of its own; reading one from a config
    # is a deliberate, narrow exception that keeps only the id half.
    competing = local_consumers(process_listing, self_pid,
                                our_bot=bot_of_root(root))
    context = daemon_context(environ)

    delivery = deliverability(root)

    lines = ["agent-letter-bridge doctor", ""]
    if not delivery["can_deliver"]:
        lines.append("*** NOTHING WILL BE DELIVERED ***")
        lines.append(f"  {delivery['reason']}")
        lines.append("  This is fail-closed behaviour working correctly, and it is")
        lines.append("  also indistinguishable from a dead bot. Add your chat id to")
        lines.append("  the allowlist - see INSTALL.md, step 7.")
        lines.append("")
    else:
        lines.append(f"DELIVERY: {delivery['reason']}")
        lines.append("")
    lines.append("TOKEN")
    lines.append(f"  no credential in this tool's own environment : "
                 f"{env_is_token_free(environ)}")
    lines.append("  (only ALB_ variables are checked; your shell's own secrets")
    lines.append("   are none of the doctor's business)")
    # Say it here rather than let the line above imply otherwise. The probe
    # below loads each bridge's config, which is a file containing a token -
    # so "not holding a token" was true of the environment and no longer true
    # of the run. It keeps the identifying half and discards the secret.
    lines.append("  to tell bridges apart the probe below loads each one's")
    lines.append("  config and keeps the bot id only; the secret half is")
    lines.append("  discarded and never printed")
    lines.append("")
    lines.append("LOCAL SINGLE-CONSUMER PROBE")
    contenders = [c for c in competing if c["bot"] in ("same", "unknown")]
    cleared = [c for c in competing if c["bot"] == "different"]
    # EVERY HEADING NAMES ITS EVIDENCE. Identity here is read from each
    # candidate's config as it stands now; a live process loaded its config at
    # startup and the file may have changed since. That is an inference about
    # a file, not proof of a running identity, and a heading that forgets the
    # difference is the same overclaim the name-matching probe made.
    if contenders:
        lines.append("  ANOTHER BRIDGE MAY BE HOLDING YOUR BOT:")
        for c in contenders:
            tag = ("its config names your bot" if c["bot"] == "same"
                   else "could not read which bot")
            lines.append(f"    pid {c['pid']}: {c['command']}  [{tag}]")
    else:
        lines.append("  no other bridge whose config names your bot")
    if cleared:
        # Listed rather than deleted: a clear made by omission is a wrong
        # clear nobody can see.
        lines.append("  other bridges running; current config names a "
                     "different bot")
        lines.append("  (running process identity unverified):")
        for c in cleared:
            lines.append(f"    pid {c['pid']}: {c['command']}")
    lines.append(f"  {lock_state(root)}")
    lines.append("")
    lines.append("DOORBELL HELPER")
    from alb.helper_probe import inspect_helper
    binary = (environ or {}).get("ALB_BUS_BINARY") or ""
    helper_note = None
    if not binary:
        from alb.bridge import run as bridge_run
        try:
            cfg = bridge_run.load_config(pathlib.Path(root) / "bridge.env")
            binary = cfg.get("ALB_BUS_BINARY") or (
                "letterbox" if cfg.get("ALB_MAIL_ROOT") else "")
        except (bridge_run.ConfigError, OSError) as exc:
            helper_note = (
                f"config not loadable: {exc}; helper compatibility UNKNOWN"
            )
            binary = None
    if helper_note:
        lines.append(f"  {helper_note}")
    elif not binary:
        lines.append("  standalone: no letterbox helper (own notifier)")
    else:
        _status, msg = inspect_helper(binary)
        lines.append(f"  {msg}")
        if _status != "ok":
            lines.append("  integrated mode rings through `letterbox ring`: Letterbox 0.5.1")
            lines.append("  or later for cmux, tmux and Zellij, 0.6.2 or later for Herdr.")
            lines.append("  To update, run your edition's install command again.")
    lines.append("")
    lines.append("DAEMON CONTEXT")
    lines.append(f"  interpreter now : {context['interpreter']}")
    lines.append(f"  cmux resolves   : {context['cmux_found']}")
    if context["version_manager_on_path"]:
        lines.append("  WARNING: a version manager is on PATH. A service manager")
        lines.append("           will not see it. Pin absolute paths in the unit file.")
    lines.append("")
    advisories = slack_advisories(root)
    if advisories:
        lines.append("SLACK")
        lines.extend(f"  {line}" for line in advisories)
        lines.append("")
    advisories = discord_advisories(root)
    if advisories:
        lines.append("DISCORD")
        lines.extend(f"  {line}" for line in advisories)
        lines.append("")
    lines.append("WHAT THIS CANNOT PROVE")
    lines.append("  A consumer on ANOTHER MACHINE is not detectable from here.")
    if _platform_of_root(root) == "slack":
        lines.append("  A second bridge using the same Slack app is not detectable")
        lines.append("  either. Slack spreads events across every connection, so")
        lines.append("  each would see only some of your messages. alb --status")
        lines.append("  shows how many connections Slack reported at the last")
        lines.append("  connect; give each bridge its own app.")
        return "\n".join(lines)
    if _platform_of_root(root) == "discord":
        lines.append("  A second bridge using the same Discord bot is not")
        lines.append("  detectable either. Every connection on one bot receives")
        lines.append("  every message, so both bridges would write the letter and")
        lines.append("  both would answer it. Give each bridge its own")
        lines.append("  application.")
        return "\n".join(lines)
    lines.append("  A webhook is not detectable from here either - run this")
    lines.append("  yourself, it is read-only and consumes nothing:")
    lines.append(f"    {webhook_check_command()}")
    lines.append("  If the token's history is unknown, revoke and re-issue it:")
    lines.append("  that makes single-consumer true by construction, which no")
    lines.append("  amount of probing can.")
    return "\n".join(lines)
