"""The link that adds a user's own bot to their server, built offline.

A bot token's first segment is the bot's user ID, base64-encoded, and a bot's
user ID is its application ID. So setup can print the invite link from the
token it was just given, with no network call and nothing for the operator
to copy from the Developer Portal.
"""
import base64
import binascii

# The permissions the bridge uses, by name, and nothing else: read the
# channels it is invited to, read back what it missed, post, post in
# threads, and attach images.
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
SEND_MESSAGES_IN_THREADS = 1 << 38

PERMISSIONS = (VIEW_CHANNEL | SEND_MESSAGES | ATTACH_FILES
               | READ_MESSAGE_HISTORY | SEND_MESSAGES_IN_THREADS)

AUTHORIZE = "https://discord.com/oauth2/authorize"


def application_id(token):
    """The application ID inside a bot token, or None if it is not one.

    Nothing about the token is returned or raised except this ID, which is
    public (it is in every message the bot posts).
    """
    first, sep, _rest = str(token or "").partition(".")
    if not sep or not first:
        return None
    try:
        decoded = base64.urlsafe_b64decode(first + "=" * (-len(first) % 4))
        text = decoded.decode("ascii")
    except (binascii.Error, ValueError):
        return None
    if not text.isdigit() or not 17 <= len(text) <= 20:
        return None
    return text


def looks_like_a_token(token):
    """Three dot-separated parts, the first an application ID."""
    parts = str(token or "").split(".")
    return len(parts) == 3 and all(parts) and application_id(token) is not None


def url(app_id):
    return (f"{AUTHORIZE}?client_id={app_id}&scope=bot"
            f"&permissions={PERMISSIONS}")
