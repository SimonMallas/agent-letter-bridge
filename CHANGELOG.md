# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] — 2026-09-02

First complete release (0.1.0 tagged earlier the same day; 0.1.1 lands the
findings of a full-repo consistency review performed by an outside agent —
init writes the pasted pane id before the resident offer reads the config,
claims scoped to what the envelope actually records, roles-not-processes
stated honestly, integrated `--mail-root` documented with its true shape, and
one voice across package metadata, CLI help and docs). Private until the
repository owner flips it; the version marks "finished", not "published".

### Highlights since the pre-release notes below
- `alb --init`: interactive setup that owns every boilerplate step — 0700
  state directory, mode-600 config, DENY-ALL allowlist — asks only what no
  program can derive, never overwrites, never invents an allowlist entry,
  never touches the network unless explicitly asked, and ends by starting
  the bridge in its own cmux pane (or printing the exact command when it
  cannot).
- Integrated mode (`--mail-root`): letters delivered into a mailbox the
  bridge does not own, private state strictly separated, ring through the
  mailbox's own doorbell helper with the outcome parsed rather than assumed.
  A missing mailbox is refused, never invented.
- Cycle report: `fetched N · published N · denied N (allowlist)` — the deny
  visible to the operator while the sender still hears silence; duplicates
  counted apart from denials. Counts, never identities.
- Refusals grown from live installs: placeholder `ALB_SURFACE` values
  refused by name; unknown config keys refused; `--version` answers without
  a state directory.
- Two install routes: `INSTALL.md` for people, `docs/agent-install.md` as a
  brief for a CLI agent installing on someone's behalf — the latter hardened
  by three real agent wrong-turns the same day they happened.
- `docs/COMPARE.md`: the field, checked against each tool's current code and
  docs, our own limitations stated first, every citation dated.
- Verified live by three agents — Claude Code, Grok Build, Codex — including
  a full phone → letter → doorbell → read → reply loop, and an install
  performed end-to-end by the agent itself from the docs.

### Proven by
277 tests, 85 mutation-pinned invariants (each proved by disabling it and
watching the suite go red), privacy and dependency gates in pre-commit and
CI, and the live deployments above.

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
- Verified live against a sacrificial bot. (Superseded above: since this note
  was written, three agents have run it live, including an install performed
  by the agent itself.)
