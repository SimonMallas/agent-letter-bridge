# Agent Letter Bridge

**Your agents, reachable from your phone — as durable mail.**

Agent Letter Bridge connects a chat app to AI agents on your own machine. An
incoming message becomes a durable file on disk **before** it is acknowledged and
**before** any agent is notified. The file is the source of truth; the
notification only makes it faster.

> **Messages outlive the crash of any single process.**

## Why this exists

The conventional approach fuses transport, durability and attention into one
process. When that process dies mid-flight — a network flap, a crash, a restart —
the message is gone, and the platform's retention window is the only thing
standing between an outage and permanent loss.

This design separates them. The message is written to disk first. Everything else
is an accelerator, and every accelerator is allowed to fail.

## What this is *not*

- **Not a messaging platform.** It does not send marketing, notifications or
  customer messages. It carries your own messages to your own agents.
- **Not [Agent Letterbox](https://github.com/SimonMallas/agent-letterbox-cmux).**
  Letterbox is where mail rests between agents on one machine. Letter Bridge is
  how mail crosses in from outside. Different products, legible relationship.
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

## Run it

```sh
./alb --config bridge.env --root ~/.alb --once
```

**Read [`docs/operations.md`](docs/operations.md) first.** You need four things
that are not in this repo — a bot token, your own chat id, an `allowlist.json`,
and a surface id for the ring — and **the bridge delivers nothing until the
allowlist exists**. That doc tells you how to get each one.

If nothing arrives, run `alb --doctor --root ~/.alb`. A fail-closed allowlist is
indistinguishable from a dead bot, so the doctor tells you which you have.

It refuses to start on a missing, world-readable or incomplete config. That is
deliberate: a bridge that starts wrong is harder to diagnose at 3am than one
that will not start at all.

**The ring requires a multiplexer** (cmux, or tmux with a uniquely identified
pane). v0.1 has no notifier that works without one — see `docs/operations.md`.

## Status

**Pre-release, and not yet run against a live platform.** Every component is
tested against fakes; nothing here has spoken to a real Telegram bot or a real
cmux pane. Do not point it at a bot you care about.

## What you need

- **Python 3.11+.** Standard library only, zero third-party runtime
  dependencies.
- **A terminal multiplexer** — cmux today. The ring works by typing a line into
  a pane, so a pane must exist to type into. Without one the mail still lands on
  disk; nothing pings.
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
