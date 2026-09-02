# Operations

Written for a stranger at 3am with nobody to ask.

**Installing for the first time? Read [`../INSTALL.md`](../INSTALL.md) instead.**
This page is reference: it explains why each thing is the way it is, and what
to do when it breaks. It is not ordered as a walkthrough.

## Before Day-0: the four things you need

None of these are in the repo, and the bridge cannot work without them.
`alb --init` collects the last three and creates the files; this section is what
it is collecting and why each one matters. Read it if setup asked you something
you did not expect.

**1. A bot token.** In your chat app, talk to `@BotFather`, send `/newbot`, and
follow the prompts. It gives you a token like `123456789:AA...`. **If you
inherited a bot from anywhere, revoke and re-issue the token** — Day-0 step 3
explains why.

**2. Your own chat id.** Send your new bot any message, then run this once. It
is read-only and consumes nothing:

```sh
curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["message"]["chat"]["id"])'
```

That number is your chat id, and it is the only sender the bridge will accept
until you add more.

**Take the `chat` id, not the `from` id.** In a direct message they are the same
number, so a wrong recipe appears to work. In a group they differ — `from` is
the person, `chat` is the group — and an allowlist holding the wrong one denies
everything. That denial is silent by design, so it looks exactly like a dead
bot, which is the situation the first-hour test below exists to prevent.

**3. An allowlist file.** This is the one thing that stops a stranger reaching
your agents, and **the bridge delivers nothing until it exists.** Create
`allowlist.json` in your state directory:

```json
{"chats": ["YOUR_CHAT_ID"]}
```

The list is exact-match and fail-closed: **missing, empty, malformed, or the
wrong shape all deny everything**, and there is no setting that opens it. If
nothing arrives, run `alb --doctor` — it says plainly whether the allowlist is
the reason.

**4. A surface id for the ring — OPTIONAL.**

Two transports ship. Choose with `ALB_NOTIFIER`; an unsupported value is
**refused by name**, never silently defaulted.

**cmux** (`ALB_NOTIFIER=cmux`, the default) — surface id from:

```sh
cmux --id-format uuids tree --all
```

**tmux** (`ALB_NOTIFIER=tmux`) — pane id such as `%1` from:

```sh
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'
```

A typo in the key itself — `ALB_NOTIFER` — is also refused. An unread setting
that appears to have worked is worse than an error, which is how a real
deployment ran for days believing it had selected a transport nothing read.

Leave `ALB_SURFACE` unset and the bridge runs happily without a multiplexer:
mail lands durably and nothing pings. `alb --status` reports the ring as
`disabled` with the reason, so a missing bell is never confused with a broken
one. Add the surface later when you have a multiplexer.

The ring types a line into the pane and presses return. If something is already
half-typed there, the doorbell appends to it and submits the combination.

**In practice this rarely bites here, and the reason is structural:** the ring
is triggered by *you sending a message*. If you are texting, you are not
simultaneously typing at that prompt. Unlike an agent-to-agent doorbell, which
can arrive at any moment, this one is caused by the human being elsewhere.

A dedicated agent pane removes even that, and is worth preferring if you have
one. It is a recommendation, not a requirement — and worth knowing rather than
worrying about. The pane your agent sits in, as your
multiplexer names it. In cmux:

```sh
cmux --id-format uuids tree --all
```

Take the id of the pane holding your agent. It is pinned, so **re-pin it after
any multiplexer restart** — see below.

Your `bridge.env`, mode `600` — `alb --init` writes this for you, and the rest
of this section is what it wrote:

```
ALB_TOKEN=123456789:AA...
ALB_SURFACE=<the id of your agent's pane>
```

## Integrated mode: letters into a mailbox you already sweep

If the agent already has an inter-agent letterbox, point the bridge's **mail**
at it and leave its **state** where it is:

```sh
alb --config bridge.env --root ~/.alb/grok --mail-root ~/shared-brain/bus/grok-build
```

Letters land in the existing inbox in the standard envelope, and the ring goes
through the letterbox's own doorbell helper — so it matches the skill the agent
already has and there is nothing to teach.

**`--root` and `--mail-root` are separate on purpose.** Everything private stays
under `--root`: the allowlist, the lock, the dedup ledger, the offset, health,
canary and dead letters. The mailbox receives **letters and nothing else** — no
state directory, and its own permissions are left exactly as found, because it
holds files belonging to something that is not this bridge.

**One running bridge per mailbox.** The lock is per `--root`, so two bridges with
different state directories pointed at one mailbox would both write to it. That
is an operational rule, not something the lock can enforce — putting a lock file
in a shared mailbox would be the contamination this design exists to avoid.

`ALB_SURFACE` is not needed in integrated mode: the letterbox helper resolves
the recipient's registered pane itself. `ALB_TO` must be the agent's exact
participant name, since that is who the doorbell is addressed to.

## Onboarding an agent that already has a doorbell

**This section is for STANDALONE mode only.** If you used `--mail-root` above,
none of it applies: letters arrive in the inbox the agent already sweeps and the
ring is the one they already recognise, so there is nothing to teach.

**In standalone mode, if the agent you are waking already handles inter-agent
mail, the transport working is not enough — the agent has to know this bridge
exists.**

Its doorbell is a different line from any other doorbell convention, and points at
a different directory. An agent with an existing sweep will receive the ALB
ring, fail to match it, run its usual check, find nothing, and reasonably
conclude the bridge is broken. Everything will have worked.

Before the first live message: give the agent `docs/agent-setup.md`, or the
equivalent in whatever form your agent takes instructions. It needs one line and
one path — the doorbell it will receive, and the inbox to read.

**Do not unify the two by pointing `--root` at an existing mail directory.** The
bridge creates private state in its root — offset, lock, ledger, dead letters,
canary log — and a shared mail directory should not inherit that file's umask,
backup story or lifecycle. Two stores is the standalone
default; one store is integrated mode, which ships — use `--mail-root`, which
keeps the private state where it belongs rather than merging the two.

**One bot per agent.** The platform permits exactly one consumer per token, so
sharing a bot between agents is not a configuration choice you have — it is a
conflict. Each agent gets its own bot, its own state root, and its own pane.

## Day-0, in order

The ordering is the content. Do these in sequence.

1. **Install and initialise.** Clone into an empty directory you choose — never
   drop it next to an existing poller — then install and run `alb --init
   --root <your state dir>`, which creates the directory 0700, `bridge.env`
   0600, and a deny-all `allowlist.json` 0600. It refuses to run without a
   terminal, so it cannot be fed answers by a script.

   ```sh
   pipx install .          # or: uv tool install .
   ```

   For a service, use a dedicated venv instead: a unit file needs an absolute
   path that belongs to you, not a tool's internal layout.

   ```sh
   python3 -m venv ~/.alb/venv && ~/.alb/venv/bin/pip install .
   ```
2. **Create your own `0600` env file.** Never copy one from an existing plugin.
3. **Revoke and re-issue the bot token.** This is a **Day-0 gate, not a footnote
   and not incident response** — an inherited bot is the *common* case. Proving
   absence on an old token is the one thing nobody can do; a re-issued token is
   one you alone hold, which makes single-consumer true by construction rather
   than by inspection. Everything below gets easier once this is done.
4. **Prove no local consumer exists** — process list, service managers, lock
   holders, cron — **including across a restart.** Policy-disabled is not
   inbound-off.
5. **Settle the webhook case.** `doctor` prints a read-only `getWebhookInfo`
   command; you run it in your own shell. If a webhook is set, the remedy is
   `deleteWebhook` or a token re-issue — polling cannot coexist with it.
   **The ring requires a multiplexer** (cmux or tmux) with a uniquely
   identified pane. v0.1 has no notifier that works without one.
6. **Start it, then run the Day-0 test — inbound only.** A message from a listed
   sender must produce a letter. A message from an unknown sender must produce
   **silence**. Outbound is not a Day-0 step: the send helper replies only to a
   stored inbound letter, so there is nothing to reply to yet.
7. **Verify the ring for real, once.** Send yourself a message and watch the
   ring arrive in the pane. This step cannot be skipped or inferred: the code
   deliberately swallows ring failures so a dead notifier never costs a letter —
   which means a broken ring is **silent**. Mail landing with no bell is a
   failure state, not a quieter mode of working. `state/ring-health.json`
   records the last outcome, but only a real ring proves the transport.
8. **Know the rollback** that restores *your* previous consumer.

### The first-hour allowlist test

A correctly-working fail-closed allowlist is **indistinguishable from a dead
bot**. Silence from an unknown sender is the deny path *succeeding*.

If you skip this test, the first thing you will do when "nothing arrives" is
disable the security control to fix it. That is the predictable disaster this
paragraph exists to prevent.

### The poller cannot send — with the honest limit

The shipped poller has no supported send code path, enforced by test. Day-0 must
not imply one binary does both jobs.

But the bot token is the same credential in both directions. This is a
code-discipline guarantee, **not** credential separation. Anyone modifying the
poller can send.

### Degraded mode

With the notifier absent or dead, mail still lands on disk and nothing pings your
terminal. That is designed behaviour, not a fault — you find the mail by looking.

## Daemon context is not terminal context

This is the failure that will cost you a morning.

A process started by a service manager gets a different environment from the same
command typed into a terminal: a different `PATH`, and on some systems different
permissions for external or removable volumes — so a write that works by hand
fails with a permission error under the daemon.

Two rules follow:

1. **Pin the absolute path** of the interpreter you intend to run. Never rely on
   `PATH` resolution in a unit file. Version managers are invisible to service
   managers.
2. **Reproduce daemon-only failures with a throwaway probe job** under the same
   service manager. Testing by hand exercises the wrong context and proves
   nothing.

`doctor` holds no token and makes no platform calls. Run it with:

```sh
alb --doctor --root /path/to/state
```

It reports: whether this process holds a credential, whether another bridge is
running locally, the lock state, the interpreter actually in use, whether `cmux`
resolves on this `PATH`, and a warning if a version manager is on `PATH` (a
service manager will not see it).

It then states plainly what it **cannot** prove: a consumer on another machine
is not detectable from here, and neither is a webhook — that check is a
read-only command it prints for you to run. If the token's history is unknown,
revoke and re-issue it; that makes single-consumer true by construction, which
no amount of probing can.

## After a multiplexer restart: RE-PIN THE SURFACE

`ALB_SURFACE` is a pinned id. **A multiplexer restart usually kills it**, and
the ring then fails silently — letters keep landing, nothing pings, and
`state/ring-health.json` is the only tell. This is the most likely way your ring
dies, and it looks exactly like nothing being wrong.

After any multiplexer restart: get the current surface id, update the env file,
restart the bridge, and send yourself one message to confirm the doorbell.

## Do not tune the poll interval

`--interval` is the pause between cycles, not how fast messages arrive. **The
wait is the long poll**: the platform holds the connection open until a message
appears, so a message is delivered the moment it exists regardless of this
number.

Shrinking it does not make the bridge faster. It makes it hammer the API for
nothing, and it is the first thing an operator reaches for when something feels
slow. If delivery is slow, the cause is elsewhere — check `alb --status`.

## Is anything wrong? Read-only, no token

```sh
alb --status --root /path/to/state
```

Reports bridge liveness (freshness of the heartbeat) and the last ring outcome.
Exits non-zero when the bridge is not ok, so a monitor can use it directly. It
reads files only — no config, no token, no network.

## Permissions

The bridge creates its directories `0700` and every state file `0600`, from the
first run. Files are created with those permissions rather than corrected
afterwards, so nothing is briefly readable while it is being written.

**If you created the directory yourself before installing, check it.** A
directory made with a normal umask is `0755` and world-listable, and the state
holds a canary log naming the chats you have messaged, an offset describing your
traffic, and dead letters quoting failed sends.

```sh
chmod -R go-rwx /path/to/state
```

## Moving machines

Preserve the whole `state/` directory, and **`state/delivered.json` above all**.
It is the record of which platform updates have already become letters: without
it, everything the platform still retains is delivered again. The other ledgers
degrade gracefully; that one is the difference between a clean move and a
re-run of your recent history.

## 3am page

- **Health reasons** — read the health file; freshness equals liveness.
- **Conflict vs rate limit** — a conflict is another consumer; a rate limit is a
  definite refusal.
- **Dead-letter table** — see below.
- **How to stop it safely** — `kill -9` is safe for the lock. It is an
  `flock`, released by the kernel when the process dies, so there is no lease
  to expire and no stale lock file to clean up before restarting.
- **Restart-on-crash-only means a clean kill stays down.** This surprises
  everyone once.

## Unit files: pin absolute paths

The shipped entry point uses `#!/usr/bin/env python3` and calls `cmux` by name.
Neither is safe under a service manager, which does not share your shell's
`PATH` — you will get the wrong interpreter, or no `cmux` at all.

**Copy-paste unit files ship with this repo**: `examples/launchd.plist` and
`examples/systemd.user.service`. Use those rather than transcribing the
paragraph below — strangers copy files, not prose.

In your unit file, invoke the absolute interpreter and the absolute script:

```
/opt/homebrew/bin/python3 /path/to/alb --config /path/to/bridge.env --root /path/to/state
```

and make sure the directory containing `cmux` is on the unit's `PATH`.
`doctor` reports its own interpreter so you can confirm which one a daemon
actually resolved.

## Dead letters — the file is the instruction

There is no team to ask. Open the dead-letter record, then open the chat.

| What you see | Do |
| --- | --- |
| Message **is** in the chat | **Stop.** Do not resend. Leave the records. |
| Not there, outcome **refused** | Fix the cause, send new text. |
| Not there, outcome **ambiguous** | Human decision only. Never automatic. |

**Never delete the attempt record.** It is a truthful account of what the sender
could observe at the time; editing it to match hindsight destroys the forensics
the ledger exists to provide.

### Worked example

A transient network fault produced an ambiguous outcome. The system refused to
auto-retry and dead-lettered for a human. The send had in fact **succeeded** —
only the response was lost.

An auto-retry design would have double-posted. This is why ambiguity is never
resolved by trying again.

## An honest limit: the inbound process can send

`adapters/telegram/api.py` is one class that both fetches and sends, so the
running inbound process has the send capability loaded even though the poller
package itself is send-free and proved so by test.

This is **code discipline, not process isolation** — the same honest limit
already stated for the token. Anyone modifying the running bridge can send.

## Canary

A local timer **you own** (cron, launchd, or a systemd user unit) runs:

```sh
alb --config /path/to/bridge.env --root /path/to/state --canary
```

It sends to your own allowlisted chat through the **real** send path — allowlist,
claim ledger, platform — because a canary that bypasses those tests nothing worth
testing. It logs to `state/canary.log`, and it refuses rather than inventing a
destination if no chat is allowlisted.

**You confirm receipt in the app.** Nothing here can prove the message arrived,
only that the send path accepted it, which is why the confirmation is a human
step and stays one.

The canary's fixture letter lives in `state/canary/`, never the inbox: a fixture
is not mail and must never be swept or acted on as though a person sent it.

A missed week does not reset anyone's calendar — it means you **lack evidence the
send path is alive**, and should establish that before relying on it.

**The timer must never be given the ability to ring.** That is a security
boundary, not a convenience. Do not widen the notifier to make a reminder easier.
