# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Pre-release. Not yet published for general use.

### Added
- Durable-letter store: atomic publish, two-fence parsing, exact-id resolution,
  path-shaped identifiers refused, delivered-ids ledger with a durable
  update-id lookup behind it.
- Fail-closed allowlist, deny-all by default, enforced at both inbound and send.
- Untrusted poller: letter to disk, then acknowledge. Denied senders produce
  silence and are still consumed.
- Notifier: fixed content-free line to one explicitly identified surface.
- Bounded outbound: replies only to a stored letter, claim before send,
  ambiguous outcomes dead-letter and are never retried.
- Watchdog reporting, and `alb --status` as the single should-I-worry surface.
- `alb --doctor`: local single-consumer probe, daemon-context checks, and an
  explicit statement of what it cannot prove.
- `alb --canary`: proves the send path through the real send helper.
- Telegram and cmux adapters. `examples/` unit files for launchd and systemd.
- Mutation gate: every invariant is disabled in turn and the suite must go red.
- Privacy enforcement from the first commit, in hooks and CI.

### Notes
- The ring requires a multiplexer. There is no notifier that works without one.
- Verified live against a sacrificial bot; not yet run by anyone but its author.
