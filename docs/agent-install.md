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
becomes a durable letter on this machine, and — optionally — a knock in a
terminal pane.

Work through the steps in order. **Stop at each `ASK` and wait for a human
answer.** Guessing at any of them produces a bridge that looks installed and
delivers nothing.

---

## What you may do, and what you may not

| You may | You may **not** |
| --- | --- |
| run `alb --init` and let it create the files | create or edit `allowlist.json` from a value you inferred |
| write `bridge.env` from values the human gave you | put a token in a transcript, log, commit, or message |
| run `--doctor`, `--status`, `--once` | run `--canary` or `--reply-to` without being asked — both send |
| read letters in the inbox | commit anything under the state directory |
| report what failed and why | conclude "installed" without the Step 8 checkpoints passing |

**The allowlist is a security control, not configuration.** It is the only thing
between a stranger and this machine's agents. Write exactly the id the human
gives you, and if you do not have one, stop and ask. An allowlist you inferred
from a chat log, a git history, or another config file is not an allowlist.

**Never echo the token.** It is a live credential. Write it to the file and do
not print it, quote it back for confirmation, or include it in a summary.

---

## Step 0 — ASK: standalone or integrated?

> "Does the agent we are waking already receive mail from other agents on this
> machine — an inter-agent inbox it already sweeps?"

- **No / don't know → standalone.** Letters go to a directory this bridge owns.
  Safe default: it cannot disturb anything already running.
- **Yes → integrated.** Letters go into that existing inbox, and the knock is
  the doorbell that agent already recognises. You need its inbox path and its
  exact participant name.

**Do not decide this by inspecting the filesystem.** An inbox existing does not
mean the operator wants letters delivered into it. There is deliberately no
detector in the tool for the same reason — `alb init` asks this question too,
and you will need their answer to it either way.

---

## Step 1 — Preconditions

```sh
python3 --version
```

Requires **3.11+**. If lower or missing, report it and stop — do not install a
Python runtime unless the human asks.

No other dependencies exist. This tool is stdlib-only, and that is a security
property rather than a preference: report it if you are asked what it pulls in.

---

## Step 2 — Install

```sh
pipx install .          # from the repository directory
alb --help
```

If `alb` is not found, run `pipx ensurepath` and report that the human needs a
new shell — you cannot fix your own parent process's `PATH`.

If the human said this will run as a background service, use a venv instead and
note the absolute path for their unit file:

```sh
python3 -m venv ~/.alb/venv && ~/.alb/venv/bin/pip install .
```

---

## Step 3 — ASK: the bot token

> "I need a bot token. In Telegram, message @BotFather, send `/newbot`, and
> paste me the token it gives you. **If this bot already existed for anything
> else, please revoke and re-issue the token first** — the platform allows one
> consumer per token, and I cannot prove an old one isn't still being polled.
> Then send the bot any message, so there is one for setup to find."

You cannot do this step. BotFather is an interactive chat the human is in.

**Do not repeat the token back.** Not in a summary, not to confirm it, not in a
log. It is a live credential.

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

---

## Step 5 — Run `alb init`

```sh
alb --init --root ~/.alb
```

It is interactive and refuses to run without a terminal, so **you cannot pipe
answers into it**. If you are not driving a terminal the human can see, hand
them the command and the answers, and wait.

It creates the state directory `0700`, `bridge.env` `0600`, and
`allowlist.json` `0600` **denying everyone**. It will not overwrite anything
that already exists, and it reports what it kept.

**Do not create these files yourself instead.** They have modes that matter and
`init` sets them at creation, not afterwards.

---

## Step 6 — Confirm the allowlist, do not invent it

```sh
alb --doctor --root ~/.alb
```

- `DELIVERY: n chat(s) permitted` → the gate is armed. Continue.
- `NOTHING WILL BE DELIVERED` → expected if the human chose `print`. Ask them
  for the number their command returned and put **exactly that** in
  `~/.alb/allowlist.json` as `{"chats": ["<id>"]}`, then re-run `--doctor`.

**If you do not have an id from the human, stop and ask.** An allowlist entry
you found in a chat log, a git history, or another config file is not an
allowlist entry. This file is the only thing between a stranger and the agents
on this machine.

`--doctor` reads files only: no token, no network. Run it freely.

---

## Step 7 — ASK: the ring

> "Do you want a knock typed into a terminal pane when mail arrives? If so, I
> need the pane id of the agent's terminal."

`alb init` lists them for you. To list them again:

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
```

You may list panes. **Do not choose one.** You cannot tell from a listing which
pane holds the agent the human means, and a knock typed into the wrong pane
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

**[integrated]** add `--mail-root <inbox>` to each command.

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
inbox the instant `--once` publishes. The letter is durable before the knock is
typed, and **[integrated]** your letterbox helper deliberately pauses between
pasting the line and pressing Enter, which widens the gap further. That pause
belongs to the helper, not to this bridge — do not quote a number for it, and
do not expect the same gap in standalone mode, where the adapter does not
pause at all.
You will file the letter, then receive a knock for mail that is already gone,
and the operator will think the bell is broken. Wait for the knock, then
sweep. That delay is not a failed ring.

---

## Step 8.5 — Leave it running. `--once` is not a bridge.

`--once` exits. After it exits, nothing polls and nothing rings. New messages
wait at the platform until the next cycle. **Do not tell the human they have
Telegram access while only `--once` has been run.** That is the failure this
step exists to prevent.

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

Confirm with `alb --status --root ~/.alb` that the heartbeat is moving while
nobody is running `--once`. Then ask the human to send a message **without**
you polling by hand. Only that proves the bell.

---

## Step 9 — Hand over

**[standalone]** If the agent being woken is not you, give it
[`agent-setup.md`](agent-setup.md) before the first real message. Its knock is a
line no existing doorbell convention matches, so an agent with its own sweep
will receive the knock, find nothing, and reasonably report the bridge broken.

**[integrated]** Nothing to hand over.

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
| mail lands, no knock | stale pane id after a multiplexer restart |
| mail lands, launchd ring is `no_live_surface` | cmux denies processes not started inside it. Best fix: run the bridge in a cmux pane, or use tmux. Copying `CMUX_SOCKET_CAPABILITY` into the unit also works, but it is a bearer token that can type into panes and a plist is usually world-readable — see `examples/launchd.plist`. A longer timeout does not fix this and never did. |
| knock lands, agent finds nothing | Step 9 was skipped |
| nothing rings until someone runs `--once` | Step 8.5 skipped — the process is not running |

Report the symptom and what you checked. **Do not disable a check to get past
it** — every refusal in this tool exists because something failing silently once
cost more than the refusal does.
