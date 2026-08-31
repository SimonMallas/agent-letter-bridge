# For the agent being woken

Written for the agent, not the operator. If you are setting this up, read
`operations.md` first — this page assumes the bridge already runs.

## The knock

When mail arrives you will receive exactly this line, typed into your pane and
submitted as if a person had typed it:

```
you have new mail: check the bridge inbox
```

It carries no content and no identifier by design. It means *something is
waiting*, nothing more.

## What to do

Read `--root/inbox`. That is the whole instruction.

## What NOT to do

**This is not a letterbox and `bus check` will not find it.** If you already have
a doorbell convention for inter-agent mail, that convention does not apply here:
different store, different knock, different handling. Running your usual sweep
after an ALB knock will report nothing and you will conclude the bridge is
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

## If the knock stops arriving

The mail still lands — letters are authoritative and the ring only accelerates.
Check `alb --status`: it reports the ring as `ok`, `failing` or `disabled` with a
reason. The most common cause is a multiplexer restart, which invalidates the
pinned surface id and makes the ring fail silently.
