# Security

## Reporting

Open a private security advisory on this repository. Please do not open a public
issue for a vulnerability.

## What this project does with your credentials

**The project operates no service and never receives your token.** Your token is
stored locally, in a file you create with `0600` permissions, and is sent only to
your chosen platform's API from your own machine.

Inbound and outbound messages necessarily traverse that platform. We do not claim
otherwise.

## Design posture

- **Zero third-party runtime dependencies** in the core, so the audit is
  finishable. Clone it, read it, run it.
- The allowlist ships **deny-all** and fails closed.
- The token appears in no log, no error message, no process argument, and no test
  fixture.
- The `doctor` command holds no token and makes no platform calls.

## Stated limitations

Read `docs/threat-model.md` for the full list. Two matter most:

1. **A compromised or modified poller can send.** One token serves both
   directions; there is no credential separation. The guarantee is that the
   shipped poller has no supported send path, enforced by test.
2. **A `getUpdates` consumer on another machine cannot be detected before first
   poll.** If your token's history is unknown, revoke and re-issue it.

## Install trust

Pin a release. Do not install from a moving branch.
