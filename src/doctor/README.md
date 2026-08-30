# doctor

Local diagnostics. **No token. No platform calls. No `getUpdates` — ever.**
The package has no network capability at all, and that is asserted by test.

## What it does

- Asserts this process holds no credential-shaped variable.
- Finds another bridge running locally, matching the EXECUTABLE at the head of
  a command line rather than the name anywhere in it — a probe that cries wolf
  teaches the operator to ignore it.
- Reports the lock state without taking the lock; acquiring it would evict the
  process it exists to observe.
- Reports the interpreter actually running, whether `cmux` resolves, and
  whether a version manager sits on `PATH` where a service manager cannot see it.
- Prints the read-only `getWebhookInfo` command for the operator to run.

## What it states it cannot prove

A consumer on another machine, and a webhook. Both are named in the report
rather than left as an implied all-clear, because the difference between a
limitation and a false assurance is whether you said it out loud.
