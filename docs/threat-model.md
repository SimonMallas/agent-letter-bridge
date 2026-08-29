# Threat model

Five laws, each bought with a real incident. Plus the classes that were
embarrassing omissions until a review caught them.

## The five laws

### 1. One consumer per token

Two `getUpdates` consumers on one token is a hard platform conflict, not a
degraded mode.

"Proven before first poll" is stronger than one machine can deliver, so the
criterion is a composition — each part provable *where it is provable*, and the
undetectable case documented rather than implied away:

- **(a) No local consumer** — process list, service managers, lock holders, cron.
  Doctor-checkable, no token needed.
- **(b) No webhook** — a webhook set on the bot hard-conflicts with polling
  forever and is **invisible locally**. Closed by one read-only `getWebhookInfo`
  call, run by the operator. If a webhook is set, the remedy is `deleteWebhook`
  or a token re-issue; polling cannot coexist with it.
- **(c) No remote `getUpdates` consumer on another machine** — **not provable
  pre-flight.** Documented as a limitation, backstopped at runtime by the
  conflict-yield invariant, with a definitive remedy: **revoke and re-issue the
  token**, which converts an unprovable negative into an enforceable positive.

### 2. Ack only after the durable write

Otherwise an outage that outlives the platform's retention window loses messages
permanently.

### 3. External input becomes a file, nothing else

The ring carries zero content. Fence-required parsing; a forged fence is
rejected.

### 4. The monitor never depends on what it monitors

### 5. Allowlist fail-closed as a default, not a setting

## Additional classes

### 6. Identifiers are not paths

Any identifier that is path-shaped is refused rather than resolved. Unchecked, a
crafted id escapes the state directory.

### 7. A stored-letter-id resolves to exactly one letter, or the send refuses

Zero matches and many matches are both refusals. This is what prevents a reply
reaching the wrong chat.

### 8. Token hygiene

The token appears in no log line, no error message, no process argument, and no
test fixture.

### 9. Leases renew with margin

Worst-case loop bound under TTL/3; startup fails fast otherwise.

## Coverage required

Token theft · allowlist bypass · duplicate send · second consumer ·
body-in-ring · message body asserting its own routing metadata (fence spoof) ·
misdelivery via id collision · duplicate letter after redelivery · unauthorised
sender in both directions · notification spoofing · supply-chain trust for the
installer.

## Stated limitations

**A compromised or modified poller can send.** One bot token serves both
directions; there is no credential separation. The guarantee is that the shipped
poller has no supported send path, enforced by test. It is code discipline, not a
capability boundary.

**A remote `getUpdates` consumer on another machine cannot be detected before
first poll.** Revoke and re-issue the token if its history is unknown.
