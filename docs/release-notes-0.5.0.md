# Agent Letter Bridge 0.5.0

Slack and Discord, beside Telegram. One letterbox behind all three. Telegram
configs, state, letters and output are unchanged.

## Slack

A Slack direct message to your bot, or a channel message that @mentions it,
becomes the same durable letter a Telegram message does. The letter is written
to disk before Slack is told it arrived, it is deduplicated, and it is rung into
the agent's pane. Replies, initiated sends, photos, the allowlist, the claim
ledger and the ambiguous-outcome rule work as they do for Telegram.

```sh
alb --init --platform slack --root ~/.alb-slack
```

Setup prints an app manifest, walks you through the two tokens and writes your
allowlist entry. [`docs/slack.md`](slack.md) covers everything that differs
from Telegram, and [Slack, step by step](slack-setup.md) shows every screen.

### How it works

- **Your own app, in your own workspace.** You create the Slack app from the
  manifest. Nothing passes through anyone else's server, and this is not a
  hosted service.
- **Socket Mode.** The bridge opens an outbound WebSocket to Slack, so it
  needs no public URL. The WebSocket client uses only Python's standard
  library, so the core still has no third-party runtime dependency.
- **Two tokens, kept apart.** The part that receives holds both tokens and has
  no send path. Sending uses the bot token alone. Both are stored in the
  mode-600 `bridge.env` and never appear in argv, a log line, an error or a
  letter.
- **The allowlist is a pair**, `<conversation>/<member>`: where a message was
  sent and who sent it. Both halves must match. A bare ID matches nothing, and
  `--status`, `--check` and `--doctor` say so.
- **What the agent writes arrives as written.** Slack's control characters are
  escaped, so `<!channel>` and `@here` in an agent's text never notify anyone.
  Formatting and link previews are off. Text longer than Slack accepts is
  refused before anything is sent.

### Limits, stated plainly

- **One Slack app serves one bridge.** Slack spreads events between
  connections on the same app without an error, so two bridges on one app
  would each see only part of your mail. `--status` shows how many
  connections Slack reported.
- **While the bridge is off**, Slack retries an event only for about five
  minutes. The bridge catches up from history each time it connects. That
  lasts as long as your workspace keeps history (90 days on Slack's free
  plan). The first run reads back 24 hours.
- **Threads** are caught up for the 200 most recently active threads the
  bridge has seen. A reply in any other thread, sent while the bridge is off,
  arrives only if Slack's own retries deliver it.
- **In a channel**, recovering missed mentions means the app can read every
  message there. Only messages that mention it become letters, and nothing
  else is stored.
- **Edits and deletions** do not change a letter.

## Discord

```sh
alb --init --platform discord --root ~/.alb-discord
```

A Discord DM to your bot, or a server message that @mentions it, becomes a
durable letter, and the agent's reply goes back to the same place.
[`docs/discord.md`](discord.md) covers what differs, and
[Discord, step by step](discord-setup.md) shows every screen.

- **Your own bot, in your own server.** Setup tells you to turn Public Bot off
  and to leave every privileged intent off.
- **The Discord Gateway,** on the same standard-library WebSocket client as
  Slack. The heartbeat must be acknowledged, or the connection is replaced.
- **It reads only what is addressed to it.** With no privileged intent,
  Discord gives the bot DMs and messages that mention it, nothing else.
- **Nothing an agent writes can ping anyone.** Discord enforces it through
  `allowed_mentions`. Link previews are off. Text over 2,000 characters is
  refused before anything is sent, and the agent can still answer that letter
  with shorter text.
- **The allowlist is a pair**, `<channel>/<user>`. A thread is matched against
  its parent channel.

### Limits, stated plainly

- **One bot serves one bridge.** Two bridges on one bot would both receive
  every message and both answer it, and nothing can detect it.
- **Discord never redelivers.** The bridge catches up from channel history on
  connect: allowlisted channels, and up to 200 threads where it has been
  mentioned. The first run reads back 24 hours.

## Evidence

Each adapter was built against fake transports with isolated tests and
mutation checks, and reviewed adversarially until no finding remained. The
suite and mutation gate passed on macOS and on Linux (Python 3.12). Slack then
ran a live round trip in a real workspace: setup, DMs in and out, catch-up
after a stop, an initiated send, photos both ways, a channel mention answered
in its thread, literal markup, and the canary. Discord ran the same round trip
in a private server: setup, DMs in and out, catch-up after a stop, an
initiated send, photos both ways, a channel mention answered as a reply, a
mention in a thread, `@everyone` and `@here` arriving as plain text, the
2,000-character limit, the ring and the canary. Faults that the live runs
found were fixed and pinned before this release.
