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

  - It will not reach the platform unless the operator asks it to, in that
    moment, having been told what the call is. Anyone who declines can be told
    truthfully that setup never touched the network.

  - It will not overwrite anything. Re-running this on a working bridge must
    not silently disarm it: an allowlist reset to deny-all reads exactly like a
    dead bot, which is the failure this whole design is organised against.

  - It will not choose a pane. A listing cannot say which pane holds the agent
    the operator meant, and a ring typed into the wrong pane lands in somebody
    else's session.

  - It will not detect a mailbox. Kimi's detect-and-ask is the right shape and
    the detector is the problem: the only mailbox layouts we know are this
    team's, so shipping a detector would either encode our topology in a public
    repo or be confidently wrong on a stranger's machine. The operator is asked
    for a path instead. Same one-key experience, nothing invented.
"""
import json
import os
import pathlib
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


def init(root, console, chat_id_reader=None, panes=None, helper_found=None,
         cmux_born=None, bridge_running=None, start_pane=None,
         resident_command=_UNSET, token_file=None):
    """Create the boilerplate under `root`, asking for what cannot be derived.

    `console` supplies say / ask / ask_secret, so the questions are testable
    and so the secret prompt is a distinct call rather than a convention.

    Note the absence of a `token` parameter: it is asked for, never passed.
    A signature that accepted one would grow a --token flag, and a flag is
    shell history. `--token-file` is the agent-driven path: a 0600 file is
    read and deleted; the token string never appears in argv.
    """
    root = pathlib.Path(root).expanduser()
    summary = {"mode": "standalone", "created": [], "kept": [], "ring": "not configured"}

    console.say("alb init - creates the files, asks for the rest.")
    console.say("Nothing here is sent anywhere unless you ask for it explicitly.")
    console.say()

    # 1. The directory. 0700 stated, not inherited: a directory created under
    #    the default umask is 0755, and this one accumulates a canary log naming
    #    chats, an offset describing traffic, and dead letters quoting failures.
    existed = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, DIR_MODE)
    summary["kept" if existed else "created"].append(str(root))

    # 2. Standalone or integrated. Asked, because the answer is about intent
    #    and no filesystem check can see intent.
    #
    #    SMALL QUESTION FIRST. This was one prompt carrying mode, path and name
    #    at once, and it failed on the first integrated install: the operator
    #    had already decided on integrated, ran this, and got a standalone
    #    config because the prompt did not read as the thing they had decided.
    #    Three keys were then added by hand. Nothing errored at any point.
    #    A question that can be answered without knowing a path gets asked
    #    first; the path is only wanted once the answer is yes.
    console.say("Does this agent already receive mail from other agents on")
    console.say("this machine - an inbox it already sweeps?")
    console.say("  yes - letters go there, and the doorbell is the doorbell it")
    console.say("        already knows. Nothing new for it to learn.")
    console.say("  no  - this bridge gets its own directory. Safe default.")
    integrated = _yes(console.ask("  y/N", "n"))

    mailbox = ""
    recipient = ""
    if integrated:
        console.say()
        console.say("The directory that CONTAINS that inbox - not the inbox")
        console.say("itself. Example: ~/mail/agents/<agent-name>")
        mailbox = console.ask("  mailbox directory", "").strip()

        console.say()
        console.say("The doorbell is addressed by name, so this has to be the")
        console.say("name the agent is registered under, not a display name.")
        console.say("Example: research-bot")
        recipient = console.ask("  participant name", "").strip()

    # A ghost path is caught HERE, while the human is still present. The
    # runtime refuses to invent a mailbox, so a config naming one would fail at
    # first run - hours after the operator walked away believing the install
    # was done. Same loud downgrade as a blank, with the reason named.
    if integrated and mailbox and not pathlib.Path(mailbox).expanduser().is_dir():
        console.say()
        console.say(f"  {mailbox} does not exist. A mailbox belongs to an agent")
        console.say("  that already has one, so it will not be invented.")
        mailbox = ""

    if integrated and mailbox and recipient:
        summary["mode"] = "integrated"
        summary["mail_root"] = mailbox
    elif integrated:
        # SAY SO. Falling back silently is the original defect wearing a
        # different hat: the operator answered yes, and would leave believing
        # they had an integrated bridge.
        integrated = False
        mailbox = recipient = ""
        console.say()
        console.say("  Both the directory and the name are needed for that, and")
        console.say("  one was blank. Writing a STANDALONE config instead.")
        console.say("  Re-run init to set up the mailbox once you have both.")

    # The letterbox helper. Not a flag: a flag is a thing every operator must
    # consider, and almost none of them have to. It is asked for only when
    # integrated mode needs it and it is not already on PATH.
    helper = ""
    if integrated:
        found = (helper_found or shutil.which)("bus.sh")
        if not found:
            console.say()
            console.say("Integrated mode rings through your letterbox's own")
            console.say("doorbell helper, and bus.sh is not on PATH here.")
            helper = console.ask("  path to the helper", "").strip()

    # 3. The token. Never echoed, never an argument, never printed back.
    console.say()
    console.say("Bot token from @BotFather. It is written to a mode-600 file and")
    console.say("not shown again. If this bot existed before, revoke and re-issue")
    console.say("the token first - one consumer per token is enforced by the")
    console.say("platform, and an old token cannot be proven unused.")
    if token_file:
        token = _consume_token_file(token_file)
        console.say("  token read from file and the file was removed.")
    else:
        token = console.ask_secret("  token (not echoed): ").strip()
    if not token:
        # Say it NOW, not at first run. The file is still written - the rest of
        # the boilerplate is real either way - but the operator leaves knowing
        # exactly what is missing rather than discovering it at 3am.
        console.say("  no token entered. The file is written without one, and the")
        console.say("  bridge will refuse to start until ALB_TOKEN is filled in.")

    env_path = root / "bridge.env"
    env_path_existed = env_path.exists()
    if env_path.exists():
        # Never clobber. Someone re-running this already has a working bridge
        # more often than not, and a silently replaced config is a morning.
        summary["kept"].append(str(env_path))
        console.say(f"  kept {env_path} - it already exists, nothing was changed")
    else:
        lines = [f"ALB_TOKEN={token}\n"]
        if mailbox:
            lines.append(f"ALB_MAIL_ROOT={mailbox}\n")
        if recipient:
            lines.append(f"ALB_TO={recipient}\n")
        if helper:
            lines.append(f"ALB_BUS_BINARY={helper}\n")
        _write_private(env_path, "".join(lines))
        summary["created"].append(str(env_path))

    # Reconcile a config that was already here. A summary is a report, never
    # a wish: if the mailbox keys are not in the file afterwards, this is not
    # an integrated install and must not be called one.
    # A REFUSAL IS TERMINAL. Downgrading to standalone and carrying on turned
    # "I will not write this" into a DIFFERENT install: a pane offered, a
    # surface written, a resident started. And where the old config held a
    # complete integrated route, the summary said standalone while the
    # runtime went on reading the old mailbox and recipient - a report that
    # disagreed with the file it described.
    #
    # Nothing further is written and nothing is started. The operator still
    # has the install they had.
    if integrated and not _persist_mailbox(console, env_path, env_path_existed,
                                           mailbox, recipient, helper, summary):
        summary["mode"] = "refused"
        summary["ring"] = "not configured"
        summary["resident"] = "not started"
        summary.pop("mail_root", None)
        console.say()
        console.say("Setup stopped. Your existing configuration is untouched.")
        _closing(console, root, summary)
        return summary

    # 4. The allowlist. Written deny-all whatever else happens; an entry is
    #    added only from a value the operator supplied or explicitly asked us
    #    to read.
    chats = _chat_ids(console, token, chat_id_reader)

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
        console.say("The doorbell is your letterbox's own, addressed to")
        console.say(f"  {recipient}")
        console.say("so there is no pane to choose and none to pin.")
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
        console.say("No ring is configured. The bridge will not be started.")
        console.say("A poller with nothing to ping is not an install.")
        console.say("Re-run init from a cmux pane, paste your agent's pane id")
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
            console.say("service manager. tmux has no born-inside rule, so the")
            console.say("ring works either way.")
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
    if answer.lower() in ("n", "no"):
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



def _chat_ids(console, token, reader):
    """Ask how the operator wants their chat id obtained. Both routes are real.

    Reading it removes a genuine trap: the payload contains a `from` id beside
    the `chat` id, they are identical in a direct message, and the wrong one
    denies everything the first time a group is used - silently.

    Printing the command keeps a stronger claim available: for anyone who
    chooses it, setup demonstrably never spoke to the platform.

    The choice is the operator's. Neither is the safe answer for everyone.
    """
    console.say()
    console.say("Your chat id goes in the allowlist. Two ways:")
    console.say("  read  - I make ONE getUpdates call with your token and show")
    console.say("          you the ids it returns. It consumes nothing and")
    console.say("          leaves your messages queued.")
    console.say("  print - I print the command and you run it yourself. I never")
    console.say("          touch the network.")
    choice = console.ask("  read or print", "print").strip().lower()

    if choice != "read" or reader is None:
        console.say()
        console.say("Run this, then put the number in allowlist.json:")
        console.say()
        console.say(CHAT_ID_COMMAND)
        console.say()
        return []

    console.say()
    console.say("Send your bot a message now, then press enter.")
    console.ask("  ready", "")

    try:
        found = reader(token)
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never crashes setup
        # A failed read must not leave a half-made install. Fall back to the
        # route that needs nothing from us.
        console.say(f"  could not read it: {exc}")
        console.say("  run this instead:")
        console.say(CHAT_ID_COMMAND)
        return []

    if not found:
        console.say("  nothing came back - that usually means the bot has not")
        console.say("  been messaged yet, or another process is polling it.")
        console.say("  run this when you have:")
        console.say(CHAT_ID_COMMAND)
        return []

    console.say()
    for index, entry in enumerate(found, start=1):
        console.say(f"  {index}) {entry['chat_id']}  {entry.get('label', '')}".rstrip())
    console.say()
    picked = console.ask("  which one is you (number, or blank for none)", "").strip()

    # Blank, a word, an out-of-range number: all mean no entry. An allowlist
    # written from a value we did not understand is the failure this command
    # exists to avoid.
    if not picked.isdigit():
        return []
    index = int(picked)
    if not 1 <= index <= len(found):
        return []
    return [str(found[index - 1]["chat_id"])]


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
    console.say("The ring types a line into a terminal pane when mail arrives -")
    console.say("it is what makes the bridge feel alive. Without it, letters land")
    console.say("and nobody is told. That is not an install.")

    if panes:
        console.say("Panes I can see:")
        for entry in panes:
            where = entry.get("notifier") or ""
            tag = f"[{where}] " if where else ""
            console.say(f"  {entry['id']}  {tag}{entry.get('label', '')}".rstrip())
        console.say("I am not choosing one: a listing cannot tell me which pane")
        console.say("holds your agent, and a doorbell in the wrong pane lands in")
        console.say("somebody else's session. Paste the id of YOUR agent's pane.")
        surface = console.ask("  your agent's pane id", "").strip()
        if not surface:
            console.say("  no pane id. The ring will not be configured.")
            return "", ""
        for entry in panes:
            if entry.get("id") == surface:
                summary["ring"] = "configured"
                notifier = entry.get("notifier") or ""
                summary["notifier"] = notifier
                return surface, notifier
        console.say("  that id is not in the list above - re-run to refresh.")
        return "", ""

    console.say("No panes are visible, so the ring cannot be configured.")
    console.say("Run init from inside a multiplexer with at least one pane,")
    console.say("then paste your agent's pane id. A later ALB_SURFACE edit")
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
    console.say("Then the test only you can run: have someone NOT on the")
    console.say("allowlist message the bot. Expect nothing to arrive and no")
    console.say("error. That silence is the gate working.")
