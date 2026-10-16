"""Turn one Slack event into the item the shared poller already reads.

Returning None means "not a letter". The caller still acknowledges it.
A raise here would leave the event unacked, and Slack would deliver it again
into the same crash.
"""


def source_id(team, channel, ts):
    return f"slack:{team}:{channel}:{ts}"


def _string(value):
    return value if isinstance(value, str) else ""


def _clean(text, bot_user):
    """Drop our own mention, then unescape. Ampersand is last, so `&amp;lt;`
    stays the literal entity `&lt;`."""
    text = _string(text).replace(f"<@{bot_user}>", "").strip()
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    return text.replace("&amp;", "&")


def _files(event):
    raw = event.get("files")
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = item.get("id")
        if not isinstance(file_id, str) or not file_id:
            continue
        mime = item.get("mimetype")
        if not isinstance(mime, str):
            mime = ""
        url = ""
        for key in ("url_private_download", "url_private", "url"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate:
                url = candidate
                break
        found.append({"id": file_id, "mimetype": mime, "url": url})
    return found


def normalise(event, *, team, bot_user, channel_type=None):
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind not in ("message", "app_mention"):
        return None
    if event.get("bot_id") or event.get("user") == bot_user:
        return None
    subtype = event.get("subtype")
    if subtype not in (None, "", "file_share"):
        return None
    user = event.get("user")
    channel = event.get("channel")
    ts = event.get("ts")
    if not all(isinstance(value, str) and value for value in (user, channel, ts)):
        return None
    if channel_type is None:
        channel_type = event.get("channel_type")
    if not isinstance(channel_type, str):
        channel_type = ""
    raw = _string(event.get("text"))
    # Live channel mail arrives as app_mention. A message event that is not a
    # DM is history, and history is mail only when it names this bot.
    if kind == "message" and channel_type != "im":
        if f"<@{bot_user}>" not in raw:
            return None
    files = _files(event)
    text = _clean(raw, bot_user)
    if not text and not files:
        return None
    thread_ts = event.get("thread_ts")
    if not isinstance(thread_ts, str) or not thread_ts:
        thread_ts = ""
    if thread_ts:
        reply_thread = thread_ts
    elif channel_type != "im":
        reply_thread = ts
    else:
        reply_thread = ""
    images = [item for item in files if item["mimetype"].startswith("image/")]
    if images:
        media_kind, photo_file_id = "photo", images[0]["id"]
    elif files:
        media_kind, photo_file_id = "document", ""
    else:
        media_kind, photo_file_id = "", ""
    provenance = {
        "slack_team": team,
        "slack_channel": channel,
        "slack_ts": ts,
    }
    if thread_ts:
        provenance["slack_thread_ts"] = thread_ts
    return {
        "update_id": source_id(team, channel, ts),
        "chat_id": f"{channel}/{user}",
        "text": text,
        "caption": "",
        "photo_file_id": photo_file_id,
        "media_kind": media_kind,
        "message_id": ts,
        "reply_to_message_id": thread_ts if thread_ts != ts else "",
        "reply_thread": reply_thread,
        "provenance": provenance,
        "files": files,
    }
