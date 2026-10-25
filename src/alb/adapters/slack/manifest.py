"""The Slack app manifest `alb --init --platform slack` prints.

A manifest gets the scopes, Socket Mode and event subscriptions right the
first time; a person clicking through Slack's settings by hand gets one of
them wrong, and the symptom of every one of them is the same silence.

Least privilege, scope by scope:
  app_mentions:read  - a channel message addressed to the bot
  im:history, im:read - direct messages to the bot, live and while it was off
  channels:history, groups:history - missed @mentions in public and private
                       channels, recovered from history. This reads every
                       message in a channel the app is added to; only
                       mentions become letters. Setup says so.
  chat:write         - replies and initiated sends
  files:read, files:write - images in and out

No `always_online`: a bot shown online while the bridge is stopped tells the
person deciding whether to send something untrue.
"""
import copy
import json
import re

# Slack's rule for a bot's display name: lowercase letters, digits, - _ and .
BOT_NAME = re.compile(r"[a-z0-9._-]{1,70}\Z")

MANIFEST = {
    "display_information": {
        "name": "Agent Letter Bridge",
        "description": "Message the agents on your own machine. Every "
                       "message becomes a letter on disk.",
    },
    "features": {
        "app_home": {
            "home_tab_enabled": False,
            "messages_tab_enabled": True,
            "messages_tab_read_only_enabled": False,
        },
        "bot_user": {
            "display_name": "letterbox",
        },
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "app_mentions:read",
                "channels:history",
                "groups:history",
                "im:history",
                "im:read",
                "chat:write",
                "files:read",
                "files:write",
            ],
        },
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": ["app_mention", "message.im"],
        },
        "interactivity": {"is_enabled": False},
        "org_deploy_enabled": False,
        "socket_mode_enabled": True,
        "token_rotation_enabled": False,
    },
}


def render(participant=""):
    """The manifest as Slack's "From a manifest" box expects it.

    The bot is named after the participant it serves when there is one. Every
    app used to call its bot "letterbox", so a person with two looked at two
    identical names and had to guess which agent each one reached (live test,
    25 Oct).
    """
    manifest = copy.deepcopy(MANIFEST)
    if BOT_NAME.match(participant or ""):
        manifest["features"]["bot_user"]["display_name"] = f"letterbox-{participant}"
    return json.dumps(manifest, indent=2)
