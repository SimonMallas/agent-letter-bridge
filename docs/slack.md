# Slack

The bridge speaks Slack as well as Telegram. A Slack message to your bot
becomes the same durable letter a Telegram message does: written to disk
before Slack is told it arrived, deduplicated, enveloped, and then rung into
the agent's pane. Replies, initiated messages, photos, the allowlist, the
claim ledger and every other rule in [`invariants.md`](invariants.md) hold
for Slack unchanged.

This page covers what is different. Everything else — modes, the ring, the
mailbox, `--status`, `--check`, `--stop`, grants — works as the README and
[`INSTALL.md`](../INSTALL.md) describe.

## How it connects

Slack's **Socket Mode**: the bridge opens an outbound WebSocket to Slack, so
it needs no public URL and works from a laptop behind NAT, the same way the
Telegram bridge long-polls. The WebSocket client is part of the bridge and
uses only Python's standard library; the core still has no third-party
runtime dependency.

The Slack app is **your own**, created in your workspace from a manifest the
bridge prints. Nothing passes through anyone else.

## Setup

```sh
pipx install agent-letter-bridge
alb --init --platform slack --root ~/.alb-slack
```

`--init` walks you through it in this order:

1. **Create the app.** Open <https://api.slack.com/apps?new_app=1>, choose
   **From a manifest**, pick your workspace and paste the manifest `--init`
   prints. In a work workspace an admin may need to approve the app; a free
   workspace of your own works.
   - Paste before you take any screenshot: a screenshot replaces what you
     copied.
   - Do not screenshot or share the **Basic Information** page. It shows the
     app's secrets. The bridge needs only the two tokens below.
2. **App-level token** (starts `xapp-`): **Basic Information → App-Level
   Tokens → Generate**, with the scope `connections:write`. The bridge uses
   it to open the Socket Mode connection.
3. **Bot token** (starts `xoxb-`): **Install App → Install to Workspace →
   Allow**, then copy the **Bot User OAuth Token**. The bridge uses it to
   read and post.
4. **Your allowlist entry.** `--init` offers to find it with read-only calls
   you choose (it checks both tokens and lists the bot's DMs; nothing is
   sent), or to tell you where to find it by hand without touching the
   network.

Both tokens are asked without echo, or read from mode-600 files given as
`--token-file` (bot) and `--app-token-file` (app), which are then removed.
They are written to the mode-600 `bridge.env` as `ALB_TOKEN` and
`ALB_APP_TOKEN`, beside `ALB_PLATFORM=slack`. Neither ever appears in argv,
a log line, an error message or a letter.

The scopes the manifest asks for, and why:

| Scope | Used for |
| --- | --- |
| `im:history`, `im:read` | direct messages to the bot, live and while it was off |
| `app_mentions:read` | a channel message that @mentions the bot |
| `channels:history`, `groups:history` | missed @mentions in public and private channels, recovered from history |
| `chat:write` | replies and initiated messages |
| `files:read`, `files:write` | images in and out |

## The allowlist is a pair

A Slack allowlist entry names **where** a message was sent and **who** sent
it: `<conversation>/<member>`.

```json
{"chats": ["D0123ABCD/U0456EFGH"]}
```

- `D…` is your DM with the bot; a channel is `C…` (public) or `G…`
  (private).
- `U…` is your member ID: your profile picture → **Profile** → the three
  dots → **Copy member ID**.

A message becomes a letter only when **both** halves match. To let a
teammate reach the agent in a channel, add their own pair for that channel.

A bare ID (`D0123ABCD` or `U0456EFGH` on its own, as a Telegram chat id would
be written) matches nothing. `alb --status` and `alb --check` say so. The
allowlist is still fail-closed and ships deny-all: silence from the bot is
the deny path working.

## DMs and channels

- **In a DM, just write.** Every message you send the bot is a letter,
  including replies in a thread.
- **In a channel, @mention the bot every time**, including follow-ups in a
  thread. A channel message that does not mention the bot is not for the
  agent and never becomes a letter. Invite the bot first:
  `/invite @letterbox`.

The bridge answers a channel mention in a thread under it, and answers a DM
at the top level unless your message was itself in a thread.

**What the app can read.** Recovering channel mentions sent while the bridge
was off means reading channel history, so the app can read every message in
a channel it has been added to. Only messages that mention it become
letters; nothing else is stored. Add it only to channels where that is
acceptable.

## While the bridge is off

Telegram holds undelivered messages for 24 hours. Slack does not hold them
for a disconnected app in the same way: it retries an unacknowledged event
three times, the last about five minutes after the first. So the bridge
**catches up** every time it connects: for each conversation on the
allowlist, it reads the history since the last message it recorded there,
and publishes anything new through the same path as a live message. The
delivered-ids ledger makes a message that arrives both ways land once.

Limits, stated plainly:

- The promise lasts as long as your workspace keeps history: **90 days on
  Slack's free plan**.
- The very first run reads back **24 hours**, matching Telegram's hold, so a
  message sent before you first started the bridge still arrives.
- Threads are caught up when the bridge has already seen them. A reply in a
  thread the bridge has never seen, sent while it is off, arrives only if
  Slack's own retries deliver it — within about six minutes.
- Edits and deletions do not change a letter. A letter is a record of what
  arrived.

## Sending

`alb --reply-to <letter-id> --text "…"` posts into the conversation the
letter came from, in its thread if it had one. `alb --send` uses a grant
bound to a pair:

```sh
alb --config ~/.alb-slack/bridge.env --root ~/.alb-slack \
    --grant-create --as owner --platform slack --chat-id D0123ABCD/U0456EFGH
```

What the agent writes arrives as written:

- Slack's control characters are escaped, so `<!channel>`, `@here` and
  `<@U…>` in an agent's text are shown as text, never as a mass mention or a
  ping.
- Formatting is off, so `*` and `_` stay literal, and link previews are off.
- Text longer than Slack accepts (40,000 characters) is refused before
  anything is sent, rather than posted cut short. Photo captions follow the
  photo rules.

Outcomes follow the same doctrine as Telegram. A Slack server failure, a
broken connection or an unreadable response after the request left is
**ambiguous**: the reply is dead-lettered for you and never retried, because
Slack, like Telegram, has no idempotency key. A rate limit is the one
outcome that provably sent nothing; run the same command again to finish it.
Any other refusal is definite. A photo is uploaded, then shared; only sharing
can post, so a failure before it is definite.

## One Slack app, one bridge

Slack lets several connections share one app and spreads events between
them without any error. Two bridges started with the same app would each see
only some of your messages, and both would catch up the same history. The
lock in each state directory stops that on one machine; across machines
there is no reliable signal. So:

- **Give each bridge its own Slack app.** Slack's free plan allows 10 apps
  per workspace.
- `alb --status` shows the number of connections Slack reported for the app
  when the bridge last connected. More than one means another bridge, or a
  connection from one that has just stopped, is holding the same app.

A Telegram bridge and a Slack bridge can run side by side, each with its own
`--root`, both publishing into the same mailbox through `--mail-root`.
