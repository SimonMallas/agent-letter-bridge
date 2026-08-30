# Architecture — why decomposed, not fused

## The failure this design exists to prevent

A fused channel plugin puts transport, durability and attention in one process.
When that process dies mid-flight, the message is gone — the platform's retention
window is the only thing between an outage and permanent loss.

A decomposed substrate writes the message to disk first, then notifies. Every
process after the write is an accelerator, and every accelerator is allowed to
fail.

## The four processes

Privilege is deliberately unequal. See `docs/invariants.md` for what each may
never do, and why each prohibition is testable.

```
platform ──▶ poller ──▶ [ letter on disk ] ──▶ notifier ──▶ terminal ring
              (untrusted)        │              (in-session, content-free)
                                 │
                    send helper ─┘  (bounded: replies only to a stored letter)

              watchdog ──▶ reads mirrored health, reports. Restarts nothing.
```

**The durable store is the only interface between delivery and notification.**
That seam is load-bearing, not taste: a merged poller-that-also-notifies works in
development and fails silently in the daemon context it was built for.

## Why the store is a filesystem

The durability guarantees are POSIX primitives — `O_EXCL`, hardlink, atomic
rename, `0600` — not library behaviour. They are inspectable by a stranger
without reading a dependency tree, and a mutation test can break exactly one of
them and watch a test fail.

The filesystem is also the history. There is no history UI and will not be one.


## Deliberately not built

Recorded so these are visible as decisions rather than gaps, and so nobody
adds them believing they were simply forgotten.

**No watchdog daemon.** `alb --status` reports; nothing restarts the bridge
automatically. Restarting belongs to the service manager. A process that
supervises the bridge is the monitor gaining authority over the thing it
monitors — the rule against that exists because we broke it once.

**No spawn-on-mail notifier in v0.1.** Waking an idle agent by launching a
headless run is a legitimate peer adapter, and it is specified — constant argv,
coalescing, an explicit privilege expansion. It is not in v0.1, and it must
never be a silent fallback when the live-pane ring fails: that is the emergency
hatch reimplemented in software.

**No auto-retry of an ambiguous send.** There is no idempotency key, so a retry
cannot be made safe. A human decides. This is not a gap to close later.

**No history UI.** The filesystem is the history.
