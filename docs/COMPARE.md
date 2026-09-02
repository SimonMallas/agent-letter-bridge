# How this differs from Telegram-to-agent tools

**Provenance.** This document was written at release, not at design time. The
design comes from our own production failures — a fused bridge that lost a
message mid-network-flap, and the incidents that followed — not from a survey
of these tools. We read them afterwards, so that every claim of difference
printed here was checked against their current code and docs before being
made. That is also why every citation below carries the same date.

**Frame.** Other tools deliver external content as **agent input**. This
project delivers it as a **durable, deduplicated, enveloped letter** before
the platform is acknowledged. The letter is the record. A knock, if any, is
an accelerator and is allowed to fail.

Sources below were read on **2026-09-01**. Re-read them before a public
launch; these repos move.

## What we are not claiming

- **Not “zero prompt injection.”** The optional knock still types a short
  generic line into a pane and presses Enter. The letter body is still
  untrusted text once an agent reads it. The allowlist is the trust
  boundary, as [nightmux states for itself][nightmux-sec]. The claim that
  *is* true: **the Telegram body never enters the composer.**
- **Not unique stdlib.** nightmux is also Python stdlib with no relay
  server.
- **Not unique allowlist.** nightmux has `allow_users`; other bridges have
  user-id lists. cc-connect's Telegram `allow_from` **defaults to all
  users** (a WARN is logged if unset). Ours is fail-closed (empty list
  delivers nothing) and unknown config keys are refused.
- **Not unique offset handling.** Several pollers persist a Telegram
  offset. Ours is: **letter on disk, then ack.** Losing the offset
  redelivers; letters plus dedup make that harmless.

## Limitation that belongs in our column first

**A live knock on cmux only works if the process that rings was started
inside cmux.** cmux refuses connections from processes that were not
(`Access denied - only processes started inside cmux can connect`). A
LaunchAgent can poll Telegram and write letters; it cannot ring unless it
is given a pane-born socket capability, and that capability goes stale
when cmux restarts. Mail still lands. The bell does not. That is designed
once you treat the ring as optional; it is a walk-back if a launch post
implies “just run a user agent.”

tmux injectors do not have this cmux-specific ACL. Do not imply they fail
the same way.

## Telegram → a coding agent

The largest public member of this class by GitHub stars is
**[cc-connect][cc-connect]** (15.3k as of 2026-09-01): a multi-platform
control plane. Inbound still becomes agent input. It is not a tmux
injector; the contrast with a letter-before-ack still holds. Details
below, not collapsed into the mux-inject column.

| | Agent Letter Bridge | nightmux | Claude Code Telegram plugin | ccgram / telemux / tg-cli / claude-telegram-mirror |
|---|---|---|---|---|
| Inbound becomes | A Markdown letter on disk (`from`/`to`/ids in the envelope), then an optional generic knock | The full Telegram text, typed into the agent pane with `tmux send-keys` | An MCP notification into the live Claude Code **session** (not a mux inject) | The full message or a tagged line, injected into a tmux (or herdr/agterm) pane |
| Platform ack | After the letter exists | After `getUpdates` in the daemon (offset file). The pane is the record | After the plugin consumes `getUpdates`. If the wrong process won the poll, the intended session never sees it | After the daemon consumes the update; the pane is the record |
| Body in the composer? | No | Yes | Body enters the session as a message, not as tmux keys | Yes |
| Trust boundary | Fail-closed chat-id allowlist; empty list is silence | `allow_users` in `~/.nightmux.json`; [SECURITY.md][nightmux-sec] is explicit that this is the whole product | Pairing (`/telegram:access`); not a chat-id file in the plugin README | Varies. OctopusGarage `telegram-bridge` empty `ALLOWED_USER_IDS` denies; do not assume the others fail-closed without re-reading |
| `getUpdates` | One process per bot token. A second consumer is a `409`; the bridge yields and stays down | One daemon | **Fused:** every Claude Code session that loads the plugin may call `bot.start()`. Telegram delivers each update to **one** consumer. Documented races: [claude-code#41835][cc-41835], [#40114][cc-40114], [#39876][cc-39876], [#36893][cc-36893]. Proposed poll lock: [plugins-official#1070][cc-1070] | One daemon is the usual shape; not re-verified per repo at call sites |
| If the notifier dies | Letters wait in the inbox | Prompts may sit in a lockout queue; there is no letter to inspect | The update is already consumed | The update is already consumed |

### Per-tool notes (so the table does not smear them together)

**nightmux** ([mmr710/nightmux][nightmux], [ARCHITECTURE.md][nightmux-arch],
[SECURITY.md][nightmux-sec]). Telegram topic → worker thread → `inject()`
via tmux. Stdlib, no relay server. Offset at `~/.nightmux.offset`. Honest
about trust: anyone who can text the topic can run commands as you.
Forwards are typed as you. Pane targeting prefers a status-line snapshot
over the focused pane ([CHANGELOG][nightmux-cl]).

**Claude Code Telegram plugin**
([plugin README][cc-plugin]). A Bun MCP server logs in as a bot; inbound
is forwarded to the Claude Code session; outbound is MCP tools
(reply/react/edit). Describe it as **fused getUpdates + session**, not as
tmux `send-keys`. The consumer-discipline bugs are in the issue tracker,
not folklore.

**cc-connect** ([chenhg5/cc-connect][cc-connect],
[docs/telegram.md][cc-tg]). 15.3k stars. Bridges local agents (Claude
Code, Codex, Gemini CLI, Cursor, and others) to Telegram, Feishu,
DingTalk, Slack, Discord, LINE, WeChat Work, and more. Telegram uses
**long polling**; no public IP. User messages are a chat with the coding
agent (`cc-connect ↔ Claude Code CLI`). Same delivery class as the
injectors: external content becomes agent input, not a letter on disk.
`allow_from` unset → **all users permitted** (WARN logged);
`allow_from = "id1,id2"` restricts. `admin_from` unset blocks privileged
commands. Chat-id **whitelisting is documented as planned**, not
shipped, as of the 2026-09-01 read. Not stdlib; not a mux-pane inject.

**ccgram** ([PyPI][ccgram]). Sits on tmux / herdr / agterm, not on an
agent SDK. Pitch is walk-away / resume the same terminal session. Inbound
is still into that session. Allowlist details were not citation-grade
from the README alone — omit specifics until re-read.

**telemux** ([maarco/telemux][telemux]). Bidirectional daemon; replies
from Telegram are injected into a named tmux session. Sanitise via
`shlex.quote`; `!` prefix for raw. Not treated here as fail-closed.

**tg-cli** ([alex-mextner/tg-cli][tg-cli]). Inbound daemon injects
`[TG from you] …` into the pane. Buttons inject answers.

**claude-telegram-mirror** ([robertelee78/claude-telegram-mirror][ctm]).
`getUpdates` → tmux `send-keys`. Fail-closed if a session never reported
a pane (ROUTING-001). Capture-pane pacing for Enter (ADR-015) — a submit
check, not a mailbox.

**OctopusGarage/telegram-bridge** ([repo][og-tg]). Command bridge
(`/run` into a configured tmux target), not agent mail. Empty
`ALLOWED_USER_IDS` denies. Listed so “fail-closed allowlist” is not
claimed as unique.

**tsgram-mcp** ([areweai/tsgram-mcp][tsgram]). Mixed, not a silent
cousin. The README path is MCP + a local Docker Telegram server: you
chat with Claude about the project from the phone; access is limited
with `AUTHORIZED_CHAT_ID` (numeric user id). A **file-queue** script
also exists ([`telegram-claude-queue.ts`][tsgram-q]: Telegram → JSONL
queue → Claude monitors). That is a file, not an enveloped letter, and
`getUpdates` still acks in the queue poller. A separate session manager
([`claude-telegram-session.ts`][tsgram-s]) injects into the current
Claude stdin. Include it so “file queue” is not a later surprise; do
not equate the JSONL queue with this project's letters.

## A layout note, not a feature

**Letters are plain Markdown with YAML frontmatter, so tools that read
Markdown already understand them** — including Obsidian: an inbox inside a
vault is indexed as notes, with the envelope as properties. That is a property
of the file format, not an integration, and we claim nothing more. Tools that
purposefully pipe Telegram into a vault exist (obsidian-telegram-sync,
LazyLogger), as do vault-based agent-memory systems; this is neither.

## Relatives that are not Telegram rivals

**tmux-agent-comms** ([law-strange/tmux-agent-comms][tac]). Inter-agent
only. `post` writes a markdown thread and injects a **short doorbell**;
`send` still injects a full line. Delivery confirmation via
`capture-pane` (`delivered` vs `UNSUBMITTED`). Credit: doorbell + file
is not original to us; Letterbox and this repo use the same split for a
different edge (outside the machine). Do not list this row as a Telegram
competitor.

**Agent Letterbox** ([SimonMallas/agent-letterbox-cmux][lb-cmux] and
siblings). Where mail rests **between agents on one machine**. This
project is how mail **crosses in from outside**. Different products,
legible relationship. See the README.

## Citations (retrieved 2026-09-01)

[nightmux]: https://github.com/mmr710/nightmux
[nightmux-arch]: https://github.com/mmr710/nightmux/blob/main/ARCHITECTURE.md
[nightmux-sec]: https://github.com/mmr710/nightmux/blob/main/SECURITY.md
[nightmux-cl]: https://github.com/mmr710/nightmux/blob/main/CHANGELOG.md
[cc-plugin]: https://github.com/anthropics/claude-plugins-official/blob/main/external_plugins/telegram/README.md
[cc-41835]: https://github.com/anthropics/claude-code/issues/41835
[cc-40114]: https://github.com/anthropics/claude-code/issues/40114
[cc-39876]: https://github.com/anthropics/claude-code/issues/39876
[cc-36893]: https://github.com/anthropics/claude-code/issues/36893
[cc-1070]: https://github.com/anthropics/claude-plugins-official/pull/1070
[cc-connect]: https://github.com/chenhg5/cc-connect
[cc-tg]: https://github.com/chenhg5/cc-connect/blob/main/docs/telegram.md
[ccgram]: https://pypi.org/project/ccgram/
[tsgram]: https://github.com/areweai/tsgram-mcp
[tsgram-q]: https://github.com/areweai/tsgram-mcp/blob/main/src/telegram-claude-queue.ts
[tsgram-s]: https://github.com/areweai/tsgram-mcp/blob/main/src/claude-telegram-session.ts
[telemux]: https://github.com/maarco/telemux
[tg-cli]: https://github.com/alex-mextner/tg-cli
[ctm]: https://github.com/robertelee78/claude-telegram-mirror
[og-tg]: https://github.com/OctopusGarage/telegram-bridge
[tac]: https://github.com/law-strange/tmux-agent-comms
[lb-cmux]: https://github.com/SimonMallas/agent-letterbox-cmux
