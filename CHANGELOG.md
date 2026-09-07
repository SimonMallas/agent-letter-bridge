# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.5] — unreleased

Both of these were found by the first installation done for real, on a seat
that had never had one, after the whole install path had already been audited
against fixtures.

**`init` could start a bridge from a different installation than the one that
ran it.** The autostart command was the bare word `alb`, handed to a new
shell, which resolves it from `PATH`. An operator who deliberately installed
into a dedicated environment, and ran `init` from it, got a resident running
whichever copy `PATH` found first — a different installation, possibly a
different version, and on a machine with several bridges, one shared with
another of them. The command now names the running installation absolutely:
the console script beside the interpreter, quoted as one argv so a path
containing a space survives the shell — a root with a space was rejected by
argument parsing, and an interpreter with one failed to execute at all, both
ordinary where a home directory carries somebody's name. What is printed and
what the pane runs are still the same bytes.

Where no command can be named that a fresh shell would resolve to this
installation, `init` declines to start one and says why. Offering
`<interpreter> -m alb` instead does not work for a source checkout: that
process can import the package only because of the path it was started with,
and a new shell inherits none of it. An autostart that reliably fails is worse
than no offer.

**The start warning reads the allowlist that was saved**, not the answers
given during setup. On a re-run those differ, and both directions were wrong:
an existing deny-all file is kept — correctly, nothing is clobbered — but if
setup had read a chat id it did not save, it reported delivery as possible and
offered to start; while an existing, populated allowlist was warned about as
deny-all. The gate on disk after keep-or-write is the one that gets read, and a
file that cannot be parsed counts as denying.

**Starting a bridge with a deny-all allowlist is now a deliberate answer
rather than the default.** The gate is unchanged and still denies everyone
until a chat id is added — that part worked exactly as intended, which is what
made it confusing: a bridge reported as running, messages sent, nothing
delivered, and no error anywhere to explain it. The offer now states plainly
that nothing will arrive, and defaults to no. An operator who means it can
still say yes.

## [0.2.4] — 2026-09-06

Two things a person installing this would have been misled by.

**The setup wizard told a correct install it was not an install.** It gated
on a terminal pane being chosen, but an integrated install does not ring
through a pane — it rings through the letterbox's own doorbell, and the
runtime asks for a pane only when there isn't one. So a correct install was
told "a poller with nothing to ping is not an install" and refused a start,
while working deployments ran exactly that configuration with a working
doorbell. The wizard now recognises the helper as the ring it is, and stops
asking integrated installs to pin a pane id that nothing ever reads.

**The single-consumer probe now compares bots rather than process names.**
The platform allows one consumer per token, so what matters is which bot a
process holds. The probe matched on the executable's name alone, and so
reported other relays that hold different bots and cannot compete. It now
reads the identifying half of each candidate's token — never the secret half,
which is dropped before any value is returned — and reports a process unless
its bot is positively known to be a different one. An unreadable config is
reported rather than cleared: being unable to prove a conflict is not the
same as proving there is none.

**A local network fault was reported as a Telegram outage.** Every transient
that was not a rate limit was recorded as `upstream_5xx`, so a dropped wifi
connection sent operators looking for a platform incident. `network` was
already in the vocabulary and nothing emitted it. It does now, matched on the
message prefix so a gateway error that merely contains the word cannot claim
the code.

> 0.2.2 and 0.2.3 were never published. Upgrading from 0.2.1 brings all three
> sets of changes at once, which is why the notes below cover more than one
> version.

## [0.2.3] — unreleased

The relay stops needing a person to notice it died - it needs an agent that wakes and asks.

An agent asks `alb --check` when it wakes: exit 0 nothing to do, 2 the
relay has been silent past a policy threshold, which is grounds to restart
it rather than proof it is dead, and here is how; 3 something a restart
will not fix. The allowance follows the state rather than one number for
everything, because a bridge waiting out a rate limit is quiet BECAUSE it
is behaving, and a bridge that has just started has not finished a poll
yet. Both used to read as dead.

`alb --stop` asks the running bridge to stand down and signals nothing.
A request names the exact run it was meant for, so one written for a
bridge that then crashes cannot stop the next bridge to start.

Failures reach a log file as well as the terminal, timestamped, so "is
it down" and "since when" have different answers. A broken log can never
stop the bridge.

The install refuses to finish without a bell. Pane discovery asks every
multiplexer present and the notifier follows the pane the operator picks,
so an id we never showed cannot be accepted and silently pointed at a
multiplexer that is not there.

## [0.2.2] — unreleased, canary only

**Not a release.** The version exists so a running bridge can say which code
it is, because a canary you cannot identify proves nothing.

Recoverable platform conditions stop being terminal. 429 and 5xx on fetch and
confirm are waited out rather than died on, honouring Telegram's documented
`parameters.retry_after` as a floor; 401 and 403 stay fatal, and 409 still
yields. A throttled send becomes `Throttled` rather than a permanent refusal,
keeps its claim, survives a restart as a deferred state rather than being
dead-lettered as ambiguous, and can be finished by retyping the same reply.

Three further defects were found and fixed in the same work, each after the
previous fix's gates were green: receipt ordering was lexical and broke past
nine events; resume was check-then-act, so two resumers could both send; and
the lock added to fix that built its path from unvalidated caller text.

## [0.2.1] — 2026-09-04

Package metadata correction. 0.2.0 shipped with `version = "0.1.1"` in
`pyproject.toml`, so `alb --version` reported the previous release on a tree
tagged v0.2.0 — a stranger could not tell which world they had cloned. The tag
stands as published;
this release makes the metadata tell the truth.

Also in this release: the README no longer describes the ring as something that
"only makes it faster" — letter first, then ring, and without a bell mail lands
in a dead drop and nobody is told. The Design section no longer dates its
isolation claim to v0.1.

## [0.2.0] — 2026-09-04

Durable outbound, correspondent identity and threading, and read-only
retrieval. First release published to a public repository.

### The letter is the claim

An outbound reply is written as a letter — created `O_EXCL` — **before** the
platform is touched. That create *is* the claim on the work, so a crash between
send and record cannot produce a silent double-send: the second attempt finds
the letter already there. Delivery events are immutable numbered files rather
than appended lines, so a crash mid-write cannot tear the record. Startup
reconciliation finds anything left in flight with no terminal event,
dead-letters it, and says so.

### Identity and threading

Correspondents are provenance, not participants: `from` and `to` remain
routable agent ids. Replies resolve on the exact `(platform, origin,
message_id)` triple in both directions — no prefix matching. One thread per
correspondent, stamped inside publish.

### Retrieval

`--list`, `--show`, `--search`, `--thread`, `--export`. Read-only, standard
library, no config and no token: reading your own records never needs a
credential.

### Coverage

The invariants are pinned by mutation testing. Automated coverage still uses
fakes — the suite proves the invariants, live runs prove the transports, and
those are different claims. The inbound path has real live mileage; the paths
added here are covered and reviewed but newer.

## [0.1.1] — 2026-09-02

First complete release (0.1.0 tagged earlier the same day; 0.1.1 lands the
findings of a full-repo consistency review —
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
  against the ways an install actually goes wrong.
- `docs/COMPARE.md`: the field, checked against each tool's current code and
  docs, our own limitations stated first, every citation dated.
- Verified live: a full phone → letter → doorbell → read → reply loop, and an
  install performed end-to-end by a CLI agent working from the docs.

### Proven by
Invariants pinned by mutation testing — each proved by disabling it and
watching the suite go red — privacy and dependency gates in pre-commit and
CI, and the live runs above.

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
- Verified live against a sacrificial bot. (Superseded above.)
