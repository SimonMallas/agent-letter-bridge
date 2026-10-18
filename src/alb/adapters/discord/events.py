"""Turn one Discord message into the item the shared poller already reads.

Returning None means "not a letter". Discord does not redeliver, so the caller
still advances the channel marker past a drop.
"""


def source_id(channel, message):
    return f"discord:{channel}:{message}"


def _string(value):
    return value if isinstance(value, str) else ""


def _mentioned(message, content, bot_user):
    mentions = message.get("mentions")
    if isinstance(mentions, list):
        for item in mentions:
            if isinstance(item, dict) and item.get("id") == bot_user:
                return True
    if f"<@{bot_user}>" in content or f"<@!{bot_user}>" in content:
        return True
    referenced = message.get("referenced_message")
    author = referenced.get("author") if isinstance(referenced, dict) else None
    if isinstance(author, dict) and author.get("id") == bot_user:
        return True
    return False


def _files(message):
    raw = message.get("attachments")
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = item.get("id")
        if not isinstance(file_id, str) or not file_id:
            continue
        mime = item.get("content_type")
        if not isinstance(mime, str):
            mime = ""
        url = item.get("url")
        if not isinstance(url, str):
            url = ""
        found.append({"id": file_id, "mimetype": mime, "url": url})
    return found


def normalise(message, *, bot_user, parent=None):
    if not isinstance(message, dict) or not isinstance(bot_user, str) or not bot_user:
        return None
    author = message.get("author")
    if not isinstance(author, dict):
        return None
    if author.get("bot") or author.get("system") or message.get("webhook_id"):
        return None
    author_id = author.get("id")
    if author_id == bot_user:
        return None
    if message.get("type") not in (0, 19):
        return None
    channel = message.get("channel_id")
    message_id = message.get("id")
    if not all(isinstance(value, str) and value
               for value in (author_id, channel, message_id)):
        return None
    content = _string(message.get("content"))
    guild = message.get("guild_id")
    direct = not (isinstance(guild, str) and guild)
    if not direct and not _mentioned(message, content, bot_user):
        return None
    files = _files(message)
    text = content.replace(f"<@{bot_user}>", "").replace(f"<@!{bot_user}>", "").strip()
    if not text and not files:
        return None
    images = [item for item in files if item["mimetype"].startswith("image/")]
    if images:
        media_kind, photo_file_id = "photo", images[0]["id"]
    elif files:
        media_kind, photo_file_id = "document", ""
    else:
        media_kind, photo_file_id = "", ""
    reference = message.get("message_reference")
    reply_to = ""
    if isinstance(reference, dict):
        replied = reference.get("message_id")
        if isinstance(replied, str):
            reply_to = replied
    chat_channel = parent if isinstance(parent, str) and parent else channel
    provenance = {
        "discord_channel": channel,
        "discord_message": message_id,
    }
    if not direct:
        provenance["discord_guild"] = guild
    if isinstance(parent, str) and parent:
        provenance["discord_thread_parent"] = parent
    return {
        "update_id": source_id(channel, message_id),
        "chat_id": f"{chat_channel}/{author_id}",
        "text": text,
        "caption": "",
        "photo_file_id": photo_file_id,
        "media_kind": media_kind,
        "message_id": message_id,
        "reply_to_message_id": reply_to,
        "reply_thread": "" if direct else f"{channel}:{message_id}",
        "provenance": provenance,
        "files": files,
    }
