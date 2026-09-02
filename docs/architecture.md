# Architecture — why decomposed, not fused

## The failure this design exists to prevent

A fused channel plugin puts transport, durability and attention in one process.
When that process dies mid-flight, the message is gone — the platform's retention
window is the only thing between an outage and permanent loss.

A decomposed substrate writes the message to disk first, then notifies. Every
process after the write is an accelerator, and every accelerator is allowed to
fail.

## The four roles

Called roles, not processes, deliberately: in v0.1 the resident bridge runs
poller, confirm and notifier in one OS process, and the watchdog is a library
behind `alb --status`. The unequal privilege is enforced at module boundaries
and proved by tests — the poller code path is never handed a transport, the
watchdog reads only mirrored state — not by OS isolation. Splitting into real
processes is possible precisely because letters-on-disk is the only interface,
but v0.1 does not claim it.

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


## Running alongside an inter-agent letterbox

If you also run a system that carries mail *between* agents on one machine, you
will have **two stores**: that system's inbox, and this bridge's `--root/inbox`.
Two directories to sweep, and two frontmatter shapes, because they were designed
for different senders.

That is correct for a standalone install and it is the shipped default — this
bridge must work when nothing else is present.

**Integrated mode ships.** `--mail-root` points letter publication at the other
system's inbox, in the standard envelope, so there is one store, one sweep and
one set of tooling — while `--root` keeps every private file where it belongs.
The two are deliberately separate settings: mail may travel, state may not.

In standalone mode the doorbell names the store it means ("the bridge
inbox") rather than a product, because in two-store mode "check your letterbox"
sends the reader to the wrong one.

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
