"""Slack events become the item the shared poller already knows how to read."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from alb.adapters.slack import events  # noqa: E402

TEAM = "T0000001"
BOT = "UBOT"
TS = "1700000000.000100"


def dm(**over):
    event = {
        "type": "message",
        "channel_type": "im",
        "user": "U0000001",
        "channel": "D0000001",
        "ts": TS,
        "text": "hi",
    }
    event.update(over)
    return event


def norm(event, **kw):
    kw.setdefault("team", TEAM)
    kw.setdefault("bot_user", BOT)
    return events.normalise(event, **kw)


class SlackEvents(unittest.TestCase):
    def test_dm_message_becomes_an_item_keyed_on_team_channel_ts(self):
        item = norm(dm(text="hello"))
        self.assertEqual(item["update_id"], f"slack:{TEAM}:D0000001:{TS}")
        self.assertEqual(item["chat_id"], "D0000001/U0000001")
        self.assertEqual(item["text"], "hello")
        self.assertEqual(item["caption"], "")
        self.assertEqual(item["message_id"], TS)
        self.assertEqual(item["photo_file_id"], "")
        self.assertEqual(item["media_kind"], "")
        self.assertEqual(item["provenance"], {
            "slack_team": TEAM,
            "slack_channel": "D0000001",
            "slack_ts": TS,
        })

    def test_thread_ts_is_provenance_never_identity(self):
        plain = norm(dm())
        threaded = norm(dm(thread_ts="1699999999.000100"))
        self.assertEqual(plain["update_id"], threaded["update_id"])
        self.assertEqual(
            threaded["provenance"]["slack_thread_ts"], "1699999999.000100"
        )
        self.assertNotIn("slack_thread_ts", plain["provenance"])
        self.assertEqual(threaded["reply_to_message_id"], "1699999999.000100")
        other = norm(dm(ts="1700000001.000100"))
        self.assertNotEqual(plain["update_id"], other["update_id"])

    def test_bot_posts_including_our_own_are_dropped(self):
        self.assertIsNone(norm(dm(bot_id="B0000001")))
        self.assertIsNone(norm(dm(user=BOT)))

    def test_edits_deletes_joins_are_dropped_but_file_share_is_kept(self):
        for subtype in (
            "message_changed", "message_deleted", "bot_message", "channel_join",
        ):
            self.assertIsNone(norm(dm(subtype=subtype)))
        kept = norm(dm(
            subtype="file_share", text="",
            files=[{
                "id": "F0000001", "mimetype": "image/png",
                "url": "https://files.slack.com/F0000001",
            }],
        ))
        self.assertEqual(kept["message_id"], TS)
        self.assertEqual(kept["text"], "")

    def test_own_mention_is_stripped_other_mentions_kept(self):
        item = norm({
            "type": "message",
            "channel_type": "channel",
            "user": "U0000002",
            "channel": "C0000001",
            "ts": TS,
            "text": "<@UBOT> ask <@U0000002> &amp; me",
        })
        self.assertEqual(item["text"], "ask <@U0000002> & me")
        literal = norm(dm(text="&amp;lt;"))
        self.assertEqual(literal["text"], "&lt;")

    def test_channel_mention_replies_in_a_thread_dm_does_not(self):
        mention = norm({
            "type": "app_mention",
            "user": "U0000002",
            "channel": "C0000001",
            "ts": TS,
            "text": "<@UBOT> hello",
        })
        self.assertEqual(mention["reply_thread"], TS)
        self.assertEqual(mention["reply_to_message_id"], "")
        self.assertEqual(norm(dm(text="hello"))["reply_thread"], "")
        threaded = norm(dm(thread_ts="1699999999.000100"))
        self.assertEqual(threaded["reply_thread"], "1699999999.000100")

    def test_history_channel_message_without_mention_is_dropped(self):
        self.assertIsNone(norm({
            "type": "message",
            "channel_type": "channel",
            "user": "U0000002",
            "channel": "C0000001",
            "ts": TS,
            "text": "talking among ourselves",
        }))
        kept = norm({
            "type": "message",
            "channel_type": "channel",
            "user": "U0000002",
            "channel": "C0000001",
            "ts": TS,
            "text": "<@UBOT> from history",
        })
        self.assertEqual(kept["text"], "from history")
        self.assertEqual(kept["chat_id"], "C0000001/U0000002")

    def test_image_file_sets_photo_fields_other_files_set_document(self):
        image = norm(dm(text="pic", files=[{
            "id": "F0000001", "mimetype": "image/jpeg",
            "url": "https://files.slack.com/a",
        }]))
        self.assertEqual(image["media_kind"], "photo")
        self.assertEqual(image["photo_file_id"], "F0000001")
        self.assertEqual(image["files"], [{
            "id": "F0000001", "mimetype": "image/jpeg",
            "url": "https://files.slack.com/a",
        }])
        document = norm(dm(text="doc", files=[{
            "id": "F0000002", "mimetype": "application/pdf",
            "url": "https://files.slack.com/b",
        }]))
        self.assertEqual(document["media_kind"], "document")
        self.assertEqual(document["photo_file_id"], "")
        mixed = norm(dm(text="both", files=[
            {"id": "F0000002", "mimetype": "application/pdf",
             "url": "https://files.slack.com/b"},
            {"id": "F0000001", "mimetype": "image/png",
             "url": "https://files.slack.com/a"},
        ]))
        self.assertEqual(mixed["media_kind"], "photo")
        self.assertEqual(mixed["photo_file_id"], "F0000001")

    def test_empty_body_without_files_is_dropped_with_files_is_kept(self):
        self.assertIsNone(norm(dm(text="")))
        self.assertIsNone(norm(dm(text="   ")))
        self.assertIsNone(norm(dm(text=None)))
        self.assertIsNone(norm(dm(text=12345)))
        kept = norm(dm(text="", files=[{
            "id": "F0000001", "mimetype": "text/plain",
            "url": "https://files.slack.com/c",
        }]))
        self.assertEqual(kept["text"], "")
        self.assertEqual(kept["media_kind"], "document")
        self.assertEqual(
            norm(dm(text="see this", files=[{
                "id": "F0000001", "mimetype": "text/plain",
                "url": "https://files.slack.com/c",
            }]))["text"],
            "see this",
        )
        self.assertEqual(
            norm(dm(text=None, files=[{
                "id": "F0000001", "mimetype": "text/plain",
                "url": "https://files.slack.com/c",
            }]))["text"],
            "",
        )

    def test_non_messages_and_missing_fields_are_dropped(self):
        self.assertIsNone(norm("nope"))
        self.assertIsNone(norm(None))
        self.assertIsNone(norm({"type": "reaction_added", "user": "U0000001"}))
        self.assertIsNone(norm(dm(user="")))
        self.assertIsNone(norm(dm(channel="")))
        self.assertIsNone(norm(dm(ts="")))
        # A live channel message is an app_mention. A bare message with no
        # channel type is history, and history without a mention is not mail.
        self.assertIsNone(norm({
            "type": "message", "user": "U0000001",
            "channel": "C0000001", "ts": TS, "text": "no mention",
        }))
