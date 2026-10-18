"""A Discord message is a letter only when a person addressed the bot."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.discord import events  # noqa: E402

BOT = "100000000000000009"
USER = "100000000000000003"
CHANNEL = "100000000000000001"
THREAD = "100000000000000002"
GUILD = "100000000000000010"
MESSAGE = "100000000000000021"
PARENT_MESSAGE = "100000000000000020"


def message(content, **extra):
    body = {
        "id": MESSAGE,
        "channel_id": CHANNEL,
        "type": 0,
        "content": content,
        "author": {"id": USER},
    }
    body.update(extra)
    return body


class DiscordEvents(unittest.TestCase):
    def test_dm_needs_no_mention(self):
        item = events.normalise(message("hello from a dm"), bot_user=BOT)
        self.assertEqual(item["update_id"], f"discord:{CHANNEL}:{MESSAGE}")
        self.assertEqual(item["chat_id"], f"{CHANNEL}/{USER}")
        self.assertEqual(item["text"], "hello from a dm")
        self.assertEqual(item["reply_thread"], "")
        self.assertEqual(item["message_id"], MESSAGE)
        self.assertNotIn("discord_guild", item["provenance"])
        self.assertEqual(item["provenance"]["discord_channel"], CHANNEL)
        self.assertEqual(item["provenance"]["discord_message"], MESSAGE)

    def test_channel_message_without_mention_is_not_a_letter(self):
        body = message("just chatting", guild_id=GUILD)
        self.assertIsNone(events.normalise(body, bot_user=BOT))

    def test_reply_to_the_bot_counts_as_a_mention(self):
        body = message(
            "following up", guild_id=GUILD, type=19,
            message_reference={"message_id": PARENT_MESSAGE, "channel_id": CHANNEL},
            referenced_message={"id": PARENT_MESSAGE, "author": {"id": BOT}},
        )
        item = events.normalise(body, bot_user=BOT)
        self.assertEqual(item["reply_to_message_id"], PARENT_MESSAGE)
        self.assertEqual(item["text"], "following up")
        self.assertEqual(item["reply_thread"], f"{CHANNEL}:{MESSAGE}")
        self.assertEqual(item["provenance"]["discord_guild"], GUILD)

    def test_thread_message_uses_parent_in_chat_id(self):
        body = message(
            f"hello <@{BOT}>", guild_id=GUILD, channel_id=THREAD,
        )
        item = events.normalise(body, bot_user=BOT, parent=CHANNEL)
        self.assertEqual(item["chat_id"], f"{CHANNEL}/{USER}")
        self.assertEqual(item["provenance"]["discord_channel"], THREAD)
        self.assertEqual(item["provenance"]["discord_thread_parent"], CHANNEL)
        self.assertEqual(item["reply_thread"], f"{THREAD}:{MESSAGE}")
        self.assertEqual(item["update_id"], events.source_id(THREAD, MESSAGE))

    def test_bot_webhook_and_system_messages_are_dropped(self):
        self.assertIsNone(events.normalise(
            message("nope", author={"id": "100000000000000008", "bot": True}),
            bot_user=BOT,
        ))
        self.assertIsNone(events.normalise(
            message("nope", author={"id": USER, "system": True}),
            bot_user=BOT,
        ))
        self.assertIsNone(events.normalise(
            message("nope", webhook_id="100000000000000030"),
            bot_user=BOT,
        ))
        self.assertIsNone(events.normalise(
            message("nope", author={"id": BOT}),
            bot_user=BOT,
        ))
        self.assertIsNone(events.normalise(
            message("nope", guild_id=GUILD, type=7),
            bot_user=BOT,
        ))

    def test_mention_token_is_stripped(self):
        body = message(f"hi <@{BOT}> there <@!{BOT}>", guild_id=GUILD)
        item = events.normalise(body, bot_user=BOT)
        self.assertNotIn(BOT, item["text"])
        self.assertNotIn("<@", item["text"])
        self.assertEqual(item["text"], "hi  there")

    def test_image_attachment_sets_photo(self):
        url = "https://cdn.discordapp.com/attachments/100000000000000001/pic.png"
        body = message("", attachments=[{
            "id": "100000000000000031",
            "content_type": "image/png",
            "url": url,
            "filename": "pic.png",
        }])
        item = events.normalise(body, bot_user=BOT)
        self.assertEqual(item["text"], "")
        self.assertEqual(item["media_kind"], "photo")
        self.assertEqual(item["photo_file_id"], "100000000000000031")
        self.assertEqual(item["files"], [{
            "id": "100000000000000031",
            "mimetype": "image/png",
            "url": url,
        }])
        self.assertIsNone(events.normalise(message(""), bot_user=BOT))


if __name__ == "__main__":
    unittest.main()
