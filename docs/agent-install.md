# Agent install route

**Paste this whole file to the CLI agent doing the install.** It is the human
walkthrough in [`../INSTALL.md`](../INSTALL.md) rewritten as a brief: same steps,
same order, with the boundaries an agent needs that a human infers.

Not to be confused with [`agent-setup.md`](agent-setup.md), which is for the
agent being *woken* once the bridge runs. This page is for the agent *installing*
it. They may be the same agent, in which case read this one first.

---

## Your brief

Install Agent Letter Bridge so that a message sent from your operator's phone
becomes a durable letter on this machine and rings a terminal pane. The letter
is the record; the ring is how anyone learns it arrived.

Work through the steps in order. **Stop at each `ASK` and wait for a human
answer.** Guessing at any of them produces a bridge that looks installed and
delivers nothing.

---

## What you may do, and what you may not

| You may | You may **not** |
| --- | --- |
| run `alb --init` and let it create the files | create or edit `allowlist.json` from a value you inferred |
| name a mode-600 token file for `--init --token-file` | put a token in a transcript, log, commit, argv, or message |
| run `--doctor`, `--status`, `--once` | run `--canary` or `--reply-to` without being asked — both send |
| read letters in the inbox | commit anything under the state directory |
| report what failed and why | conclude "installed" without the Step 8 checkpoints passing |

**The allowlist is a security control, not configuration.** It is the only thing
between a stranger and this machine's agents. Write exactly the id the human
gives you, and if you do not have one, stop and ask. An allowlist you inferred
from a chat log, a git history, or another config file is not an allowlist.

**Never echo the token.** It is a live credential. Do not print it, quote it
back for confirmation, or include it in a summary.

---

## Step 0 — ASK: standalone or integrated?

> "Does the agent we are waking already receive mail from other agents on this
> machine — an inter-agent inbox it already sweeps?"

- **No / don't know → standalone.** Letters go to a directory this bridge owns.
  Safe default: it cannot disturb anything already running. **Running inside
  cmux does NOT make an install integrated** — integrated is about where MAIL
  goes (an inter-agent letterbox inbox); cmux is only the ring transport, and
  standalone installs use it too.
- **Yes → integrated.** Letters go into that existing inbox, and the doorbell is
  the doorbell that agent already recognises. You need its inbox path and its
  exact participant name.

**Do not decide this by inspecting the filesystem.** An inbox existing does not
mean the operator wants letters delivered into it. There is deliberately no
detector in the tool for the same reason — `alb init` asks this question too,
and you will need their answer to it either way.

---

## Step 1 — Preconditions, all of them, before you install

```sh
python3 --version
```

Requires **3.11+**. If lower or missing, report it and stop — do not install a
Python runtime unless the human asks.

No other dependencies exist. This tool is stdlib-only, and that is a security
property rather than a preference: report it if you are asked what it pulls in.

Three more, and all three are decided by questions you are about to ask, so
establish them now rather than discovering them at Step 7:

- **The source and an installer.** Every command below runs from inside the
  checkout, and the upgrade path needs the same source later. Confirm you are
  in it, and that `pipx` or `uv` resolves. If neither does, report it and stop
  — installing a package manager is the human's call.
- **The mode**, from Step 0. Standalone and integrated read *different*
  settings; setup refuses a half-changed route rather than inventing one, and
  will stop the install if you try.
- **The execution context it will run in.** A cmux ring must be started inside
  cmux; tmux has no such rule; integrated mode runs the doorbell helper in the
  BRIDGE's context, so whatever the helper needs, the bridge needs. If you
  cannot start a process in a context that satisfies this, say so before the
  install rather than after — a bridge in the wrong context delivers mail
  silently and reports success.

---

## Step 2 — Install

```sh
pipx install agent-letter-bridge
alb --help
```

From a repository checkout (developer path, not the new-user line):

```sh
pipx install .          # from the repository directory
```

If `alb` is not found, run `pipx ensurepath` and report that the human needs a
new shell — you cannot fix your own parent process's `PATH`.

`alb --version` reports the version recorded at install time, not the code on
disk. If the install is editable, a **fresh invocation** runs whatever the
source tree holds when it starts — but a **process already running** (a
resident bridge) holds the code it loaded when IT started, which may differ
from both the tree now and the version string. So neither the number nor "the
tree right now" tells you what a running process has actually loaded. Two
different questions, two different checks:

- *Is this install editable, and where does it point?* Read the install's
  `direct_url.json` (under its `*.dist-info/`): an editable install records
  `{"dir_info": {"editable": true}, "url": "file://…"}`, naming that it is
  editable and the source **location**. (Being a checkout on a branch is not by
  itself proof of an editable install; `direct_url.json` is, and `pip show`
  won't always print an explicit "editable" verdict.) Note this tells you where
  the code lives, not which bytes a resident has already loaded.
- *What does the code in front of me actually carry?* Check the feature surface
  — `alb --help` shows the flags the code exposes. To be sure a running
  resident is on the current tree, restart it so it re-loads.

If the human said this will run as a background service, use a venv instead and
note the absolute path for their unit file:

```sh
python3 -m venv ~/.alb/venv && ~/.alb/venv/bin/pip install .
```

---

## If your agent already has its own messenger

Some agent runtimes ship a messenger of their own — a gateway process that holds a
Telegram bot, an official chat plugin, a desktop app. Agent Letter Bridge is **not**
that messenger, and the two must never share a bot token *at the same time*: the
platform allows one consumer per token, so a second poller on the messenger's token is
a `409` conflict, not a merge. If you keep the built-in messenger running, the bridge
needs its own bot. If you stop the built-in messenger for good — stopped, and unable to
respawn — the known-consumer cutover below lets the bridge take over that token.

The bridge's job is to turn a phone message into a durable letter in the inbox of the
pane you already work in, and then ring that pane. The built-in messenger keeps its own
delivery route: the bridge does not synchronize the messenger's conversation with the
working pane, and nothing routes the pane's letters back into the messenger. Two routes,
each landing in a different place, unless you deliberately wire them together.

**Worked example: Hermes Agent.** Hermes Agent runs a headless gateway process
that holds its own Telegram, Discord, or SMS channels, so a message to the
gateway never reaches the terminal session where the agent is actually working.
Agent Letter Bridge solves that headless problem: the bridge runs on its own
bot beside the gateway, writes the message as a letter into the working
session's inbox, and rings that pane, so the reply comes from the same live
session. The bridge bypasses the headless gateway; the letter lands in the
same live session. The rules below still apply: own bot, never share a token,
name the bots apart, reply with `alb --reply-to`.

So, for an agent like this:

1. Create a **new** bot for the bridge while the built-in messenger stays running.
   Reuse its token only through the cutover path below, after that consumer is stopped
   and cannot respawn.
2. Allowlist yourself and configure the required ring. For multiplexer delivery, run
   the bridge inside the chosen multiplexer, exactly as for any other agent.
3. **Name the two bots apart** in the phone. Two bots under one agent name is how a
   message "goes unanswered": it went to the route that does not reach the pane.
4. Talk to the bridge's bot when you mean the working terminal; talk to the built-in
   bot only when you mean the messenger's own route. If you want one, keep the bridge
   and stop or clearly label the other — the human's call, not the agent's.
5. On the agent's side the reply verb is the bridge's (`alb --reply-to <letter-id>`),
   not the letterbox's (`bus reply`). Mixing them acknowledges a human on the internal
   bus, or files a letter without sending anything.

---

## Step 3 — ASK: the bot token

> "I need a bot token. In Telegram, message @BotFather, send `/newbot`, and
> write the token it gives you into a mode-600 file I will name — do not paste
> it into this chat. **If this bot already existed for anything else, please
> revoke and re-issue the token first** — the platform allows one consumer per
> token, and I cannot prove an old one isn't still being polled. Then send the
> bot any message, so there is one for setup to find."

"Revoke and re-issue" is the safe default *because* the previous consumer is
usually unknown and unprovable. But if you **know** the current consumer —
say a legacy poller you can point to and stop — reuse is clean: stop that
consumer first, confirm it is down and not supervised into respawning, then
reuse the same token. This is the cutover path. Its real gain over revoke-and-
re-issue is **credential continuity** (the same token, no re-issue) and a
**coordinated** handoff — you can point to the one consumer and prove it
stopped, rather than trusting that an unknown one isn't still polling. It does
NOT eliminate the swap gap: stopping the old consumer before starting the new
one leaves a brief window where nothing is polling, so sequence it tightly and
expect a short catch-up on start. (Message history and the chat survive either
way — they live on the platform side.) Handing off a known consumer by
agreement is a different risk from a token that "might" still be polled
somewhere.

You cannot do this step. BotFather is an interactive chat the human is in.

**Do not repeat the token back.** Not in a summary, not to confirm it, not in a
log. It is a live credential.

**Recommended agent path: `--init --token-file`.** Name a path the human
writes (mode 600, owner-only, one line, the token only). Then:

```sh
alb --init --root ~/.alb --token-file <that-path>
```

The wizard reads the file and deletes it. Empty, whitespace-only, or
multi-line files are refused and **kept** — do not invent a second file or
echo the contents. Never pass the token as an argument (`--token` does not
exist). Do not tell them to write `bridge.env` by hand, and do not invent a
key name — the key is `ALB_TOKEN`, and a hand-written file with anything else
is refused by the loader. File-writing for the config belongs to init, which
sets the mode at creation.

**If you fall back to the hidden prompt** (human typing at the wizard, not
the token-file path): before telling them to paste anything into a pane,
read the pane and confirm it is at the hidden token prompt. A paste into
the wrong prompt or the wrong pane is how a token lands in a transcript.

---

## Step 4 — ASK: how the human wants their chat id obtained

`alb init` offers two routes and both are legitimate. Put the choice to them:

> "Setup can read your chat id itself — one `getUpdates` call with your token,
> which consumes nothing — or it can print the command for you to run. Reading
> it removes a real trap: the payload has a second, nearly identical id that
> silently denies everything. Printing it means setup never touches the
> network. Which?"

If they have no preference, say `print` is the more conservative default and
let them confirm. **Do not choose silently.**

One more thing decides this for you: `read` issues a `getUpdates` call, and
the platform allows **one** consumer per token. If anything is *already*
polling this bot — a legacy bridge, another install — `read` can collide
with it (often a 409), and two consumers on one token make update
ownership and recovery ambiguous. So if the token is already in use
anywhere, choose `print` (or stop the other consumer first). `read` is only
safe on a bot nothing else is polling.

---

## Step 5 — Run `alb init`

Recommended (agent-driven token handoff):

```sh
alb --init --root ~/.alb --token-file <mode-600-file>
```

Interactive fallback (human types the token at the hidden prompt):

```sh
alb --init --root ~/.alb
```

It is interactive and refuses to run without a terminal, so **you cannot pipe
answers into it** — a hidden token prompt needs a real tty. If you are
driving a terminal the human can see, run it there. If you are an agent in a
harness with no tty, you have two honest options: hand the human the command
and the answers and wait, or drive `init` through a **pty** you allocate
yourself (Python's `pty` module, or wrap the command in `script -q /dev/null`).
If you drive a pty: match the prompts on **plain-text-stripped**
output (the wizard's prompts carry ANSI escapes that defeat literal substring
matching), and **stream the log incrementally** rather than buffering it —
a harness timeout can kill the process after the files are already written,
and a buffered log loses the evidence that it succeeded. The files land
before the final "start now?" offer, so a lost tail does not mean a failed
install; verify with `--doctor`, not with the transcript.

It creates the state directory `0700`, `bridge.env` `0600`, and
`allowlist.json` `0600` **denying everyone**. It will not overwrite anything
that already exists, and it reports what it kept.

**Do not create these files yourself instead.** They have modes that matter and
`init` sets them at creation, not afterwards.

At the end, `init` offers to start the bridge for you. That offer begins
polling **immediately** — which is the 409 the stuck-table later blames on
the operator, if another consumer is still up. Decline it if you are mid-
handoff or unsure whether the token is free; start the bridge yourself once
you have confirmed nothing else is polling (Step 8.5). On an empty allowlist
the offer already defaults to *not* starting, which is correct.

---

## Step 6 — Confirm the allowlist, do not invent it

```sh
alb --doctor --root ~/.alb
```

- `DELIVERY: n chat(s) permitted` → the gate is armed. Continue.
- `NOTHING WILL BE DELIVERED` → expected if the human chose `print`. Ask them
  for the number their command returned and put **exactly that** in
  `~/.alb/allowlist.json` as `{"chats": ["<id>"]}`, then re-run `--doctor`.

**If you do not have an id from the human, stop and ask.** An id you *found* — dug out of a chat log, a git history, or an unrelated
config file to move past this step — is not an allowlist entry, and a
placeholder is worse than nothing. This is different from an id **the operator
has explicitly supplied to this install's standing configuration**: that is a
known, authorized destination, not a guess — and "nearby config exists" is
not the same as "the operator supplied it here." The rule forbids *inferring*
a destination; it does not forbid one the operator has deliberately given this
install. When unsure which case you are in, ask — one sentence settles it. An agent
following this document once wrote the literal string `YOUR_CHAT_ID_HERE`
into the file to move past the step; fail-closed ate it silently, and the
operator spent the next ten minutes debugging a "dead bot" that was working
exactly as configured. Write the shape exactly — `{"chats": ["<the id>"]}` —
a bare array is the wrong shape and also denies everything. This file is the
only thing between a stranger and the agents on this machine.

`--doctor` makes no network call, so it is safe to run freely. It inspects local files, including configs that hold a credential, to work out which bot each bridge is set up for. It keeps only the bot id and never prints the secret half, never contacts Telegram and never polls. What it reports is what the files say now, not proof of what a running process loaded.

---

## Step 7 — ASK: the ring

> "Do you want a ring typed into a terminal pane when mail arrives? If so, I
> need the pane id of the agent's terminal."

`alb init` lists them for you. To list them again:

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
```

You may list panes. **Do not choose one.** You cannot tell from a listing which
pane holds the agent the human means, and a ring typed into the wrong pane
lands in someone else's session.

Then set `ALB_SURFACE` to the id **they** name, and `ALB_NOTIFIER=tmux` if
applicable. A placeholder value is refused by name — do not put one there as a
"to be filled in later", because a fake pane id is the one setting that fails
silently forever.

**The bridge delivers without it, but nobody is told** — mail lands durably
and sits until something sweeps. Treat a ring-less install as the exception
that needs a reason (an agent that sweeps on its own schedule), not the
default. `--status` reports the ring as `disabled` with a reason. It can be
added later without touching anything else.

**[integrated]** do not set `ALB_SURFACE`. The doorbell helper resolves the pane
from `ALB_TO`.

---

## Step 8 — Verify. All three, by running them.

**[integrated]** init already wrote `ALB_MAIL_ROOT`; no flag needed. If passing
`--mail-root` by hand, it takes the directory CONTAINING the inbox (the
mailbox), never the inbox itself.

**First establish which route you are on.** Only one process may hold a root.
If `init` offered to start the bridge and the human accepted, it is running and
`--once` will exit `4` — the lock working, not a fault.

```sh
alb --status --root ~/.alb
```

If a bridge is already running, do NOT run `--once` and do NOT weaken the lock.
Verify the live resident instead: ask the human to send a message and watch the
inbox. To return to one-shot checks, `alb --stop --root ~/.alb` first — and if
that returns non-zero or times out, **start nothing**; an unconfirmed stop is
not a stop.

Otherwise:

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
```

**Test 1 — a listed sender produces a letter.** Ask the human to message the
bot, run one cycle, and confirm both that the cycle reported
`published 1` **and** that a `.md` file appeared in the inbox.

If they already sent several messages before the first poll, expect
`published N` and N letters. That is Telegram's backlog emptying, not
echoes. Do not tell the human the bot duplicated their texts.

**Test 2 — an unlisted sender is denied.** Ask the human whether they want this
tested; it needs a second sender. Expect `denied 1 (allowlist)` in the cycle
report and no letter. The sender sees nothing — that is the point — but the
report is how you can tell a working gate from a dead bridge without
dismantling the gate to find out.

**Test 3 — the ring, if configured.** Ask the human to message the bot and
**watch their pane**. Only they can confirm this. You cannot verify it from
files: ring failures are deliberately swallowed so a dead notifier never costs a
letter, which means a broken ring is silent. `state/ring-health.json` records
the last outcome; it is not proof the pane received anything.

**Do not report the install as complete until Test 1 has actually produced a
file you looked at.** "The command exited 0" is not the same claim.

If **you** are the agent being woken (integrated, same pane): do not sweep the
inbox the instant `--once` publishes. The letter is durable before the doorbell is
typed, and **[integrated]** your letterbox helper deliberately pauses between
pasting the line and pressing Enter, which widens the gap further. That pause
belongs to the helper, not to this bridge — do not quote a number for it, and
do not expect the same gap in standalone mode, where the adapter does not
pause at all.
You will file the letter, then receive a ring for mail that is already gone,
and the operator will think the bell is broken. Wait for the doorbell, then
sweep. That delay is not a failed ring.

---

## Step 8.5 — Leave it running. `--once` is not a bridge.

**If `init` already started the resident, it is running — do not start
another.** `alb --status --root ~/.alb` first. A second process against the
same root exits `4`; against the same bot it is a `409` at the platform. Go
straight to proving the bell at the end of this step.

Otherwise: `--once` exits. After it exits, nothing polls and nothing rings. New
messages wait at the platform until the next cycle. **Do not tell the human they
have Telegram access while only `--once` has been run.** That is the failure
this step exists to prevent.

If they want a live bell, start the process and keep it started:

```sh
python3 -m venv ~/.alb/venv
~/.alb/venv/bin/pip install .
```

Then keep it running in the way the notifier allows. **Under cmux this means a
dedicated cmux pane, not launchd** — cmux refuses connections from processes it
did not start, so a LaunchAgent delivers durable mail and never rings, however
correct the rest of the unit is. Integrated mode does not exempt you: addressing
a cmux pane is not being inside cmux. Under tmux, or with no ring, the unit in
[`../examples/`](../examples/) is right: every path absolute, including `PATH`
so `cmux`/`tmux` resolve. Restart-on-crash only — a clean `409` yield must stay
down.

Foreground is acceptable for a first live hour:

```sh
~/.alb/venv/bin/alb --config ~/.alb/bridge.env --root ~/.alb
```

`alb --status --root ~/.alb` shows the heartbeat moving while nobody runs
`--once`, and `--doctor` reports the lock file. Neither is proof: a heartbeat is
what the last cycle wrote, and a lock file is left behind by a crash too.

**Only observation proves the bell.** Ask the human to send a message while you
poll nothing by hand, and confirm two things happened: a letter appeared, and
the line was typed into the intended pane. `--status` reporting the ring as
`delivered` says the helper returned success, not that the right pane received
it — the human is the one who can see that, so ask them.

---

## Step 9 — Hand over

**[standalone]** If the agent being woken is not you, give it
[`agent-setup.md`](agent-setup.md) before the first real message. Its doorbell is a
line no existing doorbell convention matches, so an agent with its own sweep
will receive the doorbell, find nothing, and reasonably report the bridge broken.

**[integrated]** The doorbell and the mailbox are ones the agent already
knows, so there is no new grammar to teach — but that is not the same as
nothing to hand over. It still does not know this tool exists. Give it three
things:

- `alb --config <root>/bridge.env --root <root> --reply-to <letter-id> --text "..."`
  is how it answers. **Both flags**: integrated keeps letters in the agent's
  mailbox and private state under the root. The destination
  comes from the letter; it never picks one. A letter can be answered once.
- `alb --check --root <root>` is what it runs when it wakes: `0` nothing to do,
  `2` silent past the threshold and here is how to restart it, `3` something a
  restart will not fix.
- `alb --stop --root <root>` asks the holder to stand down. **If it returns
  non-zero or times out, do not start a replacement** — an unconfirmed stop is
  not a stop, and two pollers on one token is the failure it exists to avoid.

**Sweep rule (integrated).** The ring is coalesced — one ring per batch
naming the newest letter. On a ring, sweep and handle every unfiled
`telegram-bridge` letter, oldest first. An earlier letter can sit while a
later one rings; answering only the named letter leaves the rest unread.

The rest of [`agent-setup.md`](agent-setup.md) is about a doorbell convention it
already has. These three are not.

Report to the human: which mode, the state directory, whether the ring is on,
and which of the three tests actually passed. **Name any you did not run** —
an unrun test is not a passed one.

---

## If you get stuck

| Symptom | Cause |
| --- | --- |
| letters never appear | allowlist missing or wrong id — `--doctor` |
| `409 Conflict` | another consumer holds this token; the human must re-issue it |
| refuses to start | config not `600`, or an unknown key |
| mail lands, no ring | stale pane id after a multiplexer restart |
| mail lands, launchd ring is `no_live_surface` | cmux denies processes not started inside it. Best fix: run the bridge in a cmux pane, or use tmux. Copying `CMUX_SOCKET_CAPABILITY` into the unit also works, but it is a bearer token that can type into panes and a plist is usually world-readable — see `examples/launchd.plist`. A longer timeout does not fix this and never did. |
| ring lands, agent finds nothing | Step 9 was skipped |
| nothing rings until someone runs `--once` | Step 8.5 skipped — the process is not running |

Report the symptom and what you checked. **Do not disable a check to get past
it** — every refusal in this tool exists because something failing silently once
cost more than the refusal does.
