# Operations

Written for a stranger at 3am with nobody to ask.

## Day-0, in order

The ordering is the content. Do these in sequence.

1. **Install into an empty directory you choose.** Never drop it next to an
   existing poller.
2. **Create your own `0600` env file.** Never copy one from an existing plugin.
3. **Revoke and re-issue the bot token.** This is a **Day-0 gate, not a footnote
   and not incident response** — an inherited bot is the *common* case. Proving
   absence on an old token is the one thing nobody can do; a re-issued token is
   one you alone hold, which makes single-consumer true by construction rather
   than by inspection. Everything below gets easier once this is done.
4. **Prove no local consumer exists** — process list, service managers, lock
   holders, cron — **including across a restart.** Policy-disabled is not
   inbound-off.
5. **Settle the webhook case.** `doctor` prints a read-only `getWebhookInfo`
   command; you run it in your own shell. If a webhook is set, the remedy is
   `deleteWebhook` or a token re-issue — polling cannot coexist with it.
   **The ring requires a multiplexer** (cmux or tmux) with a uniquely
   identified pane. v0.1 has no notifier that works without one.
6. **Start it, then run the Day-0 test — inbound only.** A message from a listed
   sender must produce a letter. A message from an unknown sender must produce
   **silence**. Outbound is not a Day-0 step: the send helper replies only to a
   stored inbound letter, so there is nothing to reply to yet.
7. **Verify the ring for real, once.** Send yourself a message and watch the
   knock arrive in the pane. This step cannot be skipped or inferred: the code
   deliberately swallows ring failures so a dead notifier never costs a letter —
   which means a broken ring is **silent**. Mail landing with no bell is a
   failure state, not a quieter mode of working. `state/ring-health.json`
   records the last outcome, but only a real knock proves the transport.
8. **Know the rollback** that restores *your* previous consumer.

### The first-hour allowlist test

A correctly-working fail-closed allowlist is **indistinguishable from a dead
bot**. Silence from an unknown sender is the deny path *succeeding*.

If you skip this test, the first thing you will do when "nothing arrives" is
disable the security control to fix it. That is the predictable disaster this
paragraph exists to prevent.

### The poller cannot send — with the honest limit

The shipped poller has no supported send code path, enforced by test. Day-0 must
not imply one binary does both jobs.

But the bot token is the same credential in both directions. This is a
code-discipline guarantee, **not** credential separation. Anyone modifying the
poller can send.

### Degraded mode

With the notifier absent or dead, mail still lands on disk and nothing pings your
terminal. That is designed behaviour, not a fault — you find the mail by looking.

## Daemon context is not terminal context

This is the failure that will cost you a morning.

A process started by a service manager gets a different environment from the same
command typed into a terminal: a different `PATH`, and on some systems different
permissions for external or removable volumes — so a write that works by hand
fails with a permission error under the daemon.

Two rules follow:

1. **Pin the absolute path** of the interpreter you intend to run. Never rely on
   `PATH` resolution in a unit file. Version managers are invisible to service
   managers.
2. **Reproduce daemon-only failures with a throwaway probe job** under the same
   service manager. Testing by hand exercises the wrong context and proves
   nothing.

`doctor` holds no token and makes no platform calls. **PARTIAL:** it currently
performs the token-free assertion and prints the webhook command. The local
conflict probe and the daemon-context checks described above are specified but
**not yet implemented** — do not rely on the doctor to catch a stray poller or a
`PATH`/volume problem.

## 3am page

- **Health reasons** — read the health file; freshness equals liveness.
- **Conflict vs rate limit** — a conflict is another consumer; a rate limit is a
  definite refusal.
- **Dead-letter table** — see below.
- **How to stop it safely** — not `kill -9` unless you accept the lease TTL.
- **Restart-on-crash-only means a clean kill stays down.** This surprises
  everyone once.

## Dead letters — the file is the instruction

There is no team to ask. Open the dead-letter record, then open the chat.

| What you see | Do |
| --- | --- |
| Message **is** in the chat | **Stop.** Do not resend. Leave the records. |
| Not there, outcome **refused** | Fix the cause, send new text. |
| Not there, outcome **ambiguous** | Human decision only. Never automatic. |

**Never delete the attempt record.** It is a truthful account of what the sender
could observe at the time; editing it to match hindsight destroys the forensics
the ledger exists to provide.

### Worked example

A transient network fault produced an ambiguous outcome. The system refused to
auto-retry and dead-lettered for a human. The send had in fact **succeeded** —
only the response was lost.

An auto-retry design would have double-posted. This is why ambiguity is never
resolved by trying again.

## Canary

A local timer **you own** (cron, launchd, or a systemd user unit) runs the send
helper against a fixture letter on disk and logs locally. You confirm receipt in
the app.

A missed week does not reset anyone's calendar — it means you **lack evidence the
send path is alive**, and should establish that before relying on it.

**The timer must never be given the ability to ring.** That is a security
boundary, not a convenience. Do not widen the notifier to make a reminder easier.
