# Invariants

**The invariants are the product.** The poller is a few hundred lines; the value
is the failure-mode discipline around it. This document reads like a list of
incidents, because that is what it is.

Each invariant is testable, and each has a test that fails when the invariant is
disabled — proved by mutation, not merely asserted.

## Durability

**A letter's NAME is made durable, not only its bytes.** `fsync` on a file
guarantees its contents survive a crash and says nothing about the directory
entry pointing at them. Without a directory `fsync` after the link, a letter can
survive with its contents intact and no name — an unlinked letter, which is a
lost message. The same applies to an outbound claim, where losing the name
permits the double-post that claiming exists to prevent.

**The ledger, the offset and the health files are deliberately NOT fsynced.**
Losing any of them degrades to re-reading, which the letters-plus-dedup path
makes harmless. Paying a sync on every write to protect a fast path would be
cost without a guarantee.

**Letters are authoritative. Rings only accelerate.**
A ring is not a read, not a handled, not a turn-start. With the notifier absent
or dead, mail still lands on disk and nothing pings the terminal. That is
designed behaviour, not a fault.

**Ack only after the durable write.**
The platform is acknowledged after the letter exists on disk, never before.

**Exactly-once inbound is THREE mechanisms, never one.**
Drop any leg and you get duplicates only under conditions the user cannot
reproduce:
1. **Atomic publish** — temp file plus hardlink. Never a partial letter.
2. **Offset advances only after a successful publish.**
3. **A delivered-ids ledger** covers the crash window between them. It is
   consulted **before** publish and written **after** the letter.

That order is not arbitrary. Ledger-before-publish fails toward
*duplicate-with-evidence*; ledger-first would silently skip a redelivered letter
that never landed. Do not "simplify" it.

**Leases renew with margin.**
A lease renewed at its expiry is already lost. The worst-case loop bound must
stay under **TTL/3**, and startup fails fast if it does not — not a warning three
days later. Without the number the invariant is untestable: a mutation test
cannot bite on "margin".

## Privilege separation

**The poller is structurally incapable of ringing.**
This is a mechanism, not a promise: the test proves *incapability* — no ring code
path, no notifier credentials in the poller's context — not merely that it does
not currently ring.

**The shipped poller exposes no supported send code path**, and that absence is
structural and mutation-proved.

> **Stated limit, in the same breath:** the token is **not credential-separated**.
> One bot token serves both directions, so a modified or compromised poller could
> call send. This is code discipline, **not** an OS or process capability
> boundary, and must never be written as one. Anyone modifying the poller can
> send. We do not tell operators otherwise.

**The doctor holds no token, makes no platform calls, and never calls
`getUpdates`.**
It proves local state only — process, service, lock, cron — and asserts its own
environment holds no token. A `getUpdates` conflict probe is **forbidden and
uninterpretable**: an "ok" may mean it just terminated another consumer's
in-flight request, and disambiguating requires the loop the boundary forbids.

## Input handling

**Allowlist fail-closed by default, not by setting.**
Enforced at **both** inbound and send, and shipped **deny-all**: an empty
allowlist refuses everything, and a first-run step adds the first chat. An open
default in this class of tool is a CVE-shaped first issue.

> A correctly-working fail-closed allowlist is **indistinguishable from a dead
> bot**. Silence is the deny path succeeding. See `docs/operations.md`.

**Two-fence frontmatter parsing.**
A one-fence file must **never** parse, or body lines become routing metadata.
This is the fence-spoof class.

**A path-shaped identifier is refused, never resolved.**
Unchecked, a crafted id escapes the state directory.

**A stored-letter-id resolves to exactly one letter, or the operation refuses.**
Zero matches and many matches are both refusals. This is what prevents a reply
reaching the wrong chat. Substring globs misdeliver.

**The destination chat is read from the stored letter on disk** — never
remembered, never configured, never inferred.

## Outbound

**Outbound claims before it sends** (`O_EXCL` ledger), so a replay or restart
refuses rather than double-sending.

**An ambiguous outcome dead-letters for a human. It never auto-retries.**
The platform send has no idempotency key, so ambiguity cannot be resolved by
trying again. A definite refusal (for example a rate limit) is the narrow
carve-out and is not ambiguous.

## Operational

**A platform conflict is a yield, not an error.**
The losing consumer exits cleanly so the holder runs — but a clean exit under a
restart-on-crash-only policy is how a poller dies silently, so the exit
discipline must be stated wherever it is used.

**Liveness is visible from outside.**
A health heartbeat is written after *every* poll, so freshness equals liveness and
a supervisor needs no cooperation from the process to judge it.

**Supervision is not monitoring.**
Restarting is the **service manager's** job. The watchdog **reports only**.
Conflating them gives the monitor authority over the thing it monitors.

**No subprocess error is ever swallowed.**
Every boundary logs the real error verbatim. Two layers of silent stderr once
turned a permissions denial into a contention message and cost a morning.

**Token hygiene.**
The token appears in no log line, no error message, no process argument, and no
test fixture.

**Identity uncertainty fails closed.**

**A ring appends to whatever is already typed in the pane.** Recorded as a
known property, not a guarantee: a doorbell injected into a pane holding
unfinished input submits the combination. Whether a human is about to type
cannot be observed from outside the terminal, and clearing the line first does
not work — `ctrl+u`, `ctrl+c` and `escape` are all accepted by the multiplexer
and none of them clear the buffer.

**The exposure here is lower than for an agent-to-agent doorbell**, and for a
structural reason: this ring is caused by the operator sending a message, so it
correlates with them being away from the keyboard.

**Concatenation is only dangerous when one side is not a doorbell.** Two rings
landing together produce a mangled knock, not a hazard — both payloads are fixed
and innocuous, which is what the zero-content rule buys. So a pane that never
sees human keystrokes has the defect's teeth pulled entirely, with no detection
needed. That is why the dedicated-pane recommendation is the answer rather than
an interim measure.

**No prompt-free detector exists**, and this was established by falsification
rather than assumed. Three shapes were designed and all three failed at the same
point: each needed to tell an empty prompt from an occupied one, and each tried
to do so without looking at the prompt. Clearing the line does not work; the
last captured line is a status bar, not the input line; and the payload joins
the existing text with no separator, so a clean write and a dirty one are
identical in form. Do not re-derive this — the evidence is in the commit
history.
