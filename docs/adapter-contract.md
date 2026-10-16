# Adapter contract — the seams

The most dangerous thing this project can do is let people configure the parts
that must never vary. Both lists are explicit.

## Varies — parameterise freely

Platform (Telegram / Slack) · seat identity · lock target name · state and
env paths · allowlist contents · token · poll cadence *within validated
bounds* · notification transport (cmux / tmux / herdr / none) · store layout
root.

## Must stay FIXED

Varying these is how people hurt themselves.

- Offset-after-publish ordering
- Delivered-ids dedupe
- Atomic publish
- Two-fence parsing
- Exact-id letter resolution — substring globs misdeliver
- Destination chat read from the letter on disk
- Allowlist fail-closed at **both** inbound and send
- Claim-before-send `O_EXCL` ledger
- Ambiguous outcome dead-letters and never auto-retries
- Token never in argv, never logged, never in a letter
- **The doctor boundary** — no token, no platform calls, no `getUpdates`, local
  state only
- **Zero third-party runtime dependencies** in the core (poller, send, watchdog,
  doctor)
- **One language across all four processes.** Letters on disk would technically
  permit mixing; a stranger cannot audit four runtimes as easily as one.
- **No `kqueue`/FSEvents requirement.** `selectors` or a poll fallback only.

Every rule in this list holds **per platform**. A platform adapter is added by
meeting all of them, never by relaxing one: the Slack adapter keeps the same
ack-after-durable-write order (its envelope acknowledgements are sent from
`confirm()`), the same delivered-ids ledger (keyed on workspace, conversation
and message timestamp), the same allowlist at both ends, and the same
classification of a send outcome. Its WebSocket client is standard library
only. [`slack.md`](slack.md) maps each rule.

## Platform support

Declared, not promised: **macOS** (launchd) and **Linux** (systemd user units),
with the daemon-permissions class documented for macOS.

**Windows is a declared gap.** Do not promise it.
