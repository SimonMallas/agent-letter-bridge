# Discord

The bridge speaks Discord as well as Telegram and Slack. A Discord message to
your bot becomes the same durable letter a Telegram message does: written to
disk, deduplicated, enveloped, and then rung into the agent's pane. Replies,
initiated messages, photos, the allowlist, the claim ledger and every other
rule in [`invariants.md`](invariants.md) hold for Discord unchanged.

This page covers what is different. Everything else — modes, the ring, the
mailbox, `--status`, `--check`, `--stop`, grants — works as the README and
[`INSTALL.md`](../INSTALL.md) describe. For a first setup, the step-by-step
guide with screenshots is [`discord-setup.md`](discord-setup.md).

## How it connects

The Discord **Gateway**: the bridge opens an outbound WebSocket to Discord, so
it needs no public URL and works from a laptop behind NAT, the same way the
Telegram bridge long-polls. It uses the same standard-library WebSocket client
as the Slack bridge, so the core still has no third-party runtime dependency.

The bot is **your own**, created in your own Developer Portal account and
added to your own server. Nothing passes through anyone else.

## Setup

```sh
pipx install agent-letter-bridge
alb --init --platform discord --root ~/.alb-discord
```

`--init` walks you through it in this order:

1. **Create the application.** Open
   <https://discord.com/developers/applications> and choose **New
   Application**. On its **Installation** page, set **Install Link** to
   **None** and save; Discord refuses to make a bot private while it has an
   install link. Then, on its **Bot** page:
   - turn **Public Bot** off, so only you can add it to a server;
   - leave every **Privileged Gateway Intent** off — the bridge needs none;
   - choose **Reset Token** and copy the token.
2. **The token.** Paste it when asked (it is not echoed), or give it as a
   mode-600 file with `--token-file`, which is then removed. It is written to
   the mode-600 `bridge.env` as `ALB_TOKEN`, beside `ALB_PLATFORM=discord`, and
   never appears in argv, a log line, an error message or a letter.
3. **The invite link.** `--init` builds it from the token, with no network
   call, asking only for what the bridge uses:

   | Permission | Used for |
   | --- | --- |
   | View Channels | seeing the channels it is invited to |
   | Read Message History | recovering mentions sent while the bridge was off |
   | Send Messages, Send Messages in Threads | replies and initiated messages |
   | Attach Files | images out |

   Open it, pick your server, and choose **Authorize**.
4. **Your allowlist entry.** `--init` offers to find it: you give your user ID
   and it opens the DM channel between you and the bot, which sends nothing.
   Or it tells you where to find both IDs by hand, without touching the
   network.

## The allowlist is a pair

A Discord allowlist entry names **where** a message was sent and **who** sent
it: `<channel>/<user>`, both numeric IDs.

```json
{"chats": ["1234567890123456789/9876543210987654321"]}
```

- The first ID is your DM channel with the bot, or a server channel.
- The second is your user ID.
- Copying IDs needs **Developer Mode**: User Settings → Advanced → Developer
  Mode. Then right-click a name → **Copy User ID**, or a channel → **Copy
  Channel ID**.

A message becomes a letter only when **both** halves match. A message in a
thread is matched against the thread's parent channel, so one entry for a
channel covers its threads. To let a teammate reach the agent in a channel,
add their own pair for it.

A bare ID on its own matches nothing. `alb --status` and `alb --check` say
so. The allowlist is still fail-closed and ships deny-all: silence from the bot
is the deny path working.

## DMs and channels

- **In a DM, just write.** Every message you send the bot is a letter.
- **In a server channel or thread, @mention the bot.** Replying to one of the
  bot's own messages mentions it too, so a reply is enough for a follow-up. A
  message that does not mention the bot never becomes a letter.

The bridge answers a channel mention as a reply to it, which notifies you,
and answers a DM plainly.

**What the bot can read.** With the Message Content intent left off, Discord
gives the bot the text of DMs and of messages that mention it, and nothing
else — live or from history. Messages in the same channel that are not
addressed to it stay unread.

## While the bridge is off

Discord does not hold events for a bot that is not connected: a reconnect can
resume only a short gap. So the bridge **catches up** every time it connects.
For each channel on the allowlist, and each thread where it has been
mentioned, it reads the history after the last message it recorded there.
Anything new is published through the same path as a live message. The
delivered-ids ledger makes a message that arrives both ways land once.

Limits, stated plainly:

- The very first run reads back **24 hours**, matching Telegram's hold.
- Threads are caught up when the bridge is tracking them: the 200 most
  recently active threads where it has been mentioned. A mention sent while it
  is off, in a thread it has never seen, is not recovered.
- A long backlog is read in steps of up to ten pages a cycle. The marker for
  that channel does not move until the whole backlog has been read.
- Edits and deletions do not change a letter. A letter is a record of what
  arrived.

## Sending

`alb --reply-to <letter-id> --text "…"` posts into the channel the letter came
from: as a reply to it in a server channel or thread, and plainly in a DM.
`alb --send` uses a grant bound to a pair:

```sh
alb --config ~/.alb-discord/bridge.env --root ~/.alb-discord \
    --grant-create --as owner --platform discord \
    --chat-id 1234567890123456789/9876543210987654321
```

What the agent writes arrives as written:

- **Nothing the agent writes can ping anyone.** `@everyone`, `@here`, user and
  role mentions arrive as text. Discord enforces this itself
  (`allowed_mentions`), so there is no escaping to get wrong.
- Link previews are off.
- Text longer than Discord accepts from a bot (2,000 characters) is refused
  before anything is sent, rather than posted cut short.

Outcomes follow the same doctrine as Telegram and Slack. A Discord server
failure, a broken connection or an unreadable response after the request left
is **ambiguous**: the reply is dead-lettered for you and never retried. A rate
limit is the one outcome that provably sent nothing; run the same command
again to finish it. Any other refusal is definite.

## One bot, one bridge

Two connections on one bot token **both** receive every message. Two bridges
started with the same bot would each write the letter and each answer it. The
lock in each state directory stops that on one machine; across machines there
is no signal. So **give each bridge its own application**. Making another
takes a minute in the Developer Portal.

A Telegram, a Slack and a Discord bridge can run side by side, each with its
own `--root`, all publishing into the same mailbox through `--mail-root`.
