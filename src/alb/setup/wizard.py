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
    the operator meant, and a knock typed into the wrong pane lands in somebody
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


def init(root, console, chat_id_reader=None, panes=None):
    """Create the boilerplate under `root`, asking for what cannot be derived.

    `console` supplies say / ask / ask_secret, so the questions are testable
    and so the secret prompt is a distinct call rather than a convention.

    Note the absence of a `token` parameter: it is asked for, never passed.
    A signature that accepted one would grow a --token flag, and a flag is
    shell history.
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
    console.say("Does the agent already have an inbox it sweeps for mail from")
    console.say("other agents on this machine? If so, letters can be delivered")
    console.say("there instead of to a second directory it would have to learn.")
    console.say("Leave blank for a directory this bridge owns.")
    mailbox = console.ask("  path to that inbox's parent, or blank", "").strip()

    recipient = ""
    if mailbox:
        summary["mode"] = "integrated"
        summary["mail_root"] = mailbox
        console.say()
        console.say("The doorbell is addressed by name, so it has to be the name")
        console.say("that agent is registered under - not a display name.")
        recipient = console.ask("  the agent's participant name", "").strip()

    # 3. The token. Never echoed, never an argument, never printed back.
    console.say()
    console.say("Bot token from @BotFather. It is written to a mode-600 file and")
    console.say("not shown again. If this bot existed before, revoke and re-issue")
    console.say("the token first - one consumer per token is enforced by the")
    console.say("platform, and an old token cannot be proven unused.")
    token = console.ask_secret("  token (not echoed): ").strip()

    env_path = root / "bridge.env"
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
        _write_private(env_path, "".join(lines))
        summary["created"].append(str(env_path))

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

    # 5. The ring. Listed, never chosen.
    _offer_ring(console, panes, summary)

    _closing(console, root, summary)
    return summary


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


def _offer_ring(console, panes, summary):
    """List panes if we were given any. Never pick one."""
    console.say()
    console.say("The ring types a line into a terminal pane when mail arrives.")
    console.say("It is optional: without it mail still lands and nothing pings,")
    console.say("and --status reports the ring as disabled rather than broken.")

    if panes:
        console.say("Panes I can see:")
        for entry in panes:
            console.say(f"  {entry['id']}  {entry.get('label', '')}".rstrip())
        console.say("Put the id of your agent's pane in bridge.env as ALB_SURFACE.")
        console.say("I am not choosing one: a listing cannot tell me which pane")
        console.say("holds your agent, and a knock in the wrong pane lands in")
        console.say("somebody else's session.")
    else:
        console.say("Add ALB_SURFACE to bridge.env when you have a pane id.")


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
