# Contributing

## Non-negotiables

Read `docs/invariants.md` first. The invariants are the product; a change that
weakens one is not a change we can take, however tidy it is.

**Zero third-party runtime dependencies in the core.** A PR that adds one to the
poller, send helper, watchdog or doctor will be declined. This is a security
property, not a style preference — the audit has to stay finishable.

**Every invariant needs a mutation-proved test.** A test that passes when the
invariant is disabled is not a test of that invariant.

**Fixtures are synthetic.** Never copy a real message, chat id, username or path
into a fixture. This is a privacy control, not a testing preference: git history
makes a leak permanent.

**Honest labelling.** A partially-met criterion is labelled PARTIAL. Do not pad
it to look complete.

## Claims

Every claim in the docs must be traceable to the mechanism that makes it true.
If you cannot point at the mechanism, narrow the claim. Absolutes that sound
strong and are disprovable discredit the true claims beside them.

## Style

Python 3.11+, standard library only. Match the surrounding code.
