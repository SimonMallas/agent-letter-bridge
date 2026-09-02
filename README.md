# Agent Letter Bridge

**Your agents, reachable from your phone — as durable mail.**

<!-- LAUNCH GATE: the doorbell GIF goes here, and nothing is announced
     anywhere until it does. A message arrives on a phone; a letter file
     appears in the inbox; THEN the knock types itself into the pane.
     (assets/doorbell.gif — to be recorded against a clean demo root.) -->

Agent Letter Bridge connects a chat app to AI agents on your own machine. An
incoming message becomes a durable file on disk **before** it is acknowledged and
**before** any agent is notified. The file is the source of truth; the
notification only makes it faster.

Most tools in this space deliver an external message **as the agent's input** —
typed into its terminal, or handed to its session as a prompt. This one delivers
it as a **durable, deduplicated, enveloped letter**, written to disk before the
platform is even told the message was received. The message body never enters
the composer; the optional knock is one fixed, contentless line. How that
differs from each neighbouring tool, checked against their code and docs rather
than asserted: [`docs/COMPARE.md`](docs/COMPARE.md).

> **Messages outlive the crash of any single process.**

## Why this exists

A letter is more than the message. It carries who sent it, when, a verified
sender, its platform addressing and an exactly-once guarantee — a record, in
plain Markdown, that everything downstream can trust: the agent reading it
now, the memory system ingesting it later, the search that asks what was
said last month. Replies work the same way — addressed to the letter, which
knows its own way home.

That is what makes this a front door rather than a pipe. Whatever you build
behind it — today's agent, tomorrow's memory system — inherits records
instead of scrollback. The input side of your setup is settled once.

Durability is the supporting property, not the pitch: your phone keeps your
copy, and the letter is your agent's — still on disk after a crash, a
restart or a compaction, which is how a resurrected agent gets its context
back.

## What this is *not*

- **Not a messaging platform.** It does not send marketing, notifications or
  customer messages. It carries your own messages to your own agents.
- **Not [Agent Letterbox](https://github.com/SimonMallas/agent-letterbox-cmux).**
  Letterbox is where mail rests between agents on one machine. Letter Bridge is
  how mail crosses in from outside. Different products, legible relationship.
- **Not injection-proof, and we will not claim it.** The body never enters
  the composer, which removes the *delivery* path where a stranger's text
  becomes the agent's next command. But the knock is still one typed line, and
  a letter's body is still untrusted text once an agent chooses to read it.
  The allowlist is the trust boundary, here as in every tool of this class —
  the difference is what arrives when it passes: a letter to open, not a
  command already running.
- **Not a proxy or interceptor.** The bridge never *interprets* content or turns
  it into action, and the untrusted poller never inspects content for routing or
  ringing. (It is not "never reads" — the outbound helper necessarily reads a
  reply body in order to send it. We state the exact claim, not a flattering one.)
- **Not a hosted service.** There is no Bridge-operated service; your token is
  stored locally and sent only to your chosen platform's API, from your own
  machine. Inbound and outbound messages necessarily traverse that platform —
  we do not claim otherwise.

## Design

Four processes with deliberately unequal privilege. The separation *is* the
product.

| Process | Trust | May do | May **never** do |
| --- | --- | --- | --- |
| Poller | untrusted | fetch, write letter, then ack | ring, notify, or touch a terminal |
| Notifier | in-session | ring after a letter exists | carry message content in the ring |
| Send helper | bounded | reply to a stored letter's origin | originate contact; send on allowlist miss; auto-retry |
| Watchdog | independent | read mirrored health, report | restart anything; depend on what it monitors |

**Order is the invariant.** Letter to disk → *then* platform ack. A crash between
fetch and write causes redelivery, never loss.

Read [`docs/invariants.md`](docs/invariants.md) before trusting this with a token.
The invariants are the product; the code is how they are kept.

## Install

**Start here: [`INSTALL.md`](INSTALL.md)** — one numbered path from nothing to a
working bridge, about 15 minutes, with the checkpoints that catch the failures
that otherwise look like something else.

Installing it with a CLI agent rather than by hand? Give the agent
[`docs/agent-install.md`](docs/agent-install.md) — the same install written as a
brief, with the boundaries an agent needs and a human infers.

The short version, once you know the shape:

```sh
pipx install .                                  # or: uv tool install .
alb --init --root ~/.alb                        # creates the files, asks for the rest
alb --config ~/.alb/bridge.env --root ~/.alb --once
```

`--init` creates the state directory, a mode-600 config and a **deny-all**
allowlist, then asks you for the things no program can derive: your bot token,
whether your agent already has a mailbox, and your chat id — which it will
either read for you, or print the command for you to run, your choice. It never
invents an allowlist entry, never overwrites a file, and never touches the
network unless you ask it to.

**It will deliver nothing until a chat id is in that allowlist.** That is
deliberate and it is the step people skip.

If nothing arrives, run `alb --doctor --root ~/.alb`. A fail-closed allowlist is
indistinguishable from a dead bot, so the doctor tells you which you have. Each
cycle also reports itself — `fetched 2 · published 1 · denied 1 (allowlist)` —
so a working deny is visible to you without being visible to the sender.

It refuses to start on a missing, world-readable or incomplete config. That is
deliberate: a bridge that starts wrong is harder to diagnose at 3am than one
that will not start at all.

**The ring is optional and requires a multiplexer** — cmux or tmux, selected
with `ALB_NOTIFIER`. Without one the bridge still runs: mail lands durably,
nothing pings, and `alb --status` reports the ring as `disabled` rather than
leaving you guessing. Adapters are small files behind a written contract
([`docs/adapter-contract.md`](docs/adapter-contract.md)); a Herdr adapter is
planned, and will ship when there is a live workspace to prove the knock
against — untested transports do not ship here.

Reference and failure modes: [`docs/operations.md`](docs/operations.md).
Waking an agent that already handles other mail: [`docs/agent-setup.md`](docs/agent-setup.md).

## Status

**Pre-release.** Inbound delivery, ringing and bounded replies have been
exercised live against real bots on macOS and Linux, cmux and tmux, including by
someone other than the author. **Automated coverage still uses fakes** — the
suite proves the invariants, the live runs prove the transports, and those are
different claims. Not published, and not formally audited; see
`docs/threat-model.md` for what is and is not claimed.

## What you need

- **Python 3.11+.** Standard library only, zero third-party runtime
  dependencies.
- **A terminal multiplexer — optional.** cmux or tmux, selected with
  `ALB_NOTIFIER`. The ring works by typing a
  line into a pane, so a pane must exist to type into. **Without one the bridge
  still runs**: mail lands durably and nothing pings, and `alb --status` reports
  the ring as `disabled` rather than leaving you guessing.
- **A CLI agent** sitting in that pane.
- **A bot** on your chat platform.

**You do not need Agent Letterbox.** They are separate products that share a
metaphor and nothing else — no shared code, no shared files, no shared config.
Letterbox carries mail between agents on one machine; this carries mail in from
outside. Install either, both, or neither.

## Requirements

Python 3.11+, standard library only. **Zero third-party runtime dependencies** —
by design, and enforced in CI. macOS (launchd) and Linux (systemd user units).
**Windows is a declared gap**, not a promise.

## Licence

MIT — see [LICENSE](LICENSE).
