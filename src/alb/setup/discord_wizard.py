"""The Discord half of `alb --init`: one token and one allowlist pair.

Everything else - the mailbox question, the config's mode, the ring, the
resident offer - is the shared wizard's, unchanged. The refusals are the
same too: no token in argv, no allowlist entry the operator did not choose,
and no network call unless the operator asks for it in that moment, having
been told what the calls are.

A Discord allowlist entry is a pair, `<channel>/<user>`: where a message was
sent and who sent it. Both must be allowed for it to become a letter.
"""
import re

from alb.adapters.discord import invite

PORTAL_URL = "https://discord.com/developers/applications"

_SNOWFLAKE = re.compile(r"[0-9]{17,20}")


def _rest_call():
    # Resolved when needed, so the wizard imports without the network module.
    from alb.adapters.discord import rest
    return rest.call


def explain(console):
    """Making the bot. Said once, in the order the Developer Portal asks."""
    console.say()
    console.say("Discord setup. The bot is your own, in your own server -")
    console.say("nothing passes through anyone else.")
    console.say()
    console.say(f"1. Open {PORTAL_URL}")
    console.say("   choose New Application, give it a name, agree, Create.")
    console.say("2. Open Bot in the left-hand menu:")
    console.say("   - turn OFF Public Bot, so only you can add it to a server;")
    console.say("   - leave every Privileged Gateway Intent OFF - the bridge")
    console.say("     needs none of them;")
    console.say("   - choose Reset Token and copy the token. Discord shows it")
    console.say("     once.")
    console.say()
    console.say("   The token is a password. Paste it only here, never into a")
    console.say("   chat, a document or a screenshot.")


def token(console, consume, *, token_file=None):
    """The bot token. Asked without echo, or read from a 0600 file that is
    then removed. A blank or misshapen one comes back empty and the bridge
    refuses to start until it is filled in."""
    if token_file:
        value = consume(token_file)
        if not invite.looks_like_a_token(value):
            from alb.setup.wizard import SetupError
            raise SetupError("the token file does not hold a Discord bot token")
        console.say("  bot token read from file and the file was removed.")
        return value
    value = console.ask_secret("  bot token (not echoed): ").strip()
    if value and not invite.looks_like_a_token(value):
        # Never echoed, not even in part: the likeliest mistake is another
        # secret pasted here.
        console.say("  that is not a Discord bot token.")
        console.say("  Nothing was written for it; add it to bridge.env later.")
        return ""
    if not value:
        console.say("  The token is missing. bridge.env is written without it,")
        console.say("  and the bridge will refuse to start until it is there.")
    return value


def invite_step(console, bot_token):
    """Step 3, from the token alone: no network call."""
    app_id = invite.application_id(bot_token)
    console.say()
    if not app_id:
        console.say("3. Add the bot to your server: on the Developer Portal's")
        console.say("   OAuth2 page, tick bot, then View Channels, Send Messages,")
        console.say("   Send Messages in Threads, Read Message History and Attach")
        console.say("   Files, and open the link it makes.")
    else:
        console.say("3. Add the bot to your server. Open this link, pick the")
        console.say("   server, then Authorize:")
        console.say()
        console.say(f"   {invite.url(app_id)}")
        console.say()
        console.say("   It asks only for what the bridge uses: read the channels")
        console.say("   it is in and their history, post, post in threads, and")
        console.say("   attach images.")
    console.say()
    console.say("In a server channel, @mention the bot every time; in a DM you")
    console.say("don't need to. The bot cannot read channel messages that do")
    console.say("not mention it.")
    console.say()
    console.say("One Discord bot serves one bridge. For a second agent, make a")
    console.say("second application.")


def _manual(console):
    console.say()
    console.say("Put the pair in allowlist.json yourself, as")
    console.say('  {"chats": ["<channel>/<user>"]}')
    console.say("First turn on Developer Mode: User Settings -> Advanced ->")
    console.say("Developer Mode.")
    console.say("Your user ID: right-click your own name -> Copy User ID.")
    console.say("The DM channel: open your DM with the bot; the address ends")
    console.say("with its ID (discord.com/channels/@me/<this>).")
    console.say("A server channel: right-click it -> Copy Channel ID.")
    console.say()


def pairs(console, bot_token, call=None):
    """Ask how the allowlist pair is found. Both routes are real.

    read  - you give your user ID; two calls: who the bot is (GET
            /users/@me) and the DM channel between you (POST
            /users/@me/channels, which opens it and sends nothing)
    print - say how to find both IDs by hand. Setup never touches the network.
    """
    console.say()
    console.say("Your allowlist entry is the DM between you and the bot plus")
    console.say("your user ID. Two ways to find them:")
    console.say("  read  - you paste your user ID; I check the token and open")
    console.say("          the DM channel with you. Nothing is sent.")
    console.say("  print - I tell you where to find them. I never touch the")
    console.say("          network.")
    choice = console.ask("  read or print", "print").strip().lower()
    if choice != "read" or not bot_token:
        _manual(console)
        return []

    console.say()
    console.say("Your user ID: turn on Developer Mode (User Settings ->")
    console.say("Advanced), then right-click your own name -> Copy User ID.")
    user = console.ask("  your user ID", "").strip()
    if not _SNOWFLAKE.fullmatch(user):
        # Anything not understood means no entry. An allowlist written from a
        # value we did not understand is the failure this command exists to
        # avoid.
        console.say("  that is not a Discord user ID (17 to 20 digits).")
        _manual(console)
        return []

    call = call or _rest_call()
    try:
        me = call("GET", "/users/@me", bot_token)
        bot_user = me.get("id") if isinstance(me, dict) else None
        if not isinstance(bot_user, str):
            raise ValueError("unexpected answer")
        if user == bot_user:
            console.say("  that is the bot's own ID, not yours.")
            _manual(console)
            return []
        console.say(f"  the token works for {me.get('username') or 'your bot'}.")
        dm = call("POST", "/users/@me/channels", bot_token,
                  {"recipient_id": user})
        channel = dm.get("id") if isinstance(dm, dict) else None
        if not isinstance(channel, str) or not _SNOWFLAKE.fullmatch(channel):
            raise ValueError("unexpected answer")
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never crashes setup
        console.say(f"  could not read them: {type(exc).__name__}")
        _manual(console)
        return []
    pair = f"{channel}/{user}"
    console.say(f"  your DM with the bot: {pair}")
    return [pair]
