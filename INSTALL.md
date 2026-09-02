# Install

A single path from nothing to a working bridge, in order. Roughly 15 minutes.

If you want to know *why* any step is the way it is, every step links into
[`docs/operations.md`](docs/operations.md). This page only tells you what to do.

**Installing this with a CLI agent rather than by hand?** Give it
[`docs/agent-install.md`](docs/agent-install.md) instead — same install, written
as a brief for an agent.

---

## Step 0 — Pick your mode. Everything after this depends on it.

Answer one question: **does the agent you are waking already receive mail from
other agents on this machine?**

| | **Standalone** | **Integrated** |
| --- | --- | --- |
| Your agent's mail today | nothing, or only this | already has an inter-agent inbox |
| Letters land in | a directory this bridge owns | the inbox it already sweeps |
| The doorbell | a new line it must learn | the doorbell it already knows |
| You must teach the agent | yes — one line, one path | no |
| Setup asks you for | nothing extra | that inbox's path, and the agent's name |

**If in doubt, choose standalone.** It owns everything it touches, so it cannot
disturb something already running. You can switch later by adding one setting.

`alb init` asks you this in Step 4. It does **not** work it out by looking: an
inbox existing on your disk is not you asking for letters to be put in it.

Steps marked **[integrated]** or **[standalone]** apply to one mode only.
Everything else applies to both.

---

## Step 1 — Check you have Python 3.11 or newer

```sh
python3 --version
```

**Expect:** `Python 3.11.x` or higher.

Older, or "command not found"? Install Python 3.11+ before continuing — from
[python.org](https://www.python.org/downloads/), `brew install python@3.12`, or
`uv python install 3.12`. Nothing else is required: this tool has **zero
third-party runtime dependencies**.

---

## Step 2 — Install the tool

From the repository directory:

```sh
pipx install .          # or: uv tool install .
```

**Check it:**

```sh
alb --help
```

**Expect:** the usage text. If `alb` is not found, your shell cannot see pipx's
bin directory — run `pipx ensurepath` and open a new terminal.

> **Planning to run it as a background service?** Use a dedicated venv instead,
> because a launchd/systemd unit needs an absolute path that belongs to you
> rather than to a tool's internal layout:
> ```sh
> python3 -m venv ~/.alb/venv && ~/.alb/venv/bin/pip install .
> ```
> Then point the unit at `~/.alb/venv/bin/alb`. Examples in [`examples/`](examples/).
> Do this at the end, once the bridge works by hand.

---

## Step 3 — Make the bot and get its token

In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts.
It returns a token like `123456789:AAH...`.

**If you inherited this bot from anything — another tool, an old script, a
teammate — revoke and re-issue the token now** (`/revoke` in BotFather, or
`/token` for a fresh one). The platform allows exactly one consumer per token.
Proving nothing else is holding an old token is the one thing nobody can do;
re-issuing makes it true by construction.

**This step cannot be automated and never will be.** BotFather is a
conversation you are in.

Then message your new bot once — anything, "hello" is fine. You need a message
to exist for the next step.

---

## Step 4 — Run `alb init`

```sh
alb --init --root ~/.alb
```

This does every remaining piece of setup that is typing rather than judgement:
creates the state directory `0700`, writes `bridge.env` at `0600`, and writes
`allowlist.json` at `0600` **denying everyone**.

It asks you four things:

**1. Does this agent already receive mail from other agents on this machine?**
A yes/no — you do not need a path to answer it. `n` (the default) gives you
standalone, which is the right answer unless you specifically want letters
delivered into an existing inter-agent mailbox.

Say yes and it asks two more: the directory containing that inbox, and the
agent's registered participant name. **Both are required.** Leave either blank
and it writes a standalone config and tells you it did — it will not hand you
something that looks integrated and is not. It also asks for your letterbox
helper's path, but only if that helper is not already on `PATH`.

**2. Your bot token.** Not echoed as you type, not shown again, and never
accepted as a command-line argument — a flag would put your token in shell
history.

**3. How to get your chat id — `read` or `print`.** Both are real options:

| | what happens | what you can then say |
| --- | --- | --- |
| `read` | one `getUpdates` call with your token; shows the ids it found; you pick yours | the wrong-id trap is impossible — it reads `chat.id` by construction |
| `print` | prints the command for you to run yourself | setup never touched the network at all |

`read` consumes nothing — it sends no offset, so your messages stay queued for
the bridge to collect properly later. Pick whichever you prefer; neither is the
safe answer for everybody.

**4. Your agent's pane, if you want the ring.** It lists the panes it can see.
**It will not pick one** — a listing can't tell which pane holds your agent, and
a ring typed into the wrong pane lands in someone else's session. Copy the id
into `bridge.env` as `ALB_SURFACE`.

### What it will not do

It never invents an allowlist entry, never overwrites a file that already
exists, never reaches the platform unless you choose `read`, and never picks a
pane or a mailbox for you. Re-running it on a working bridge is safe: it keeps
what is there and tells you what it kept.

### Checkpoint

```sh
alb --doctor --root ~/.alb
```

If you chose `print`, this will say **nothing will be delivered** — correct, and
the allowlist is still empty. Add your chat id to `~/.alb/allowlist.json`:

```json
{"chats": ["YOUR_CHAT_ID"]}
```

then run `--doctor` again and expect `DELIVERY: 1 chat(s) permitted`.

**Do not skip this.** A correctly-working fail-closed allowlist is
**indistinguishable from a dead bot** — both produce silence. `--doctor` is the
only thing that tells you which one you have. It reads files only: no token,
no network.

---

## Step 5 — What `init` wrote, if you want to change it later

`~/.alb/bridge.env`, mode `600`:

| Key | Meaning | Default |
| --- | --- | --- |
| `ALB_TOKEN` | your bot token | **required** |
| `ALB_SURFACE` | the pane to ring on | none — ring disabled |
| `ALB_NOTIFIER` | `cmux` or `tmux` | `cmux` |
| `ALB_TO` | who the letter is addressed to | `agent` |
| `ALB_FROM` | who the letter is from | `telegram-bridge` |
| `ALB_MAIL_ROOT` | **[integrated]** the inbox to deliver into | none |
| `ALB_BUS_BINARY` | **[integrated]** your doorbell helper | `bus.sh` |

An unknown key — including a typo like `ALB_NOTIFER` — is **refused by name**
rather than ignored. So is an `ALB_SURFACE` still set to a placeholder from
this document: a fake pane id fails silently forever, because ring failures are
deliberately swallowed so a dead notifier never costs a letter.

`chmod 600` is not advisory either — the bridge refuses to start on a
world-readable config. A bridge that starts wrong is harder to diagnose at 3am
than one that will not start at all.

---

## Step 6 — The ring: do this unless you have a reason not to

The ring types a line into your agent's terminal pane so it notices mail
immediately — **it is what makes the bridge feel alive.** Without it, letters
land durably and sit unread until something sweeps: a dead drop, not a dead
loss. The design's promise is narrower than "optional": the ring may *fail*
without costing a letter. Skip it only if your agent checks its own mail on a
schedule; `alb --status` reports the ring as `disabled` with a reason, so a
missing bell is never confused with a broken one.

`alb init` lists your panes in Step 4. To find them again yourself:

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
```

Add the id to `~/.alb/bridge.env`:

```
ALB_SURFACE=<the id of your agent's pane>
ALB_NOTIFIER=tmux          # only if you use tmux; cmux is the default
```

**Paste a real id, not the line above.** Placeholder values are refused by
name, because a fake pane id is the one wrong setting that produces no error
anywhere: ring failures are swallowed on purpose so a dead notifier never costs
a letter. Not setting `ALB_SURFACE` at all is fully supported and reports
itself as `disabled`.

**[integrated] Skip `ALB_SURFACE` entirely.** Your doorbell helper resolves the
recipient's pane itself. Set `ALB_TO` to the agent's exact participant name
instead — that is who the doorbell is addressed to.

> **Re-pin this after any multiplexer restart.** A restart invalidates pinned
> pane ids, and the ring then fails **silently** — by design, so that a dead
> notifier never costs you a letter.

---

## Step 7 — First run, and the tests that matter

**[integrated]** `alb init` already wrote `ALB_MAIL_ROOT` into your config, so no
extra flag is needed. If you ever pass `--mail-root` by hand, give it the
directory that CONTAINS the inbox (the mailbox), not the inbox itself — the
bridge creates `inbox/` and `processed/` inside it.

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
```

**Expect:** a line like `alb: fetched 0 · published 0`. `--once` runs a single
cycle and reports what it did, so a run that found nothing still says so.

### Test 1 — a listed sender produces a letter

Message your bot, then:

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
ls ~/.alb/inbox/          # [integrated] ls the agent's inbox instead
```

**Expect:** `alb: fetched 1 · published 1`, and one `.md` file containing your
message.

The first poll after setup will publish **every** message Telegram was
holding, not just the last one. Three real hellos become three letters in
one cycle. That is catch-up, not echo, and not the bot talking to itself.

**Nothing there?** Run `alb --doctor --root ~/.alb`. Nearly always the
allowlist (Step 3) or a token another process is already polling (Step 3).

### Test 2 — an unlisted sender produces silence

Have someone else message the bot, or temporarily put a wrong id in the
allowlist and message it yourself.

**Expect:** no new letter, no error, and `alb: fetched 1 · published 0 · denied
1 (allowlist)`.

The *sender* gets total silence — that is the security property. You, at the
terminal, get told the gate did it. Those two things being different is
deliberate: an operator who cannot tell a working deny from a dead bridge
eventually widens the allowlist to find out.

**Restore the allowlist afterwards.**

### Test 3 — the ring, if you set one up

Message your bot and **watch the pane — an idle one, with nothing half-typed
in its composer** (the doorbell submits whatever is already there along with
itself; that is documented behaviour, not a bug to discover on camera). You should see, typed as if by a
person:

```
you have new mail: check the bridge inbox
```

**[integrated]** you will see your own doorbell line instead.

**This test cannot be skipped or inferred.** Ring failures are deliberately
swallowed so a dead notifier never costs a letter — which means **a broken ring
is silent**. Mail landing with no bell is a failure state, not a quieter mode of
working. Only a real ring proves the transport.

### Then leave it running

`--once` was a test. It exits. If you stop here, the next message from your
phone sits at Telegram and **no bell rings**. That is not a broken ring; it is
an install that was never turned on.

```sh
alb --config ~/.alb/bridge.env --root ~/.alb
```

Make it a service with the templates in [`examples/`](examples/), using the
dedicated-venv path from Step 2. Prove it: send a message *without* running
`--once` again. Only that is a live bell.

**If you use cmux, the bridge process must be born inside cmux to ring.**
cmux refuses connections from processes it did not start, and a LaunchAgent is
one of those — mail lands, the ring records `no_live_surface`, and the usual
fix people reach for (a longer timeout) is not the cause. This applies in
integrated mode too: being *addressed to* a cmux pane does not put the bridge
*inside* cmux. So under cmux the working shape is the bridge running in its own
dedicated pane; launchd gives you durable mail only. Proven live on the first
outside install, both ways round. Details and the (discouraged) workaround:
the comment at the top of [`examples/launchd.plist`](examples/launchd.plist).

**If you are sitting in the agent's pane** (you installed this onto
yourself): the doorbell arrives after the letter is written, not with it.
If you read and file the letter in that window, you will then get a doorbell
for mail that is already gone. Wait for the doorbell before sweeping. Do not
treat that delay as a failed ring.

---

## Step 8 — Tell the agent

**[standalone] Do this before the first real message.** The transport working is
not the same as the agent recognising the doorbell. An agent that already handles
mail will receive the ALB doorbell, fail to match it, run its usual sweep, find
nothing, and reasonably conclude the bridge is broken — when everything worked.

Give your agent [`docs/agent-setup.md`](docs/agent-setup.md) — in the file it
reloads every session (its CLAUDE.md / AGENTS.md equivalent), not just pasted
into a conversation that will be forgotten. Its opening section also states
what the agent must BE: an agentic model, with unattended permission to read
the inbox and run the reply command. An agent that must ask a human before
each action has no phone line — the human is away; that is the premise.

**[integrated] Nothing to do.** Letters arrive in the inbox it already sweeps
and the doorbell is the one it already knows.

---

## Step 9 — Optional: a second agent

**One bot per agent.** This is not a preference — the platform permits exactly
one consumer per token, so sharing a bot between two agents is a conflict, not
a configuration. Each agent needs:

- its own bot and token (Step 3)
- its own state directory (Step 3)
- its own pane id, or its own `ALB_TO` in integrated mode
- its own running bridge

Repeat Steps 3–7 with a different directory, e.g. `~/.alb/grok`.

---

## Did it work?

| Check | Command | Healthy |
| --- | --- | --- |
| Should I worry? | `alb --status --root ~/.alb` | ring `ok` or `disabled`, no dead letters |
| What is wrong? | `alb --doctor --root ~/.alb` | allowlist present, config readable |
| Can it send? | `alb --canary --config ~/.alb/bridge.env --root ~/.alb` | a message arrives in your own chat |

`--status` and `--doctor` read files only — no token, no network. Safe to run
any time, including on a machine you are not sure about.

---

## If something is wrong

| Symptom | Almost always |
| --- | --- |
| Mail arrives, no ring | pane id stale after a multiplexer restart — re-pin (Step 5) |
| Nothing arrives at all | allowlist missing or wrong id — run `--doctor` (Step 3) |
| `409 Conflict` | something else is polling this token — re-issue it (Step 3, the bot) |
| Refuses to start | config not `600`, or an unknown/misspelled key (Step 5) |
| Agent gets the doorbell, finds nothing | it was never told about the bridge (Step 8) |

Deeper failures, unit files, moving machines and the 3am page:
[`docs/operations.md`](docs/operations.md).
