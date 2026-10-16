"""The Slack half of `alb --init`: two tokens and one allowlist pair.

Everything else - the mailbox question, the config's mode, the ring, the
resident offer - is the shared wizard's, unchanged. The refusals are the
same too: no token in argv, no allowlist entry the operator did not choose,
and no network call unless the operator asks for it in that moment, having
been told what the calls are.

A Slack allowlist entry is a pair, `<conversation>/<member>`: where a message
was sent and who sent it. Both must be allowed for it to become a letter.
"""
from alb.adapters.slack import manifest

APP_URL = "https://api.slack.com/apps?new_app=1"


def _web_call():
    # Resolved when needed, so the wizard imports without the network module.
    from alb.adapters.slack import web
    return web.call


def explain(console):
    """Creating the app. Said once, in the order Slack asks for things."""
    console.say()
    console.say("Slack setup. The Slack app is your own, in your workspace -")
    console.say("nothing passes through anyone else.")
    console.say()
    console.say(f"1. Open {APP_URL}")
    console.say("   choose From a manifest, pick your workspace, paste the")
    console.say("   manifest below over what is in the box, then Create.")
    console.say("   In a work workspace an admin may need to approve the app;")
    console.say("   a free workspace of your own works.")
    console.say()
    console.say("   Copy and paste it before you take any screenshot - a")
    console.say("   screenshot replaces what you copied.")
    console.say()
    console.say(manifest.render())
    console.say()
    console.say("   Do not screenshot or share the Basic Information page: it")
    console.say("   shows the app's secrets. This bridge needs only the two")
    console.say("   tokens below.")
    console.say()
    console.say("2. App-level token: Basic Information -> App-Level Tokens ->")
    console.say("   Generate, with the scope connections:write. It starts xapp-.")
    console.say("3. Bot token: Install App -> Install to Workspace -> Allow,")
    console.say("   then copy the Bot User OAuth Token. It starts xoxb-.")
    console.say()
    console.say("In a channel, @mention the bot every time; in a DM you don't")
    console.say("need to. Missed channel mentions are recovered from history,")
    console.say("so the app can read every message in channels it is added to;")
    console.say("only messages that mention it become letters.")
    console.say()
    console.say("One Slack app serves one bridge. Slack's free plan allows 10")
    console.say("apps per workspace.")


def _one_token(console, label, prefix, path, consume):
    if path:
        token = consume(path)
        if not token.startswith(prefix):
            from alb.setup.wizard import SetupError
            raise SetupError(f"the {label} file does not hold a {label} "
                             f"({prefix}...)")
        console.say(f"  {label} read from file and the file was removed.")
        return token
    token = console.ask_secret(f"  {label} (not echoed): ").strip()
    if token and not token.startswith(prefix):
        # Named by its expected prefix, never echoed: the likeliest mistake is
        # the other token pasted here, and that one is a secret too.
        console.say(f"  that is not a {label} - it should start {prefix}")
        console.say("  Nothing was written for it; add it to bridge.env later.")
        return ""
    return token


def tokens(console, consume, *, bot_token_file=None, app_token_file=None):
    """(bot token, app-level token). Asked without echo, or read from 0600
    files that are then removed. A blank or wrong one comes back empty and the
    bridge refuses to start until it is filled in."""
    app = _one_token(console, "app-level token", "xapp-", app_token_file,
                     consume)
    bot = _one_token(console, "bot token", "xoxb-", bot_token_file, consume)
    if not (app and bot):
        console.say("  A token is missing. bridge.env is written without it,")
        console.say("  and the bridge will refuse to start until it is there.")
    return bot, app


def _manual(console):
    console.say()
    console.say("Put the pair in allowlist.json yourself, as")
    console.say('  {"chats": ["<conversation>/<member>"]}')
    console.say("Your member ID: your profile picture -> Profile -> the three")
    console.say("dots -> Copy member ID. It starts U.")
    console.say("The conversation: open the app's Messages tab at")
    console.say("app.slack.com; the address ends with its ID, which starts D.")
    console.say("For a channel, it is the channel's ID (C... or G...).")
    console.say()


def _ok(console, payload, what):
    if isinstance(payload, dict) and payload.get("ok") is True:
        return True
    error = payload.get("error") if isinstance(payload, dict) else None
    console.say(f"  {what} failed: {error or 'no answer'}")
    return False


def pairs(console, bot, app, call=None):
    """Ask how the allowlist pair is found. Both routes are real.

    read  - three kinds of read-only call: who the bot is (auth.test), that
            Socket Mode is on (apps.connections.open, not connected), and the
            bot's DMs with their latest message (conversations.list, .history)
    print - say how to find both IDs by hand. Setup never touches the network.
    """
    console.say()
    console.say("Your allowlist entry is the DM between you and the bot plus")
    console.say("your member ID. Two ways to find them:")
    console.say("  read  - I check both tokens and list the bot's DMs, with read-")
    console.say("          only calls. Nothing is sent.")
    console.say("  print - I tell you where to find them. I never touch the")
    console.say("          network.")
    choice = console.ask("  read or print", "print").strip().lower()
    if choice != "read" or not (bot and app):
        _manual(console)
        return []

    call = call or _web_call()
    try:
        who = call("auth.test", bot, {})
        if not _ok(console, who, "checking the bot token"):
            return []
        if not _ok(console, call("apps.connections.open", app, {}),
                   "checking Socket Mode with the app-level token"):
            console.say("  Socket Mode must be on, and the app-level token")
            console.say("  needs the connections:write scope.")
            return []
        console.say(f"  bot token works in {who.get('team', 'your workspace')};")
        console.say("  Socket Mode is on.")
        console.say()
        console.say("Send the app one DM in Slack now (find it under Apps), then")
        console.say("press enter.")
        console.ask("  ready", "")
        found = _dms(call, bot, who.get("user_id", ""))
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never crashes setup
        console.say(f"  could not read them: {type(exc).__name__}")
        _manual(console)
        return []

    if not found:
        console.say("  no DM with a message from a person yet.")
        _manual(console)
        return []
    console.say()
    for index, (pair, preview) in enumerate(found, start=1):
        console.say(f"  {index}) {pair}  last message: {preview}")
    console.say()
    picked = console.ask("  which one is you (number, or blank for none)",
                         "").strip()
    # Anything not understood means no entry. An allowlist written from a
    # value we did not understand is the failure this command exists to avoid.
    if not picked.isdigit() or not 1 <= int(picked) <= len(found):
        return []
    return [found[int(picked) - 1][0]]


def _dms(call, bot, bot_user):
    """[(pair, preview)] for each DM whose latest person-message is visible."""
    listed = call("conversations.list", bot,
                  {"types": "im", "limit": "100", "exclude_archived": "true"})
    ims = listed.get("channels") or [] if listed.get("ok") else []
    found = []
    for im in ims:
        conversation, member = im.get("id", ""), im.get("user", "")
        if not conversation or not member or member in ("USLACKBOT", bot_user):
            continue
        history = call("conversations.history", bot,
                       {"channel": conversation, "limit": "5"})
        messages = history.get("messages") or [] if history.get("ok") else []
        for message in messages:
            if (message.get("user") == member and not message.get("bot_id")
                    and isinstance(message.get("text"), str)):
                preview = message["text"].replace("\n", " ")[:40]
                found.append((f"{conversation}/{member}", repr(preview)))
                break
    return found
