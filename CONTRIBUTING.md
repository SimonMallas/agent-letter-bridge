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

## Set up the hooks — they are not automatic

Git hooks are per-clone local config. A fresh clone inherits nothing, so run this
once after cloning:

```sh
git config core.hooksPath .githooks
```

That wires the `pre-commit` privacy scan and the `commit-msg` trailer check. CI is
the real gate and runs regardless; the hooks just fail faster.

## What the privacy scan does and does not cover

The scan uses **structural** patterns — absolute home paths, volume paths,
service-manager domains, token-shaped strings, assistant trailers, machine
identifiers. It deliberately contains no list of private strings, because such a
list in the repo would be the leak it exists to prevent.

**It therefore cannot catch semantically private names** (an internal tool or host
name that looks like an ordinary word). Supply those out of band via
`ALB_EXTRA_PATTERNS`, a path to a newline-separated regex file that is never
committed. **CI does not set it**, so that class is covered by review and the
pre-release history audit — not by the automated gate. Do not assume CI covers it.

## Claims

Every claim in the docs must be traceable to the mechanism that makes it true.
If you cannot point at the mechanism, narrow the claim. Absolutes that sound
strong and are disprovable discredit the true claims beside them.

## Style

Python 3.11+, standard library only. Match the surrounding code.
