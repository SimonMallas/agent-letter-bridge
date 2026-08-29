# doctor

Local diagnostics. **No token. No platform calls. No `getUpdates` — ever.**

## What it does today

- `env_is_token_free()` — asserts this process holds no credential-shaped
  variable. Hermes' constraint, promoted from policy to a testable invariant.
- `webhook_check_command()` — prints the read-only `getWebhookInfo` command for
  the **operator** to run in their own shell. The doctor never runs it, because
  running it would mean holding the token.

## What it does NOT do yet — PARTIAL

The local single-consumer conflict probe (process, service-manager, lock and
cron inspection) and the daemon-context permission checks are **specified but
not implemented**. Do not tell an operator the doctor will catch a stray poller
or a `PATH`/volume problem. It will not, yet.

## Why the boundary

A doctor that polls is the very thing it exists to detect. A `getUpdates`
conflict probe is forbidden and uninterpretable: an "ok" may mean it just
terminated another consumer's in-flight request, and telling which side of the
conflict you were on requires repeating it — the loop the boundary forbids.
