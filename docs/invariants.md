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

**The doctor makes no platform calls, carries no credential of its own, and
never calls `getUpdates`.**
It proves local state only — process, service, lock, cron — and asserts its own
environment holds no token. Telling one bridge from another is the single
exception to "reads no credential": it loads each candidate's config through
the same loader the runtime uses, inheriting that loader's refusal to read a
token file others can read, and keeps only the bot id. The secret half is
discarded inside the parser and reaches no caller, report or log. That
identification is an inference from a file rather than proof of what a live
process loaded, and the report says so rather than implying otherwise. A `getUpdates` conflict probe is **forbidden and
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
remembered, never configured, never inferred — **and checked against the
bridge's private message index.** Where the bridge recorded the chat it
published that letter from, a letter naming any other chat is refused. A
letter from before that record existed has none to check, and is bounded by
the allowlist alone.

## Outbound

**Outbound claims before it sends** (`O_EXCL` ledger), so a replay or restart
refuses rather than double-sending.

**An ambiguous outcome dead-letters for a human. It never auto-retries.**
The platform send has no idempotency key, so ambiguity cannot be resolved by
trying again. A definite refusal (for example a rate limit) is the narrow
carve-out and is not ambiguous.

**A missing mailbox is refused, never invented.**
`--mail-root` names a directory that already belongs to some agent; one that
does not exist is nobody's, so it is a typo. Creating it would put every letter
into a tree nothing sweeps while the doorbell sends the real agent to an inbox
that stays empty.

**The mail root is the recipient's own mailbox.** A letterbox keeps each
agent's mail at `<letterbox>/<agent>`, so a mail root whose last part is not
`ALB_TO` is refused before anything is fetched. Given the whole letterbox, the
bridge wrote letters to `<letterbox>/inbox`, which no agent reads.

## Operational

**A platform conflict is a yield, not an error.**
The losing consumer exits cleanly so the holder runs — but a clean exit under a
restart-on-crash-only policy is how a poller dies silently, so the exit
discipline must be stated wherever it is used.

**Liveness means a COMPLETED cycle, not a poll.**
The heartbeat is written only after fetch, durable write and platform confirm
have all succeeded, so freshness equals "the whole path works" and a supervisor
needs no cooperation from the process to judge it. It was once written per poll,
which meant a bridge whose confirm failed every cycle — consuming nothing —
still looked healthy. A poll is not a completed cycle.

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
landing together produce a mangled ring, not a hazard — both payloads are fixed
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

## Discord: the same invariants, mapped

| Invariant | On Discord |
| --- | --- |
| Ack only after the durable write | Discord has no acknowledgement and does not retry missed events beyond a short Gateway resume. What moves is the per-channel marker, and only in `confirm()`, after the letter exists. A crash in between loses the gateway copy; the next start reads history after the marker and publishes it then. |
| Delivered-ids ledger | Keyed on channel and message ID, which Discord makes unique. The guild and a thread's parent are provenance only. |
| The platform holds what the bridge missed | A reconnect resumes only a short gap. On connect the bridge reads the history of each allowlisted channel, and of up to 200 threads where it has been mentioned, after the last message it recorded. A message that arrives both ways lands once. A channel whose read failed keeps its marker until a read succeeds. |
| Liveness means a completed cycle | Discord's heartbeat must be acknowledged. An idle cycle completes only if an acknowledgement arrived within the heartbeat interval; a missing one closes the connection and resumes. |
| Allowlist fail-closed at both ends | An entry is `<channel>/<user>`; both must match. Checked again at send. |
| Ambiguous outcome never retried | A Discord server error, a broken connection or an unreadable response after a send left is ambiguous and dead-letters. A 429 is the one provably unsent outcome. |
| The poller has no send path | The Discord receiving object has no send method. Sending is a separate object. Both hold the one bot token. |
| Token hygiene | The token lives only in the mode-600 config. It never appears in argv, a log line, an error, a `repr`, a letter or a test fixture. |

## Slack: the same invariants, mapped

A second platform keeps every invariant here; none is relaxed for it. Where
Slack's mechanics differ, the rule is restated in Slack's terms:

| Invariant | On Slack |
| --- | --- |
| Ack only after the durable write | A Socket Mode envelope is acknowledged from `confirm()`, after its letter exists. Slack redelivers an unacknowledged envelope, so a crash in between causes a redelivery, never a loss. |
| Delivered-ids ledger | Keyed on workspace, conversation and message timestamp. A thread timestamp is provenance only, never part of the key. |
| The platform holds what the bridge missed | Slack retries an unacknowledged event three times, the last about five minutes after the first. On every connect the bridge reads conversation history since the last message it recorded and publishes anything new through the same path; a message that arrives both ways lands once. The per-conversation marker advances only in `confirm()`, and not at all for a conversation whose catch-up failed. |
| Liveness means a completed cycle | A cycle completes when events are fetched, written and acknowledged. When the socket is idle, a frame from Slack or an answered ping is required; silence is a transient failure and a reconnect, never a fresh heartbeat. |
| Allowlist fail-closed at both ends | An entry is `<conversation>/<member>`; both must match. Checked again at send. |
| Ambiguous outcome never retried | A Slack server error, a broken connection or an unreadable response after a send left is ambiguous and dead-letters. A 429 is the one provably unsent outcome. |
| The poller has no send path | Stronger than Telegram: the Slack receiving object has no send method at all. Sending is a separate object holding only the bot token. The token is still not credential-separated: the receiving side holds the bot token too, to read history and download files. |
| Token hygiene | Both tokens live only in the mode-600 config. Neither appears in argv, a log line, an error, a `repr`, a letter or a test fixture. |
