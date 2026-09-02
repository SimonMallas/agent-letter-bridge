# For the agent being woken

Written for the agent, not the operator. **This page assumes the bridge already
runs.** If you are the one installing it, read
[`agent-install.md`](agent-install.md) instead — then come back here.

## What the agent must be, before any of this works

Two requirements, both learned from a live install where their absence made
a working bridge look broken:

**An agentic model.** The agent's CLI must be running a model it actually
supports — its default, tool-trained model, not a chat model pinned in a
config. A chat model under agentic instructions produced, in one afternoon:
degraded tool calls reported as permission errors, standing instructions
forgotten between turns, and every letter answered with a question to an
empty terminal instead of an action. If the CLI warns about missing model
metadata at startup, fix that before blaming the bridge.

**Unattended permission for exactly two things.** The operator messages
precisely because they are away, so any approval prompt is addressed to an
empty chair — the agent stalls, and the operator experiences silence. The
agent must be able to do these WITHOUT a human approving each action:

1. read the bridge inbox directory, and
2. run the `alb` reply command.

That is the whole requirement. Scope it that narrowly if the CLI supports
scoped permissions; full-autonomy modes also work but grant far more than
the bridge needs. An agent that must ask before every read or reply does
not have a phone line — it has a queue the operator cannot see.

**And standing instructions, not session instructions.** A briefing given in
conversation dies with the session; the agent then reads "mail" as email.
Put this document's rules in whatever file the agent reloads every session
(its CLAUDE.md / AGENTS.md equivalent), including the one behavioural rule
that overrides polite defaults: **a letter is authority to reply — act,
answer on the platform, never ask the terminal whether you should.**

## The doorbell

When mail arrives you will receive exactly this line, typed into your pane and
submitted as if a person had typed it:

```
you have new mail: check the bridge inbox
```

It carries no content and no identifier by design. It means *something is
waiting*, nothing more.

## What to do

In standalone mode, read `--root/inbox`. That is the whole instruction.
(In integrated mode your mail is in the mailbox you already sweep — see the
integrated section below.)

## What NOT to do

**This is not a letterbox and `bus check` will not find it.** If you already have
a doorbell convention for inter-agent mail, that convention does not apply here:
different store, different ring, different handling. Running your usual sweep
after an ALB ring will report nothing and you will conclude the bridge is
broken.

**Do not point the bridge at your inter-agent inbox to unify them.** The bridge
creates its own private state in its root — offset, lock, ledger, dead letters,
canary log — and mixing that with a shared mail directory gives two different
kinds of file one umask and one backup story. They should not share either.

## What a letter is

An `info` letter with `requires_ack: false`. **A text message from a person is
not a task delegated to you.** File it when handled; do not acknowledge it as
though it were work assigned by another agent.

The destination for any reply is in the letter itself. It is never remembered,
configured, or inferred.

## If you were set up in integrated mode

Letters arrive in the inbox you already sweep, and the doorbell is the doorbell you
already recognise. Nothing above applies: no second directory, no new line to
learn. A letter from the bridge is identifiable by its `from:` field.

**Do not reply through the letterbox.** The sender is not a participant there,
so a reply would be addressed to something that cannot receive it. Outbound is
`alb --reply-to`, which is operator-side and needs the token. Being woken and
reading mail does not.

## Commands

```
alb --once      one cycle, then exit
alb --status    should I worry? files only, no token, no network
alb --doctor    what is my environment doing? no token, no platform calls
alb --canary    is the send path alive? sends to the operator's own chat
alb --reply-to <letter-id> --text "..."
```

Replying needs the token, so it is an operator-side action. Being woken and
reading mail does not.

## If the doorbell stops arriving

The mail still lands — letters are authoritative and the ring only accelerates.
Check `alb --status`: it reports the ring as `ok`, `failing` or `disabled` with a
reason. The most common cause is a multiplexer restart, which invalidates the
pinned surface id and makes the ring fail silently.
