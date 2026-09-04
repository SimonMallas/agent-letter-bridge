# Agent Letter Bridge

**Message the CLI agents on your machine from your phone — and every message
becomes part of their memory.**

Agent Letter Bridge connects a chat app to AI agents on your own machine. An
incoming message becomes a durable file on disk **before** it is acknowledged and
**before** any agent is notified. The file is the source of truth; the ring is
how it gets read. Letter first, then ring — without a bell, mail lands in a dead
drop and nobody is told.

Most tools in this space deliver an external message **as the agent's input** —
typed into its terminal, or handed to its session as a prompt. This one delivers
it as a **durable, deduplicated, enveloped letter**, written to disk before the
platform is even told the message was received. That buys you three things:
a context window that stays clean, because the doorbell is one contentless
line and the body enters only when the agent chooses to read it; messages
that survive compaction at full fidelity, because a letter lives outside the
session; and a growing archive of records — origin, receipt time, content,
every origin allowlist-verified — that any memory system can take as ground
truth. Delivery that
behaves like memory, not like typing.

> **More memory than message.**

## Why this exists

CLI agents don't have phones. The most capable coding agents live in
terminal panes on a machine, and the moment you stand up from the desk they
are unreachable — while everything else in your life answers from the phone
in your pocket. This bridge gives the agents on your machine a messaging
app: text them from anywhere, and they can answer.

The added edge is what arrives. A letter is more than the message. It
carries its origin — the chat it came from, verified against the allowlist —
when it was received, its platform addressing and an exactly-once
guarantee — a record, in
plain Markdown, that everything downstream can trust: the agent reading it
now, the memory system ingesting it later, the search that asks what was
said last month. Replies are addressed to the letter, which knows its own
way home. Since v0.2 the archive runs both ways: an outbound reply is
written as its own letter *before* the platform is touched, and its delivery
events are recorded as immutable files beside it.

That is what makes this a front door rather than a pipe. Whatever you build
behind it — today's agent, tomorrow's memory system — inherits records
instead of scrollback. The input side of your setup is settled once.

The context economics follow from the same split. The body never enters the
agent's context window until the agent chooses to read it — the doorbell is one
contentless line — and it can be re-read at full fidelity after a
compaction, because a letter is storage outside the window. What a session
forgets, the inbox still knows.

Memory systems get the same service. Underneath every one of them —
vault-based, RAG, graph, whatever comes next — sits the same need: durable,
addressable, re-readable records of what was actually said. A letter already
is one. Point any memory system at the inbox and it has its ground truth: no
scraper, no export, no plugin. We are deliberately only the storage half —
the records are ours, the librarian can be anyone's.

Durability is the supporting property, not the pitch: your phone keeps your
copy, and the letter is your agent's — still on disk after a crash, a
restart or a compaction, which is how a resurrected agent gets its context
back.

## Design

Four roles with deliberately unequal privilege. The separation *is* the
product — and it is enforced at module boundaries and proved by
tests (the poller code path cannot ring; the watchdog reads only mirrored
state), not by OS process isolation: the resident bridge holds the token and
the notifier in one process, and `docs/operations.md` states that limit
plainly rather than letting this table imply more.

| Role | Trust | May do | May **never** do |
| --- | --- | --- | --- |
| Poller | untrusted | fetch, write letter, then ack | ring, notify, or touch a terminal |
| Notifier | in-session | ring after a letter exists | carry message content in the ring |
| Send helper | bounded | reply to a stored letter's origin | originate contact; send on allowlist miss; auto-retry |
| Watchdog | independent | read mirrored health, report | restart anything; depend on what it monitors |

**Order is the invariant.** Letter to disk → *then* platform ack. A crash between
fetch and write causes redelivery, never loss.

Read [`docs/invariants.md`](docs/invariants.md) before trusting this with a token.
The invariants are the product; the code is how they are kept.

## What this is *not*

- **Not a messaging platform.** It does not send marketing, notifications or
  customer messages. It carries your own messages to your own agents.
- **Not [Agent Letterbox](https://github.com/SimonMallas/agent-letterbox-cmux).**
  Letterbox is where mail rests between agents on one machine. Letter Bridge is
  how mail crosses in from outside. Different products, legible relationship.
- **Not injection-proof, and we will not claim it.** The body never enters
  the composer, which removes the *delivery* path where a stranger's text
  becomes the agent's next command. But the doorbell is still one typed line, and
  a letter's body is still untrusted text once an agent chooses to read it.
  The allowlist is the trust boundary, here as in every tool of this class —
  the difference is what arrives when it passes: a letter to open, not a
  command already running.
- **Not a proxy or interceptor.** The bridge never *interprets* content or turns
  it into action, and the untrusted poller never inspects content for routing or
  ringing. (It is not "never reads" — the outbound helper necessarily reads a
  reply body in order to send it. We state the exact claim, not a flattering one.)
- **Not asserted, checked.** Every claim of difference from the neighbouring
  tools was verified against their current code and docs before being made:
  [`docs/COMPARE.md`](docs/COMPARE.md).
- **Not a hosted service.** There is no Bridge-operated service; your token is
  stored locally and sent only to your chosen platform's API, from your own
  machine. Inbound and outbound messages necessarily traverse that platform —
  we do not claim otherwise.

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

**The ring is what makes the bridge live**, and it needs a multiplexer — cmux
or tmux, selected with `ALB_NOTIFIER`. Mail without a bell is a dead drop:
delivered, safe, and unread until someone thinks to look. What the design
guarantees is that the ring may *fail* without costing a letter — never that
you would want to run without one. If you must (no multiplexer, an agent that
sweeps on its own schedule), the bridge still delivers and `alb --status` says
the ring is `disabled` rather than leaving you guessing. Adapters are small files behind a written contract
([`docs/adapter-contract.md`](docs/adapter-contract.md)); a Herdr adapter is
planned, and will ship when there is a live workspace to prove the doorbell
against — untested transports do not ship here.

Reference and failure modes: [`docs/operations.md`](docs/operations.md).
Waking an agent that already handles other mail: [`docs/agent-setup.md`](docs/agent-setup.md).

## Status

**v0.2.1.** Inbound delivery, ringing and bounded replies have been
exercised live against real bots on macOS and Linux, cmux and tmux, including by
someone other than the author. v0.2 adds durable outbound letters, correspondent
identity and threading, and read-only retrieval (`--list`, `--show`, `--search`,
`--thread`, `--export`); those are covered by the suite and reviewed, but have
not yet had the same live mileage as the inbound path. **Automated coverage
still uses fakes** — the suite proves the invariants, the live runs prove the
transports, and those are different claims. Not on a package index, and not
formally audited; see `docs/threat-model.md` for what is and is not claimed.

## What you need

- **Python 3.11+.** Standard library only, zero third-party runtime
  dependencies.
- **A terminal multiplexer — cmux or tmux** — selected with `ALB_NOTIFIER`.
  The ring types a line into a pane, so a pane must exist to type into.
  Expect to want this: without it mail lands durably and *nobody is told*.
  The bridge runs regardless, and `alb --status` reports the ring as
  `disabled` rather than leaving you guessing.
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
