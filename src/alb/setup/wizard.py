"""`alb init`: do the boilerplate, refuse the judgement calls.

Asked independently whether the install could be made easier, all four
reviewers proposed the same single command and refused the same shortcuts. The
agreement is the design: this creates the directory, the config and the
allowlist with the right modes, and it does nothing that requires knowing
something only the operator knows.

WHAT IT WILL NOT DO, and why each one is a refusal rather than an omission:

  - It will not invent an allowlist entry. The allowlist is the only thing
    between a stranger and the agents on this machine. It is created DENYING
    EVERYONE; the file existing is the convenience, the file being empty is the
    security property.

  - It will not take a token as an argument. argv is recorded by the shell, by
    the process table, and by anything reading either. The token is asked for
    without echo or it is not asked for here at all.

  - It will not poll for messages unless the operator asks it to, in that
    moment, having been told what the call is. On Telegram, pasting a token
    is a different ask: one check of whether the platform accepts the token,
    and not a poll. Anyone who declines the chat read can be told that setup
    did not poll. Slack and Discord do not make that check.

  - It will not overwrite anything. Re-running this on a working bridge must
    not silently disarm it: an allowlist reset to deny-all reads exactly like a
    dead bot, which is the failure this whole design is organised against.

  - It will not choose a pane. A listing cannot say which pane holds the agent
    the operator meant, and a ring typed into the wrong pane lands in somebody
    else's session.

  - It will not guess a mailbox from a layout it invented. A terminal that
    already names a seat uses that seat. Any other terminal is asked for
    the name of the agent, and that name joins only an exact seat in the
    folder this terminal was given, or the one folder Letterbox's own
    setup has already recorded. A blank does not join. A name that is not
    a seat does not create one, and a typo does not attach to a nearby
    name. No other layout is searched, and the working directory is not
    a folder.
"""
import json
import os
import pathlib
import re
import shutil
import stat


class SetupError(Exception):
    """Init refused. Message is safe to print: never contains a token."""

DIR_MODE = 0o700
FILE_MODE = 0o600

CHAT_ID_COMMAND = (
    'curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \\\n'
    "  | python3 -c 'import json,sys; "
    'print(json.load(sys.stdin)["result"][0]["message"]["chat"]["id"])\''
)

# One BotFather token: a short numeric id, one colon, then about 35
# characters. A second paste in the same hidden prompt adds a second colon
# or a length that is not one token. The check prints the length only.
_TELEGRAM_TOKEN = re.compile(r"[0-9]{6,12}:[A-Za-z0-9_-]{30,50}")


def _write_private(path, text):
    """Create with the mode already set, never set it afterwards.

    os.open with the mode in the call closes the window in which the file
    exists at the umask's permissions - which on a default umask is
    world-readable, and this file holds a token.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, FILE_MODE)


def _yes(answer):
    return answer.strip().lower() in ("y", "yes")


# A seat name is one path segment. The same shape Letterbox uses for a
# participant: a letter, then letters, digits, dot, underscore, or hyphen.
_SEAT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,31}$")


def _seats_in(directory):
    """Names of child folders that already have an inbox.

    Empty when the directory is missing or unreadable. A symlink is not a
    seat: following one would offer a folder the operator did not put here.
    Hidden names and folders with no inbox are not seats. Nothing is created.
    """
    if not directory:
        return []
    root = pathlib.Path(directory).expanduser()
    try:
        if not root.is_dir():
            return []
        names = []
        for child in root.iterdir():
            name = child.name
            if not _SEAT_NAME.fullmatch(name) or child.is_symlink():
                continue
            inbox = child / "inbox"
            if inbox.is_symlink() or not inbox.is_dir():
                continue
            names.append(name)
    except OSError:
        return []
    return sorted(names)


# The two states in which a bell will actually ring. "configured" is a pane
# the operator pasted; "helper" is integrated mode, where the letterbox
# resolves the recipient's pane itself. Anything else means no bell, and the
# resident offer refuses to start a bridge that cannot ring one.
RINGS = ("configured", "helper")


# Distinguishes "caller said None" from "caller said nothing": None is a
# meaningful answer here, meaning there is no safe command.
_UNSET = object()


def _consume_token_file(path):
    """Read a 0600 token file and delete it. Never returns the path into logs."""
    path = pathlib.Path(path)
    try:
        st = os.lstat(path)
    except OSError:
        raise SetupError("token file unreadable") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise SetupError("token file refused")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        raise SetupError("token file unreadable") from None
    try:
        try:
            st2 = os.fstat(fd)
        except OSError:
            raise SetupError("token file unreadable") from None
        if not stat.S_ISREG(st2.st_mode):
            raise SetupError("token file refused")
        if st2.st_mode & 0o077:
            raise SetupError("token file must be mode 600")
        if (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
            raise SetupError("token file refused")
        try:
            raw = os.read(fd, st2.st_size)
        except OSError:
            raise SetupError("token file unreadable") from None
    finally:
        os.close(fd)
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SetupError("token file unreadable") from None
    if not token:
        raise SetupError("token file empty")
    if any(ch.isspace() for ch in token):
        raise SetupError("token file refused")
    try:
        os.unlink(path)
    except OSError:
        raise SetupError("token file could not be consumed") from None
    return token


def recorded_letterbox_dir(path=None):
    """The folder Letterbox's own setup recorded, or "" if there is none.

    The file holds one path. A missing file, a symlink, or more than one
    line is no folder. This does not look at the working directory and
    does not search the machine. Tests pass `path` so they never open the
    live file. The real command calls this with no argument.
    """
    if path is None:
        config = os.environ.get("XDG_CONFIG_HOME") or str(
            pathlib.Path.home() / ".config")
        path = pathlib.Path(config) / "agent-letterbox" / "default-dir"
    else:
        path = pathlib.Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text or "\n" in text or "\x00" in text:
        return ""
    return text


def installed_letterbox(path=None):
    """The one helper path Letterbox's installer uses, or "" if it is absent.

    Tests pass `path`. The real command calls this with no argument, which
    is the only place that looks at the install path on this machine.
    """
    if path is None:
        path = pathlib.Path.home() / ".local" / "bin" / "letterbox"
    else:
        path = pathlib.Path(path)
    try:
        if path.is_file():
            return str(path)
    except OSError:
        return ""
    return ""


def _folder_for_a_typed_name(letterbox_dir, recorded_dir):
    """The one folder a typed name may join, and the seats already in it.

    A folder this terminal was given is the only folder, even when it
    lists nothing. The recorded default is used only when no folder was
    given, and only when the reader returns one. No reader means no folder.
    """
    given = (letterbox_dir or "").strip()
    if given:
        folder = str(pathlib.Path(given).expanduser())
        return folder, _seats_in(folder)
    recorded = ""
    if recorded_dir is not None:
        try:
            recorded = (recorded_dir() or "").strip()
        except OSError:
            recorded = ""
    if not recorded:
        return "", []
    folder = str(pathlib.Path(recorded).expanduser())
    return folder, _seats_in(folder)


def _ask_agent_name(console):
    console.say("Name of the agent.")
    console.say("One word, the name that agent already uses.")
    console.say("Leave it blank and the bridge keeps its own folder.")
    console.say("A name that is not already an agent here does not create one,")
    console.say("and it does not match a nearby name.")
    return console.ask("  name", "").strip()


def _join_typed_name(console, typed, folder, seats):
    """Join an exact seat, or say why this run keeps its own folder.

    Returns (integrated, mailbox, recipient). Nothing is created.
    """
    if not typed or not _SEAT_NAME.fullmatch(typed):
        if typed:
            console.say("That is not a name this setup can use, so this run does not use an agent.")
        else:
            console.say("No name was given, so this run does not use an agent.")
        console.say("The bridge will keep its own folder.")
        if not folder:
            console.say("Setup did not look for a folder.")
        console.say("It will not ask you for a folder.")
        return False, "", ""
    if not folder:
        console.say("Setup did not look for a folder.")
        console.say("The name is not used.")
        console.say("The bridge will keep its own folder.")
        console.say("It will not ask you for a folder.")
        return False, "", ""
    if typed not in seats:
        console.say(f"  {typed} is not an agent in that folder.")
        console.say("Setup will not create one, and it will not match a nearby name.")
        console.say("The bridge will keep its own folder.")
        console.say("It will not ask you for a folder.")
        return False, "", ""
    console.say("Is Letterbox already installed for this agent?")
    console.say("  yes - messages go to that agent's folder.")
    console.say("  no  - the bridge keeps its own folder.")
    if not _yes(console.ask("  Y/n", "y")):
        console.say("The bridge will keep its own folder.")
        return False, "", ""
    mailbox = str(pathlib.Path(folder).expanduser() / typed)
    return True, mailbox, typed


def _resolve_helper(console, helper_on_path, helper_install):
    """Return (join, helper_path). A path is never asked for.

    A helper on PATH that fails its check still joins, and the path is not
    recorded. That is the previous behaviour. Anything else has to be the
    one installer path and has to pass the same check. If neither is
    usable, this run does not join.
    """
    from alb.helper_probe import inspect_helper

    def recorded(path):
        helper = _helper_absolute(path)
        console.say(f"  doorbell helper: {helper}")
        console.say("  recorded as ALB_BUS_BINARY, so a later PATH")
        console.say("  cannot swap it.")
        return helper

    def rejected(msg):
        console.say()
        console.say(msg)
        console.say("A new message is announced with `letterbox ring`:")
        console.say("Letterbox 0.5.1 or later for cmux, tmux and Zellij,")
        console.say("0.6.2 or later for Herdr. To update, run your")
        console.say("edition's install command again. Installing now will")
        console.say("record every announcement as failing until it is updated.")

    if helper_on_path:
        status, msg = inspect_helper(helper_on_path)
        if status != "ok":
            rejected(msg)
            console.say("  That helper was not recorded.")
            return True, ""
        return True, recorded(helper_on_path)

    candidate = ""
    if helper_install is not None:
        try:
            candidate = (helper_install() or "").strip()
        except OSError:
            candidate = ""
    if candidate:
        status, msg = inspect_helper(candidate)
        if status == "ok":
            return True, recorded(candidate)
        rejected(msg)

    console.say()
    console.say("The name matches an agent, but letterbox is not available.")
    console.say("This run keeps the bridge's own folder rather than one")
    console.say("it cannot announce.")
    console.say("Re-run when letterbox is on PATH.")
    return False, ""


def init(root, console, chat_id_reader=None, panes=None, helper_found=None,
         cmux_born=None, bridge_running=None, start_pane=None,
         resident_command=_UNSET, token_file=None, platform="telegram",
         app_token_file=None, slack_call=None, discord_call=None,
         letterbox_dir="", letterbox_agent="", token_check=None,
         recorded_dir=None, helper_install=None):
    """Create the boilerplate under `root`, asking for what cannot be derived.

    `console` supplies say / ask / ask_secret, so the questions are testable
    and so the secret prompt is a distinct call rather than a convention.

    Note the absence of a `token` parameter: it is asked for, never passed.
    A signature that accepted one would grow a --token flag, and a flag is
    shell history. `--token-file` is for when no terminal can take the
    paste: a 0600 file is read and deleted, so the token never appears in
    argv. When the person is here, the hidden prompt is the path, in a
    terminal the caller does not read.

    `letterbox_agent` is the seat this terminal already has. There is no
    flag for it. A carried name that is not already a seat stops setup
    instead of asking the person to pick one. When the terminal names no
    seat, the person is asked for the name of the agent. That name joins
    only an exact seat. It does not create one.

    `recorded_dir` and `helper_install` default to absent so a test does
    not read this machine. The real command passes the readers.
    """
    root = pathlib.Path(root).expanduser()
    summary = {"mode": "standalone", "created": [], "kept": [], "ring": "not configured"}

    console.say("alb init - creates the files, asks for the rest.")
    if platform in ("slack", "discord"):
        console.say("Nothing here is sent anywhere unless you ask for it explicitly.")
    else:
        console.say("Pasting a token asks the platform whether it accepts that token.")
        console.say("Nothing else is sent unless you ask for it explicitly.")
    console.say()

    # 1. The directory. 0700 stated, not inherited: a directory created under
    #    the default umask is 0755, and this one accumulates a canary log naming
    #    chats, an offset describing traffic, and dead letters quoting failures.
    existed = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, DIR_MODE)
    summary["kept" if existed else "created"].append(str(root))

    # 2. Standalone or integrated.
    #
    # A terminal that already names a seat uses it and does not ask.
    # Any other terminal is asked for the name of the agent. That name
    # joins only an exact seat. There is no folder question, no numbered
    # list, and no helper-path question.
    #
    # The folder is the one this terminal was given. When it was not given
    # one, the only other folder is the one Letterbox's own setup recorded,
    # and only when that record exists. A given folder does not fall
    # through to the record. The working directory is not a folder.
    helper_lookup = helper_found or shutil.which
    helper_on_path = helper_lookup("letterbox")
    carried = (letterbox_agent or "").strip()

    mailbox = ""
    recipient = ""
    integrated = False
    carried_ok = False
    no_folder = False
    if carried:
        # The seat came with the terminal. Do not ask, and do not fall
        # through into the question a person answers for themselves.
        # An empty folder does not fall through to the recorded default:
        # that would be a second way for a caller to attach a name.
        seats = _seats_in(letterbox_dir)
        summary["letterbox_seen"] = bool(seats)
        usable = bool(_SEAT_NAME.fullmatch(carried)) and carried in seats
        if not usable:
            console.say("This terminal names an agent, and setup will not use it.")
            if _SEAT_NAME.fullmatch(carried):
                console.say(f"  agent: {carried}")
            else:
                console.say("  The name is not one word, so it is not shown.")
            if letterbox_dir:
                shown = pathlib.Path(letterbox_dir).expanduser()
                console.say(f"  folder: {shown}")
                console.say("That name is not a folder with an inbox there.")
            else:
                console.say("The Letterbox folder is not set, so there is nowhere to look.")
            console.say("Setup stopped. It will not ask you to choose an agent.")
            console.say("Run it again from that agent's own terminal.")
            console.say("A terminal with no agent name keeps the bridge's own folder.")
            summary["mode"] = "refused"
            summary["ring"] = "not configured"
            summary["resident"] = "not started"
            summary["has_token"] = False
            return summary
        shown = pathlib.Path(letterbox_dir).expanduser()
        mailbox = str(shown / carried)
        recipient = carried
        integrated = True
        carried_ok = True
    else:
        folder, seats = _folder_for_a_typed_name(
            letterbox_dir, recorded_dir)
        no_folder = not bool(folder)
        summary["letterbox_seen"] = bool(seats)
        typed_name = _ask_agent_name(console)
        integrated, mailbox, recipient = _join_typed_name(
            console, typed_name, folder, seats)

    # A ghost path is caught HERE, while the human is still present. The
    # runtime refuses to invent a mailbox, so a config naming one would fail at
    # first run - hours after the operator walked away believing the install
    # was done. Same loud downgrade as a blank, with the reason named.
    if integrated and mailbox and not pathlib.Path(mailbox).expanduser().is_dir():
        console.say()
        console.say(f"  {mailbox} does not exist. A folder belongs to an agent")
        console.say("  that already has one, so it will not be invented.")
        mailbox = ""

    # The letterbox helper. Not a flag, and not a question. On PATH, the
    # absolute path is recorded. Otherwise the one path the installer uses,
    # and only when the helper check accepts it. If neither is usable, this
    # run does not join: a mailbox it cannot ring is not an install.
    helper = ""
    if integrated and mailbox and recipient:
        join, helper = _resolve_helper(console, helper_on_path, helper_install)
        if not join:
            integrated = False
            mailbox = ""
            recipient = ""
            carried_ok = False
        elif carried_ok:
            summary["seat_carried"] = True
            console.say("This terminal already names an agent. Setup will use it.")
            console.say(f"  agent: {recipient}")
            console.say(f"  folder: {mailbox}")
            console.say("This run did not choose an agent, and it did not ask.")
        else:
            console.say(f"  agent: {recipient}")
            console.say(f"  folder: {mailbox}")

    if integrated and mailbox and recipient:
        summary["mode"] = "integrated"
        summary["mail_root"] = mailbox
        summary["recipient"] = recipient
    elif integrated:
        # SAY SO. Falling back silently is the original defect wearing a
        # different hat: the operator answered yes, and would leave believing
        # they had an integrated bridge.
        integrated = False
        mailbox = recipient = ""
        console.say()
        console.say("  Both the directory and the name are needed for that, and")
        console.say("  one was blank. This run keeps the bridge's own folder.")
        console.say("  Re-run init once you have both the folder and the name.")

    # Said here, after the folder and the helper, so "next" is the token
    # and not a question that still sits in between.
    if platform in ("slack", "discord"):
        token_next = "the token. Nothing is sent until you ask."
    else:
        token_next = "the token. Pasting it asks whether the platform accepts it."
    if summary["mode"] == "integrated":
        _noted(console, "messages will go to that agent's folder.", token_next)
    else:
        if no_folder:
            console.say("The bridge may stay stopped until something can tell you a message arrived.")
        _noted(console, "the bridge will keep its own folder.", token_next)

    # 3. The token. Never echoed, never an argument, never printed back.
    app_token = ""
    if platform == "slack":
        from alb.setup import slack_wizard
        slack_wizard.explain(console, recipient)
        console.say()
        token, app_token = slack_wizard.tokens(
            console, _consume_token_file, bot_token_file=token_file,
            app_token_file=app_token_file)
    elif platform == "discord":
        from alb.setup import discord_wizard
        discord_wizard.explain(console)
        console.say()
        token = discord_wizard.token(console, _consume_token_file,
                                     token_file=token_file)
        discord_wizard.invite_step(console, token)
    else:
        token = _telegram_token(console, token_file, token_check)

    env_path = root / "bridge.env"
    env_path_existed = env_path.exists()
    if env_path_existed:
        # Never clobber. Someone re-running this already has a working bridge
        # more often than not, and a silently replaced config is a morning.
        # The chat read has not happened. Keeping the file does not undo a
        # token check the paste already asked for.
        summary["kept"].append(str(env_path))
        console.say(f"  kept {env_path} - it already exists, nothing was changed")
        if integrated and not _persist_mailbox(
                console, env_path, env_path_existed, mailbox, recipient,
                helper, summary):
            summary["mode"] = "refused"
            summary["ring"] = "not configured"
            summary["resident"] = "not started"
            summary.pop("mail_root", None)
            summary["has_token"] = bool(token)
            console.say()
            console.say("Setup stopped. Your existing configuration is untouched.")
            _closing(console, root, summary)
            return summary

    # Telegram's read can refuse the token. Ask before the file is created,
    # so a token the platform has just refused is never what gets saved.
    # Slack and Discord collect their chats after the file, as before.
    telegram_chats = None
    if platform not in ("slack", "discord"):
        ask_again = None
        if not env_path_existed and not token_file:
            checker = _token_checker(token_check)

            def ask_again():
                return _prompt_telegram_token(console, checker)
        telegram_chats, token = _chat_ids(
            console, token, chat_id_reader, ask_again)

    if not env_path_existed:
        lines = [f"ALB_TOKEN={token}\n"]
        if platform == "slack":
            lines = ["ALB_PLATFORM=slack\n", f"ALB_TOKEN={token}\n",
                     f"ALB_APP_TOKEN={app_token}\n"]
        elif platform == "discord":
            lines = ["ALB_PLATFORM=discord\n", f"ALB_TOKEN={token}\n"]
        if mailbox:
            lines.append(f"ALB_MAIL_ROOT={mailbox}\n")
        if recipient:
            lines.append(f"ALB_TO={recipient}\n")
        if helper:
            lines.append(f"ALB_BUS_BINARY={helper}\n")
        _write_private(env_path, "".join(lines))
        summary["created"].append(str(env_path))
        # New file: persist returns immediately. The keys are in the write
        # above. The call stays so a future edit cannot skip the check.
        if integrated and not _persist_mailbox(
                console, env_path, False, mailbox, recipient, helper, summary):
            summary["mode"] = "refused"
            summary["ring"] = "not configured"
            summary["resident"] = "not started"
            summary.pop("mail_root", None)
            summary["has_token"] = bool(token)
            console.say()
            console.say("Setup stopped. Your existing configuration is untouched.")
            _closing(console, root, summary)
            return summary

    summary["has_token"] = bool(token) or env_path_existed

    # 4. The allowlist. Written deny-all whatever else happens; an entry is
    #    added only from a value the operator supplied or explicitly asked us
    #    to read.
    if platform == "slack":
        chats = slack_wizard.pairs(console, token, app_token, slack_call)
    elif platform == "discord":
        chats = discord_wizard.pairs(console, token, discord_call)
    else:
        chats = telegram_chats

    allow_path = root / "allowlist.json"
    if allow_path.exists():
        summary["kept"].append(str(allow_path))
        console.say(f"  kept {allow_path} - it already exists, nothing was changed")
    else:
        _write_private(allow_path, json.dumps({"chats": chats}, indent=2) + "\n")
        summary["created"].append(str(allow_path))
        if not chats:
            console.say(f"  wrote {allow_path} denying everyone.")
            console.say("  NOTHING IS DELIVERED until a chat id is in it.")
    # READ THE FILE THAT WILL BE READ. Taking this from the answers given this
    # run is wrong in both directions on a re-run: a kept deny-all looked
    # deliverable because setup had just read an id it did not save, and a
    # kept, populated allowlist was warned about as deny-all because this run
    # took the route that reads nothing. The gate that matters is the one on
    # disk after keep-or-write. Unreadable counts as denying, because a file
    # we cannot parse is not evidence that anything gets through.
    summary["delivers"] = _allowlist_delivers(allow_path)
    _noted(console, "the files are written.",
           "how you will be told that a message arrived.")

    # 5. The ring.
    #
    #    INTEGRATED MODE ALREADY HAS ONE. The letterbox helper resolves the
    #    recipient's registered pane itself, and `run.py` rings through it
    #    whether or not a surface is set - it asks for ALB_SURFACE only when
    #    NOT integrated. So offering the pane list here would have the
    #    operator pin a surface nothing ever reads, which is the exact thing
    #    the runtime's own comment warns against.
    #
    #    Getting this wrong cost more than a wasted question: the resident
    #    offer below gated on a PANE being configured, so a correct
    #    integrated install was told "a poller with nothing to ping is not an
    #    install" and refused a start - while two seats on this fleet ran
    #    that configuration with a working doorbell. The wizard contradicted
    #    the runtime. Found by installing on the maintainer's own seat.
    if integrated:
        console.say()
        console.say("A new message will be announced through the folder")
        console.say("you named, to:")
        console.say(f"  {recipient}")
        console.say("There is no pane to pick.")
        summary["ring"] = "helper"
    else:
        # Listed, never chosen - but the operator may paste the id HERE, so
        # the env carries it before the resident offer loads that env.
        # (Found by codex's consistency review: the old order started a
        # resident whose ring stayed disabled until a restart nobody
        # mentioned.)
        surface, notifier = _offer_ring(console, panes, summary)
        if surface:
            _persist_ring(console, env_path, env_path_existed, surface,
                          notifier, summary)

    if summary.get("ring") == "helper":
        _noted(console, "a new message is announced through the folder you named.",
               "whether to start the bridge.")
    elif summary.get("ring") == "configured":
        _noted(console, "a new message will be typed into the pane you chose.",
               "whether to start the bridge.")
    else:
        _noted(console, "nothing will announce a new message yet.",
               "the bridge stays stopped until that is set.")

    # 6. The resident. Both real installs stalled at "--once looks fine" with
    #    nothing left running - so init finishes the job, or prints exactly
    #    what remains.
    _offer_resident(console, root, summary,
                    cmux_born or _cmux_born,
                    bridge_running or _bridge_running,
                    start_pane or _start_pane,
                    command=resident_command)

    _closing(console, root, summary)
    return summary


def _helper_absolute(path):
    """The path setup actually checked, not a name a later shell resolves."""
    checked = pathlib.Path(path).expanduser()
    if not checked.is_absolute():
        checked = pathlib.Path.cwd() / checked
    return str(checked)


def _token_was_refused(exc):
    """401 and 404 from this platform mean the token is not a bot token.

    Anything else is a read failure. The exception text is never shown: the
    request URL is built with the token in it, and some errors quote that URL.
    """
    code = getattr(exc, "code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    return code in (401, 404)


def _noted(console, done, nxt):
    """Say that a step finished, and name the next one.

    No "step 2 of 5". The path branches (a folder, a helper, a platform,
    panes), so a fixed total would be a lie on some of those paths.
    """
    console.say(f"  Done: {done}")
    console.say(f"  Next: {nxt}")


def _token_checker(token_check):
    """The paste's platform check. Tests pass their own. The real one is getMe."""
    if token_check is not None:
        return token_check

    def real(token):
        from alb.setup import discover
        discover.check_token(token)

    return real


def _platform_accepts(console, token, checker):
    """"accepted", "refused", or "unchecked".

    A 401 or 404 is a refusal and is not saved. Anything else is a failure
    to ask: not a refusal, and not accepted. The exception text is never
    shown, because a failure line can quote the request URL, and that URL
    contains the token.
    """
    while True:
        try:
            checker(token)
        except Exception as exc:  # noqa: BLE001 - classified, never printed
            if _token_was_refused(exc):
                console.say("  Refused. The platform did not accept that token.")
                console.say(f"  ({len(token)} characters). It was not saved.")
                return "refused"
            console.say("  Not accepted. The platform could not be asked.")
            console.say("  This is not a refusal. The details are not shown,")
            console.say("  because a failure line can contain the token.")
            if _yes(console.ask("  try the check again? [y/N]", "n")):
                continue
            console.say("  The token was not saved.")
            return "unchecked"
        console.say("  Accepted. The platform accepted that token")
        console.say(f"  ({len(token)} characters). It is not shown again.")
        return "accepted"


def _prompt_telegram_token(console, checker):
    """Ask until the platform accepts one token, or the operator leaves it blank.

    Echo stays off. The shape check is local. Accepted means the platform
    said so. A paste that is not one token is not sent.
    """
    while True:
        token = console.ask_secret("  token (not echoed): ").strip()
        if not token:
            # Say it NOW, not at first run. The file is still written - the
            # rest of the boilerplate is real either way - but the operator
            # leaves knowing exactly what is missing rather than discovering
            # it at 3am.
            console.say("  no token entered. The file is written without one, and the")
            console.say("  bridge will refuse to start until ALB_TOKEN is filled in.")
            console.say("  Next: the chat question. A read cannot succeed without a token.")
            return ""
        if _TELEGRAM_TOKEN.fullmatch(token):
            if _platform_accepts(console, token, checker) == "accepted":
                console.say("  Next: choose whether I ask the platform for your chat,")
                console.say("  or you look that up yourself.")
                return token
            console.say("  Paste the token once, then press enter.")
            continue
        console.say(f"  that does not look like one token ({len(token)} characters).")
        console.say("  A bot token is digits, a colon, and about 35 characters.")
        console.say("  Nothing is shown as you paste. Paste it once, then press enter.")


def _telegram_token(console, token_file, token_check):
    checker = _token_checker(token_check)
    console.say()
    console.say("Bot token from @BotFather. It is written to a mode-600 file and")
    console.say("not shown again. If this bot existed before, revoke and re-issue")
    console.say("the token first - one consumer per token is enforced by the")
    console.say("platform, and an old token cannot be proven unused.")
    if token_file:
        token = _consume_token_file(token_file)
        if not _TELEGRAM_TOKEN.fullmatch(token):
            # Same rule as a paste. The file is already consumed. The message
            # carries the count only: the text is a secret, and this line is
            # what the command prints.
            raise SetupError(
                "the token file does not look like one token "
                f"({len(token)} characters)")
        verdict = _platform_accepts(console, token, checker)
        if verdict == "refused":
            raise SetupError(
                "the platform did not accept the token "
                f"({len(token)} characters)")
        if verdict != "accepted":
            raise SetupError(
                "the platform could not be asked about the token "
                f"({len(token)} characters). It was not saved.")
        console.say("  token read from file and the file was removed.")
        console.say("  Next: choose whether I ask the platform for your chat,")
        console.say("  or you look that up yourself.")
        return token
    console.say("  Nothing will appear as you paste. Paste the token once,")
    console.say("  then press enter.")
    return _prompt_telegram_token(console, checker)


def _cmux_born():
    """Is THIS process inside cmux? Pane creation obeys the same born-inside
    ACL as the ring, so an offer made from a plain terminal is an offer that
    cannot succeed - the precondition is checked before the question exists."""
    return bool(os.environ.get("CMUX_SOCKET_PATH") or
                os.environ.get("CMUX_SOCKET_CAPABILITY"))


def _bridge_running(root):
    """Is a bridge already holding this root's lock? The flock answers the
    question re-run safety actually asks; detecting a pane by its title would
    be the mailbox detector in a trench coat, and stays refused."""
    import fcntl
    lock_path = pathlib.Path(root) / "bridge.lock"
    if not lock_path.exists():
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _start_pane(title, command):
    """Create the dedicated pane. Returns an identifier for the report."""
    import subprocess
    result = subprocess.run(
        ["cmux", "workspace", "create", "--name", title, "--command", command],
        capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "cmux refused")
    return result.stdout.strip() or "created"


def _allowlist_delivers(path):
    """Would the gate on disk let anything through?

    Asks the gate. Answering it here meant two definitions of an allowlist,
    and they disagreed: a truthiness test read `{"chats": "42"}` as
    permissive where the gate requires a non-empty list, so setup offered to
    start a bridge that would deny everyone.
    """
    from alb.allowlist import gate
    return gate.permits_anyone(path)


# The one launcher, and the one way it is asked about. `-I` is isolated mode:
# PYTHONPATH is ignored, the user site directory is ignored, and the script's
# directory is not prepended to sys.path. Everything that could substitute a
# different alb at launch is switched off, and the probe below runs under the
# SAME flag - which is the whole point. Proving identity under isolation while
# launching without it proved something about a context the launch never uses.
_ISOLATED = "-I"


def _origin_for(executable):
    """WHERE that interpreter's alb comes from under the launch conditions.

    Importability was the first version of this question and it was the wrong
    one: it establishes that SOME alb is reachable, not that it is the one
    running setup. An environment holding an older copy answers yes, and the
    command then starts that older copy.

    Asking it under different conditions from the launch was the second
    mistake, and a subtler one. This stripped PYTHONPATH and ran from `/`
    while the generated command enforced neither, so a shell whose PYTHONPATH
    named another alb - or whose working directory sat beside one - launched
    that one instead, having been approved on evidence from a context that
    never existed.
    """
    import subprocess
    # No environment surgery here: `-I` implies `-E`, so PYTHON* variables are
    # already ignored, and `-c` under isolation does not put the working
    # directory on the path either. Stripping PYTHONPATH by hand as well was
    # dead code - the mutation gate proved it, by disabling it and finding
    # nothing that could tell. Defence in depth that no test can distinguish
    # from its own absence is the hollow-pin problem wearing a helmet.
    try:
        result = subprocess.run(
            [executable, _ISOLATED, "-c",
             "import alb, sys; sys.stdout.write(alb.__file__ or '')"],
            cwd="/", capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _same_installation(origin):
    """Is that the alb running this setup? Compared by resolved location.

    Version equality would not do it: two builds can share a version string
    and differ, and the thing being promised is that the resident is THIS
    code, not code that agrees about its name.
    """
    import alb
    ours = getattr(alb, "__file__", None)
    if not origin or not ours:
        return False
    try:
        return pathlib.Path(origin).resolve() == pathlib.Path(ours).resolve()
    except OSError:
        return False


def _prefix_letterbox_dir(command, summary):
    """Put the mailbox's parent on the start line, when there is a mailbox.

    The helper reads LETTERBOX_DIR from the bridge process. Deriving it from
    the mailbox the operator just named means the printed command is the one
    that rings, not a command that finds whichever letterbox is installed.
    """
    if not command:
        return command
    mail = summary.get("mail_root") or ""
    if not mail:
        return command
    parent = pathlib.Path(mail).expanduser().parent
    import shlex
    assignment = "LETTERBOX_DIR=" + shlex.quote(str(parent))
    if str(command).startswith("LETTERBOX_DIR="):
        return command
    return f"{assignment} {command}"


def _resident_command(root, origin_of=None, executable=None):
    """The command that starts THIS installation, or None if there isn't one.

    It used to be the bare word `alb`, handed to a new shell. A shell resolves
    that from PATH, so an operator who installed into a dedicated venv and ran
    init from it got a resident running whichever copy PATH found first - a
    different installation, a possibly different version, and on a machine
    with several relays, one shared with somebody else. The resolution IS the
    defect, so nothing here is left to resolve.

    Two things this got wrong on the first attempt, both found by executing
    the result in a real shell rather than reading it:

    - Built by interpolation, a path containing a space became two arguments.
      A root with a space was rejected by argparse; an interpreter with one
      failed to execute at all. Both are ordinary on a machine where the home
      directory is a person's name. The parts are quoted as one argv now.

    - `<interpreter> -m alb` was offered wherever no console script existed.
      For a source checkout that command fails in a fresh shell - this process
      can import alb because of the path it was started with, and a new shell
      inherits none of it. An autostart that reliably fails is worse than no
      offer, so where no command can be named that a fresh shell will resolve,
      this returns None and the offer is declined with a reason.
    """
    import shlex
    import sys
    exe = executable or sys.executable
    origin = (origin_of or _origin_for)(exe)

    if not _same_installation(origin):
        return None

    # ONE LAUNCHER, ISOLATED. The console script was friendlier to read and
    # could not carry this guarantee: it is a shebang into an interpreter that
    # will still honour PYTHONPATH, the user site, and its own directory. A
    # script beside the interpreter proved proximity, never provenance.
    argv = [exe, _ISOLATED, "-m", "alb"]

    root = str(root)
    return shlex.join(argv + ["--config", f"{root}/bridge.env", "--root", root])


def _offer_resident(console, root, summary, cmux_born, bridge_running,
                    start_pane, command=_UNSET):
    """Finish the install, or hand over exactly what remains.

    The printed command and the pane's command are the same bytes, so both
    paths converge on the same running bridge. Consent is to a named thing:
    the command and the title are shown before the question is asked, and the
    report names what started and how to stop it - a daemon the operator owns
    but cannot find is what costs trust, not a started process.
    """
    command = _resident_command(root) if command is _UNSET else command
    command = _prefix_letterbox_dir(command, summary)
    if isinstance(command, str) and command:
        summary["start_command"] = command
    if command is None:
        # No command a fresh shell would resolve to THIS install.
        # Declining is the honest answer: a pane that exits
        # immediately looks like a product fault, and a pane
        # running somebody else's copy is worse.
        console.say()
        console.say("This installation has no launcher a new shell")
        console.say("would resolve to it, so the bridge will not be")
        console.say("started from here. Install it with pipx or into")
        console.say("a venv, then start it by its absolute path.")
        summary["resident"] = "unsupported"
        return
    title = f"\U0001F4EE {pathlib.Path(root).name} bridge \u2014 DO NOT CLOSE"

    console.say()
    if bridge_running(root):
        console.say("A bridge for this root is already running - nothing to start.")
        summary["resident"] = "already running"
        return

    console.say("The bridge only delivers while it is running. The command:")
    console.say()
    console.say(f"  {command}")
    console.say()

    if summary.get("ring") not in RINGS:
        # Simon: a full install is not an install without the bell. Starting
        # a poller here would succeed bell-less and --status would say
        # disabled, not broken. That is grok's install.
        console.say("The bridge will not be started, because nothing is set up to tell you a message arrived.")
        if summary.get("letterbox_seen"):
            console.say("This is not set up for an agent by name.")
            console.say("Re-run init and give that agent's exact name.")
            console.say("A cmux pane is only for a bridge that keeps its own")
            console.say("folder.")
        else:
            console.say("Re-run init from a cmux pane, paste your pane id")
            console.say("when asked, then start.")
        summary["resident"] = "incomplete"
        return

    if not cmux_born():
        # THE ADVICE HAS TO MATCH THE RING. Automatic pane creation exists
        # only for cmux, and this message once generalised that into a claim
        # about where the bridge may run at all - telling a tmux operator to
        # adopt a multiplexer they do not need, and an integrated one to worry
        # about panes that play no part in their bell.
        if summary.get("ring") == "helper":
            # NOT a broker. The helper is executed in the BRIDGE's context, so
            # whatever it needs, the bridge needs - and a helper that reaches
            # a cmux pane still meets cmux's born-inside rule. Saying the
            # location does not matter sells an operator a service unit that
            # delivers mail silently forever.
            console.say("Run that in a context your doorbell helper supports.")
            console.say("The helper runs from here, so whatever it needs, this")
            console.say("needs: if it reaches a cmux pane, this has to be")
            console.say("started inside cmux too.")
            console.say("Then check the bell in two steps, because they are")
            console.say("two different facts: `alb --status` reports what the")
            console.say("helper RETURNED, and watching the pane is the only way")
            console.say("to see that the line arrived where you meant it to.")
        elif (summary.get("notifier") or "").strip().lower() == "tmux":
            console.say("Run that in a tmux pane you leave open, or from your")
            console.say("service manager. tmux does not care which pane you")
            console.say("start it from, so a message still shows up either way.")
        else:
            # Real, and only here: cmux refuses a pane created from outside it.
            console.say("Run it from inside a cmux pane (cmux refuses processes")
            console.say("born outside it, so started from here the bell would not")
            console.say("work).")
        summary["resident"] = "printed"
        return

    console.say(f'It can start now, in its own cmux pane titled "{title}".')
    # A BRIDGE THAT CAN RECEIVE NOTHING IS NOT A RUNNING BRIDGE, and starting
    # one by default is how an operator ends up sending test messages into a
    # correctly-working deny-all and finding no error anywhere to explain it.
    # The gate is not weakened; the DEFAULT is. Starting in this state stays
    # available to anyone who means it, and stops being what enter does.
    if not summary.get("delivers", True):
        console.say()
        console.say("  NOTE: the allowlist currently denies everyone, so a")
        console.say("  bridge started now would poll correctly and deliver")
        console.say("  nothing - your own messages included, with no error.")
        console.say("  Add your chat id first unless you have a reason not to.")
        default = "n"
        prompt = "  start it anyway, with an empty allowlist? [y/N]"
    else:
        default = "y"
        prompt = "  start the bridge now in its own cmux pane? [Y/n]"
    # An empty answer means "the default", whatever the default currently is -
    # which now differs between the two states above. Reading blank as "no"
    # would have made enter refuse a start it was offering to make.
    answer = (console.ask(prompt, default).strip() or default)
    # Only an explicit yes starts it. A stray answer typed or buffered for an
    # earlier question (an agent driving setup sent a user id here) counted
    # as yes, and started a bridge the operator had declined.
    if answer.lower() not in ("y", "yes"):
        console.say("Not started. Run the command above from a cmux pane when ready.")
        summary["resident"] = "printed"
        return

    try:
        surface = start_pane(title, command)
    except Exception as exc:  # noqa: BLE001 - degrade to the printed path, never half-start
        console.say(f"  could not start it: {exc}")
        console.say("  run the command above from a cmux pane instead.")
        summary["resident"] = "printed"
        return

    console.say(f"  started: {surface}")
    console.say("  To stop it: close that pane. It holds this root's lock, so")
    console.say("  a second bridge for the same root refuses to start.")
    summary["resident"] = "started"



def _chat_ids(console, token, reader, ask_again=None):
    """Ask how the operator wants their chat id obtained. Both routes are real.

    One answer contacts the platform. The other does not. That is why this
    is asked. The words are read and print. read is recommended, and enter
    takes it. print does not contact the platform.

    Reading it removes a genuine trap: the payload contains a `from` id beside
    the `chat` id, they are identical in a direct message, and the wrong one
    denies everything the first time a group is used - silently.

    Printing the command keeps the poll off. On Telegram the token check
    already happened when there was a token. The printed command keeps a
    placeholder. The token is never copied into it.

    Returns (chat ids, token). The token may be empty when the platform
    refused the one that was offered and it must not be saved.

    The choice is the operator's. Neither is the safe answer for everyone.
    """
    console.say()
    console.say("Your chat has to be allowed, or your messages will not arrive.")
    console.say("Two ways. One contacts the platform. The other does not.")
    console.say("  read  - recommended. One getUpdates check. It consumes nothing and")
    console.say("          leaves your messages queued.")
    console.say("  print - the command is printed, and nothing is asked from here.")
    choice = console.ask("  read or print", "read").strip().lower()
    if choice in {"look", "lookup", "look it up"}:
        choice = "read"

    if choice != "read" or reader is None:
        console.say()
        console.say("  Done: I did not ask the platform for a chat.")
        if token:
            console.say("  The token check already happened. This step does not poll.")
        else:
            console.say("  This step does not poll.")
        console.say("  Next: run the command below, then put the number in")
        console.say("  the allowlist.")
        console.say()
        console.say("Run this, then put the number in allowlist.json:")
        console.say()
        console.say(CHAT_ID_COMMAND)
        console.say()
        return [], token

    while True:
        console.say()
        console.say("Send your bot a message now, then press enter.")
        console.ask("  ready", "")

        try:
            found = reader(token)
        except Exception as exc:  # noqa: BLE001 - any failure falls back, never crashes setup
            if _token_was_refused(exc):
                # 401/404 is the token. Say so, and do not save it. A generic
                # "could not read it" made a wrong token look like success.
                console.say("  that token is not recognised. The platform")
                console.say("  answered 401 or 404. It was not saved.")
                if ask_again is None:
                    console.say("  An existing config was left as it was.")
                    return [], ""
                console.say("  Paste the token once, then press enter.")
                token = ask_again()
                if not token:
                    return [], ""
                continue
            # Any other failure: do not print the exception. Its text can
            # quote the request URL, and that URL contains the token.
            console.say("  could not read it. The details are not shown,")
            console.say("  because a failure line can contain the token.")
            console.say("  This was not a rejected token. Run this instead,")
            console.say("  and put the token only where the placeholder is:")
            console.say(CHAT_ID_COMMAND)
            return [], token

        if not found:
            console.say("  Done: the platform accepted this token. No chat came back.")
            console.say("  Next: message the bot, then try this read again.")
            console.say("  nothing came back. That usually means the bot has not")
            console.say("  been messaged yet, or another process is polling it.")
            if _yes(console.ask("  try the read again? [y/N]", "n")):
                continue
            console.say("  No chat id was saved.")
            console.say("  The allowlist will deny everyone until one is added.")
            console.say("  Run this when you have messaged the bot:")
            console.say(CHAT_ID_COMMAND)
            return [], token

        console.say("  Done: the platform accepted this token.")
        console.say("  Next: pick which chat is you.")
        console.say()
        for index, entry in enumerate(found, start=1):
            console.say(f"  {index}) {entry['chat_id']}  {entry.get('label', '')}".rstrip())
        console.say()
        picked = console.ask("  which one is you (number, or blank for none)", "").strip()

        # Blank, a word, an out-of-range number: all mean no entry. An allowlist
        # written from a value we did not understand is the failure this command
        # exists to avoid.
        if not picked.isdigit():
            return [], token
        index = int(picked)
        if not 1 <= index <= len(found):
            return [], token
        return [str(found[index - 1]["chat_id"])], token


class UnreadableConfig(Exception):
    """The file exists and the loader refuses it. Not the same as empty."""


def _effective(env_path):
    """What the RUNTIME would read, not what the file happens to contain.

    A commented line contains "ALB_SURFACE=" and configures nothing; an empty
    assignment contains it and configures nothing. A substring check answers a
    question about shape when the question is about meaning, so this parses
    with the same loader the bridge uses and setup cannot disagree with
    runtime about what is set.

    A loader REFUSAL raises rather than returning empty. Treating "I cannot
    read this" as "there is nothing here" is how a duplicate key gets appended
    to a config whose permissions are merely wrong - and when those are
    repaired, the effective pin has silently changed underneath the operator.
    """
    from alb.bridge import run

    try:
        return run.load_config(env_path)
    except Exception as exc:  # noqa: BLE001 - any refusal, for the same reason
        raise UnreadableConfig(str(exc)) from None


def _append_settings(env_path, lines):
    """Add settings to an existing config without joining onto the last one.

    A file whose final line lacks its newline turns the next line into a
    continuation of that value - and the value at the end of this particular
    file is usually the token, so a careless append changes the one secret in
    it while reporting success. Found by review, and the reason this is one
    function rather than two copies: the second copy would not have this
    comment, and would eventually not have the guard either.
    """
    if not lines:
        return
    try:
        existing = env_path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    with open(env_path, "a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.writelines(lines)


def _persist_mailbox(console, env_path, env_path_existed, mailbox, recipient,
                     helper, summary):
    """Add the integrated keys to a config that already exists, or say so.

    Non-clobber protects what is THERE; declining to add what is ABSENT is
    how a re-run answered "yes, integrated" and got a standalone config back
    while the summary reported integrated. Same shape as `_persist_ring`,
    including the two rules that one was written to keep: an append must not
    JOIN onto a last line missing its newline, and the mode is claimed only
    after re-reading the file and finding it true.

    Returns True when the config really is integrated afterwards.
    """
    if not env_path_existed:
        # init wrote the keys itself a moment ago; nothing to reconcile.
        return True
    try:
        config = _effective(env_path)
    except UnreadableConfig as exc:
        console.say(f"  bridge.env exists but cannot be read as config: {exc}")
        console.say("  Not writing to it. Fix the file, then re-run.")
        return False

    # THE ROUTE IS A PAIR. Applied per key, append-if-absent synthesises a
    # destination nobody chose: a config naming an old participant and no
    # mailbox, re-run with a new pair, kept the old name and took the new
    # directory. Mail then goes to a participant the operator did not select.
    #
    # So retained values are usable only when the pair is empty, or when it
    # already says exactly what was just selected. Anything else is refused
    # with both values named and nothing written - non-clobber intact, and
    # the operator left able to see what disagrees.
    kept_mailbox = config.get("ALB_MAIL_ROOT") or ""
    kept_recipient = config.get("ALB_TO") or ""
    for label, kept, chosen in (("mailbox", kept_mailbox, mailbox),
                                ("participant", kept_recipient, recipient)):
        if kept and chosen and kept != chosen:
            console.say(f"  bridge.env already names a {label}: {kept}")
            console.say(f"  and you selected: {chosen}")
            console.say("  A mailbox and a participant are one route, so this")
            console.say("  will not take half of each. Nothing was written.")
            console.say("  Edit bridge.env yourself, or start a new root.")
            return False

    lines = []
    for key, value in (("ALB_MAIL_ROOT", mailbox), ("ALB_TO", recipient),
                       ("ALB_BUS_BINARY", helper)):
        if value and not config.get(key):
            lines.append(f"{key}={value}\n")
    _append_settings(env_path, lines)

    try:
        after = _effective(env_path)
    except UnreadableConfig:
        return False
    if after.get("ALB_MAIL_ROOT") and after.get("ALB_TO"):
        return True
    console.say("  bridge.env still has no mailbox and recipient, so this is")
    console.say("  NOT an integrated install. Nothing was overwritten.")
    return False


def _persist_ring(console, env_path, env_path_existed, surface, notifier,
                  summary):
    """Write the ring into the env, or refuse to call it configured.

    Non-clobber protects what is THERE; it must not decline to add what is
    absent. And an append must not JOIN: a file whose last line lacks a
    newline turns the next line into a continuation of the token's value,
    changing the one secret in the file while reporting success.
    """
    try:
        config = _effective(env_path) if env_path_existed else {}
    except UnreadableConfig as exc:
        # Stop, do not append. The bytes stay exactly as they are.
        console.say(f"  bridge.env exists but cannot be read as config: {exc}")
        console.say("  Not writing to it. Fix the file, then re-run.")
        summary["ring"] = "not configured"
        return

    lines = []
    retained_surface = config.get("ALB_SURFACE")
    if not retained_surface:
        lines.append(f"ALB_SURFACE={surface}\n")
    else:
        console.say("  bridge.env already names a surface; leaving it alone. "
                    "Edit it yourself if that pane is wrong.")

    # A notifier describes the surface that will actually be used. If we are
    # RETAINING someone else's surface, the pane just selected says nothing
    # about its type - so labelling the retained one from the new selection
    # would save a pair that cannot both be true.
    if notifier and not config.get("ALB_NOTIFIER") and not retained_surface:
        lines.append(f"ALB_NOTIFIER={notifier}\n")

    _append_settings(env_path, lines)

    try:
        after = _effective(env_path)
    except UnreadableConfig:
        summary["ring"] = "not configured"
        return
    chosen = (after.get("ALB_NOTIFIER") or "").strip().lower()
    if not after.get("ALB_SURFACE"):
        summary["ring"] = "not configured"
    elif retained_surface and notifier and (chosen or "cmux") != notifier:
        console.say(f"  bridge.env keeps {retained_surface}, which is a "
                    f"{chosen or 'cmux'} surface, and you picked a {notifier} "
                    f"pane. Those cannot both be the ring. Fix the env or pick "
                    f"a {chosen or 'cmux'} pane.")
        summary["ring"] = "not configured"
    elif notifier and chosen and chosen != notifier:
        console.say(f"  bridge.env says ALB_NOTIFIER={chosen}, and that pane "
                    f"is a {notifier} pane. Those cannot both be true, so the "
                    f"ring is not configured. Fix the env or pick a "
                    f"{chosen} pane.")
        summary["ring"] = "not configured"


def _offer_ring(console, panes, summary):
    """List panes if we were given any; accept the id the operator pastes.

    Listing without choosing still holds: the operator supplies the id, and a
    single pane in the listing is still not a choice init may make. Returns
    the pasted id (or "") so the caller writes it before anything loads the
    env. Blank is not a skip. Simon: there is no supported bell-less install.
    """
    console.say()
    console.say("When a message arrives, this can type one line into a")
    console.say("terminal pane so you notice it. Without that, the message")
    console.say("is saved and nothing tells you. That is not an install.")

    if panes:
        console.say("Panes I can see:")
        for entry in panes:
            where = entry.get("notifier") or ""
            tag = f"[{where}] " if where else ""
            console.say(f"  {entry['id']}  {tag}{entry.get('label', '')}".rstrip())
        console.say("I will not pick one. This list does not say which pane")
        console.say("is yours, and a line in the wrong pane shows up in")
        console.say("someone else's work. Paste the id of your pane.")
        surface = console.ask("  your pane id", "").strip()
        if not surface:
            console.say("  no pane id. Nothing is set up to tell you a message arrived.")
            return "", ""
        for entry in panes:
            if entry.get("id") == surface:
                summary["ring"] = "configured"
                notifier = entry.get("notifier") or ""
                summary["notifier"] = notifier
                return surface, notifier
        console.say("  that id is not in the list above - re-run to refresh.")
        return "", ""

    if summary.get("letterbox_seen"):
        console.say("No panes are visible, so this cannot type a line into a pane.")
        console.say("An agent already in that folder can be used by that exact name.")
        console.say("A pane id is only for a bridge that keeps its own folder.")
        return "", ""

    console.say("No panes are visible, so this cannot be set up yet.")
    console.say("Run init again from a terminal that has at least one pane,")
    console.say("then paste your pane id. Editing the config later")
    console.say("is not a supported install.")
    return "", ""


def _closing(console, root, summary):
    console.say()
    if summary["created"]:
        console.say("Created:")
        for path in summary["created"]:
            console.say(f"  {path}")
    if summary["kept"]:
        console.say("Kept as they were:")
        for path in summary["kept"]:
            console.say(f"  {path}")
    console.say()
    console.say("Next:")
    console.say(f"  alb --doctor --root {root}")
    console.say("     says whether the allowlist is why nothing is arriving.")
    console.say(f"  alb --config {root}/bridge.env --root {root} --once")
    console.say("     one cycle. Reports fetched / published / denied.")
    console.say()
    console.say("Optional, and not required: have someone NOT on the")
    console.say("allowlist message the bot. Expect nothing to arrive and no")
    console.say("error. That silence is the gate working. You do not need")
    console.say("a second person to finish.")
    console.say()
    if summary.get("has_token"):
        console.say("To finish: message the bot yourself, from the account")
        console.say("you allowlisted. Then start the bridge and leave it running:")
    else:
        console.say("To finish: put a token in bridge.env. The bridge refuses")
        console.say("to start until that is there. Then message the bot yourself")
        console.say("and leave the bridge running:")
    command = summary.get("start_command") or ""
    if command:
        console.say(f"  {command}")
    else:
        console.say(f"  alb --config {root}/bridge.env --root {root}")
    console.say()
    recipient = summary.get("recipient") or ""
    if summary.get("mode") == "integrated" and recipient:
        console.say(f"This is set up for {recipient}.")
        console.say("Messages go to that agent's folder.")
    else:
        console.say("This is not set up for an agent by name.")
        if summary.get("letterbox_seen"):
            console.say("Nothing names who the messages are for.")
            console.say("Re-run init and give the exact name of an agent already there.")
        else:
            console.say("Nothing names who the messages are for.")
    console.say()
    if summary.get("resident") == "started":
        console.say("The bridge process is running. That is not the same as working.")
    elif summary.get("resident") == "already running":
        console.say("A bridge for this folder is already running. That is not")
        console.say("the same as working.")
    else:
        console.say("The bridge is not working yet. These files do not run it.")
    console.say("A reply typed in the terminal stays in the terminal.")
    console.say("The phone hears the answer only when alb --reply-to sends it.")
    console.say("You will know it is working when it is running, a message")
    console.say("from you shows up, and the answer comes back on the phone.")
    console.say("Until then, it is not.")
