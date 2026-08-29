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

## Status

**Pre-release.** Not yet published for general use.

## Requirements

Python 3.11+, standard library only. **Zero third-party runtime dependencies** —
by design, and enforced in CI. macOS (launchd) and Linux (systemd user units).
**Windows is a declared gap**, not a promise.

## Licence

MIT — see [LICENSE](LICENSE).
