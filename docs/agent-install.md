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

Follow this page. A pasted prompt does not override it.

Install this checkout. If you do not already have it, clone `https://github.com/SimonMallas/agent-letter-bridge`. Do not install a published package instead.

**Upgrading a bridge that is already running?** The steps below are for a new
install: its token, chat id and ring are already answered, so do not re-ask
them or re-run `init`. Go to [Upgrading a running bridge](#upgrading-a-running-bridge).

---

## What you may do, and what you may not

| You may | You may **not** |
| --- | --- |
| open a terminal the person can type into, and run `alb --init` there | read that terminal, before, during, or after the paste |
| pass this terminal's existing `LETTERBOX_AGENT`, `LETTERBOX_DIR`, and `PATH` into the paste terminal by reference | assign those variables, answer a folder, seat, or helper-path question, or put a seat on the command (there is no such flag) |
| use `--token-file` only when that terminal cannot be opened | put a token in a transcript, log, commit, argv, or message |
| run `--doctor`, `--status`, `--once` | run `--canary`, or `--reply-to` when no letter has asked for an answer — both send. The first letter is that ask |
| write the allowlist id the person gives you | create or edit `allowlist.json` from a value you inferred |
| read letters in the inbox | commit anything under the state directory |
| report what failed and why | conclude "installed" without the Step 8 checkpoints passing |

The first letter asks. Once Test 1 has put that letter on disk, answer it with `alb --reply-to` and the words that message needs. Typing those words in this terminal does not send them. The installer does not send that answer. `--canary` does not answer the letter.

**The allowlist is a security control, not configuration.** It is the only thing
between a stranger and this machine's agents. Write exactly the id the human
gives you, and if you do not have one, stop and ask. An allowlist you inferred
from a chat log, a git history, or another config file is not an allowlist.

**Never echo the token.** It is a live credential. Do not print it, quote it
back for confirmation, or include it in a summary.

---

## Step 0 — The seat this terminal already has

Use this path first. The person sent you, the agent they are already working
with, to this repo to install the bridge. Do not ask whether this is
integrated or standalone. Do not ask which folder, which seat, or which
pane. You already are that seat, or this terminal does not say so.

Look at the environment you inherited. Do not assign, unset, or replace
`LETTERBOX_DIR` or `LETTERBOX_AGENT`. Passing `"$LETTERBOX_AGENT"` through,
as Step 3 does, is not a change: the shell expands the value it already
has. A name you type is a choice, and a choice can point the bridge at
someone else's mailbox. Setup trusts only the seat the terminal already
had. There is no flag for a seat.

A shell that has just started has neither variable. Empty references are
not a seat. Do not ask the person for a folder. The setup terminal asks
them for the name of the agent. Do not answer that question, and do not
pass a name. A blank keeps the bridge's own folder. Joining a mailbox
that already exists needs the exact name of a seat in the folder this
terminal was given, or the one Letterbox's own setup has already recorded.
This run does not search the machine for one.

You run init in Step 3, in a terminal the person can type into. The program
is `alb --init`. It uses a seat only when that terminal's own environment
already names one. A pane started by a server does not inherit this
terminal. Step 3 passes the variables through by reference. You still do
not type a name.

Nothing on that command names a folder, a seat, or a token file. There is
still no flag.

- They tell you init used a seat and did not ask: that is the install.
  Continue. The seat and the folder they report are the ones to report.
  You still do not read the terminal to check.
- They tell you it asked which folder, or for a helper path, or that the
  named seat will not be used: **stop.** The seat did not carry. Do not
  answer. Do not assign `LETTERBOX_DIR` or `LETTERBOX_AGENT`. Do not type
  a helper path. Do not guess a participant name from the repo, a
  transcript, or a folder listing. The name of the agent is the one
  question you leave in that terminal. Do not answer it.

### When this terminal does not name a seat

Init asks for the name of the agent, does not ask for a folder, and keeps
the bridge's own folder unless that name is an exact seat. You read that
folder from the root you passed to init. You still do not read the paste
terminal.

**Running inside cmux does not make an install integrated.** Integrated is
about where mail goes. cmux is only the ring transport, and a bridge that
keeps its own folder uses it too.

A folder question, or a helper-path question, means an older build. Stop.
Do not answer. Do not guess a name from the repo, a transcript, or a
folder listing. The name question is the current build. Leave it there.

Joining a mailbox that already exists still needs a terminal that already
names that seat.

---

## Step 1 — Preconditions, all of them, before you install

```sh
python3 --version
```

Requires **3.11+**. If `python3` is older, look for a newer one that is
already installed (`python3.13 --version`, `python3.12 --version`,
`python3.11 --version`; on a Mac, `python3` is often Apple's 3.9 while a newer
Python sits beside it) and use that name wherever this guide says `python3`
(with pipx: `pipx install --python python3.12 …`). If none is 3.11+, report it
and stop — do not install a Python runtime unless the human asks.

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
   not the letterbox's (`letterbox reply`). Mixing them acknowledges a human on the internal
   bus, or files a letter without sending anything.

---

## On Slack or Discord: Steps 3 to 5 differ

Steps 0–2 and 6–9 apply as written, and so does the table of what you may and
may not do. Use `--platform slack` or `--platform discord`, each with its own
`--root` (`~/.alb-slack`, `~/.alb-discord`). The human's step-by-step guides
are [`slack-setup.md`](slack-setup.md) and [`discord-setup.md`](discord-setup.md);
point them there. Steps 3 to 5 below are Telegram's.

**What you cannot do on either platform:** create the app or the bot, sign in,
type the human's password, pass a "prove you are human" check, or copy their
user ID from their chat app. All of that happens in their browser or app.

**Slack.** The app is made from a manifest that `alb --init` prints on its
first screen, so the app cannot exist before init runs.

- Run `alb --init --platform slack --root ~/.alb-slack` in a terminal pane the
  human can see. Do not read the token prompts. Step 3 is the same rule.
- They copy the manifest from that screen, from the first `{` to the last
  `}`, before any screenshot. They make the app, then paste the two tokens
  at init's hidden prompts themselves: the app-level token (`xapp-`) and
  the bot token (`xoxb-`). Never ask for either in chat.
- If the app already exists and both tokens are issued, the token-file route
  works: `--token-file <bot> --app-token-file <app>`, both mode 600, both
  removed after reading.
- A work workspace may need an admin to approve the app. That is the human's
  call, not yours.
- For the allowlist (`read`): ask the human to send the app one DM first. Setup
  lists the bot's DMs and the human picks which one is them. Do not pick for
  them.

**Discord.** The bot exists before init, so the token-file route works.

- The human makes the application by following `discord-setup.md` Steps 2–3.
  On the Installation page, Install Link must be **None** before Public Bot can
  be turned off. Reset Token may ask for their password.
- They write the token into a mode-600 file you name. Then run
  `alb --init --platform discord --root ~/.alb-discord --token-file <file>`.
- Init prints an invite link. The human opens it, picks their server, and
  authorizes. Discord may ask them to prove they are human.
- For the allowlist (`read`): ask for their Discord user ID. In Discord: User
  Settings → Developer (Advanced in older versions) → Developer Mode on, then
  right-click their own name → Copy User ID. Setup opens the DM with them;
  nothing is sent.
- One bot serves one bridge. A second agent needs a second application.

---

## Step 3 — The paste terminal

You open this. The person does not open a terminal, and they do not write a file.

> "I am opening a terminal that will ask you to paste the bot token and press
> enter. Nothing will appear as you paste. Do not paste it into this chat.
> In Telegram, message @BotFather, send `/newbot`, and paste the token it
> gives you into that terminal. **If this bot already existed for anything
> else, please revoke and re-issue the token first** — the platform allows
> one consumer per token, and I cannot prove an old one isn't still being
> polled. Then send the bot any message, so there is one for setup to find."

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

You cannot message BotFather. That chat is the person's. Opening the terminal is yours.

**Do not repeat the token back.** Not in a summary, not to confirm it, not in a
log. It is a live credential.

**The path is a terminal already waiting for the paste.** After Step 2, open
one. The program it runs is this, started from the terminal that already
names the seat:

```sh
/usr/bin/env \
  LETTERBOX_AGENT="$LETTERBOX_AGENT" \
  LETTERBOX_DIR="$LETTERBOX_DIR" \
  PATH="$PATH" \
  alb --init --root ~/.alb
```

The shell that already has the variables expands the three references. You
do not assign them. You do not print them. You do not replace
`"$LETTERBOX_AGENT"` with a name. A name you type can point the bridge at
someone else's mailbox. There is still no flag. Setup trusts only the
environment the paste terminal actually receives.

If this shell does not already have both variables, leave them off the
command and run `alb --init --root ~/.alb`. Do not ask the person to
supply them.

If a bridge is already using `~/.alb`, pick a new root, tell the person the path, and do not ask them to choose it. Leave any bridge that is already running untouched.

`PATH` is in that line because a server-started pane does not keep this
terminal's `PATH`, and both `alb` and `letterbox` have to be found there.
Do not type a path to the helper.

No token file. No seat flag.

The wizard is already waiting. It says nothing will appear as you paste, to
paste once and press enter. A paste that is one token is one check of
whether the platform accepts it. Accepted means the platform accepted it,
with the character count, and the next step. A refusal is not saved. A
failure to ask is not a refusal, and the details are not shown.

**Do not read that terminal.** Not before the paste, not during it, and not
after. Do not confirm the prompt by looking. Reading it is how a key ends
up in your transcript. The person tells you whether they saw accepted and
the next step. You then run `alb --doctor --root ~/.alb`. Doctor keeps
the bot id and does not print the secret half.

**How to open it.**

- **cmux**, and only when this process is already inside cmux
  (`CMUX_SOCKET_PATH` or `CMUX_SOCKET_CAPABILITY`). The only open command
  in this repo is the one setup uses for the bridge's own pane. The same
  flags start the paste terminal. The seat shell expands the references.
  Do not read the workspace it creates:

  ```sh
  cmux workspace create --name "bridge setup" --command \
    "/usr/bin/env LETTERBOX_AGENT=\"$LETTERBOX_AGENT\" LETTERBOX_DIR=\"$LETTERBOX_DIR\" PATH=\"$PATH\" alb --init --root ~/.alb"
  ```

- **Rex.** A pane opened with `rex session new` is started by the Rex
  server and does not inherit the caller's environment. A bare `alb --init`
  there names no seat, so the bridge keeps its own folder and does not ask
  for one. The program after `--` is the env line above, and only when this
  terminal already has those variables. Do not add Rex flags this page does
  not show. It shows none: this repo does not ship that command. If you do
  not already know a complete `rex session new` line, do not guess one.
- **tmux, Zellij, Herdr.** Open a new pane in the same window and run the
  env line in it. This repo does not ship an open command for those, and
  this page will not invent flags. Inheritance was not measured for them.
  Do not rely on it.

If that terminal asks which folder, or for a helper path, stop, as Step 0
says. Do not answer. Do not assign the variables to a value you chose.
The name of the agent is the one question you leave there. Do not answer it.

**When you cannot open a terminal the person can type into**, including when
opening one would mean guessing a command, the file is the path. Run it
here, in the terminal that already names the seat, so the route is still
the agent the person was talking to. That limit is why the file exists:
you must not watch the paste,
and a file you do not read keeps the key out of your transcript. It does
not replace the terminal when the person is here and a pane can be opened.
Name a mode-600 path they write (owner-only, one line, the token only).
Then, in the terminal that already names the seat:

```sh
alb --init --root ~/.alb --token-file <that-path>
```

The wizard reads the file and deletes it. Empty, whitespace-only, or
multi-line files are refused and **kept** — do not invent a second file or
echo the contents. Never pass the token as an argument (`--token` does not
exist). Do not tell them to write `bridge.env` by hand, and do not invent a
key name — the key is `ALB_TOKEN`, and a hand-written file with anything else
is refused by the loader. File-writing for the config belongs to init, which
sets the mode at creation. Slack and Discord, below, still take a token from
a file. That is a different path. Do not send Telegram to the file when a
paste terminal can be opened.

---

## Step 4 — ASK: how the human wants their chat id obtained

`alb --init` offers two routes and both are legitimate. Put the choice to them:

> "Setup can read your chat id itself — one `getUpdates` call with your token,
> which consumes nothing — or it can print the command for you to run. Reading
> it removes a real trap: the payload has a second, nearly identical id that
> silently denies everything. The token was already checked. Printing does
> not poll. Which?"

If they have no preference, say `print` is the more conservative default and
let them confirm. **Do not choose silently.** Say this before the paste
terminal asks, so they can answer there. You do not type the answer, and
you do not read the terminal to see which they picked. `--doctor` tells you
whether an id was saved.

One more thing decides this for you: `read` issues a `getUpdates` call, and
the platform allows **one** consumer per token. If anything is *already*
polling this bot — a legacy bridge, another install — `read` can collide
with it (often a 409), and two consumers on one token make update
ownership and recovery ambiguous. So if the token is already in use
anywhere, tell them to choose `print` (or stop the other consumer first). `read` is only
safe on a bot nothing else is polling.

---

## Step 5 — `alb --init` is the paste terminal

Step 3 already started it. Do not start a second one.

The offer to start the bridge is in that same terminal. Ask the person what
they answered. Do not start another bridge from your own session.

If no paste terminal could be opened, Step 3's file path is the run:

```sh
alb --init --root ~/.alb --token-file <mode-600-file>
```

If it asks which folder or which seat, stop, as Step 0 says. Do not answer.

It is interactive and refuses to run without a terminal, so **you cannot pipe
answers into it**. Do not allocate a pty in order to watch the token prompt.
A session you read is the same leak as reading the pane. Confirm with
`--doctor` and with what the person says they saw, not with a transcript of
the paste. The files land before the final "start now?" offer, so a lost
tail does not mean a failed install.

It creates the state directory `0700`, `bridge.env` `0600`, and
`allowlist.json` `0600` **denying everyone**. It will not overwrite anything
that already exists, and it reports what it kept.

**Do not create these files yourself instead.** They have modes that matter and
`init` sets them at creation, not afterwards.

At the end, `init` offers to start the bridge for you. That offer begins
polling **immediately** — which is the 409 the stuck-table later blames on
the operator, if another consumer is still up. Tell them to decline it if you are mid-
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

## Step 7 — The ring

If init used a seat, or it said there is no pane to pick, **do not ask.**
Do not ask which cmux or tmux pane. Do not set `ALB_SURFACE`. The ring is
the letterbox helper (`ALB_BUS_BINARY`), which resolves the pane from
`ALB_TO`.

That is how a carried seat rings on cmux, tmux, Herdr, Zellij, and Rex.
Rex is not a pane id this bridge types into. There is no Rex notifier.
Zellij rings through that same helper, not through a notifier this bridge
types into. You do not choose the pane, and you do not ask the person to
name one.

The question below is only for a bridge that keeps its own folder. The
wizard asks it in the init terminal. You still do not choose a pane, and
you do not read that terminal to copy an id out of it. The person pastes
the id at the wizard's own question.

To list panes later, outside that terminal:

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
herdr agent list                                                        # herdr
```

You may list panes. **Do not choose one.** You cannot tell from a listing which
pane holds the agent the human means, and a ring typed into the wrong pane
lands in someone else's session.

On that standalone path, `ALB_SURFACE` is the id **they** named, and
`ALB_NOTIFIER=tmux` or `herdr` only when the listing they used was that
one. A placeholder value is refused by name — do not put one there as a
"to be filled in later", because a fake pane id is the one setting that fails
silently forever.

**The bridge delivers without it, but nobody is told** — mail lands durably
and sits until something sweeps. Treat a ring-less install as the exception
that needs a reason (an agent that sweeps on its own schedule), not the
default. `--status` reports the ring as `disabled` with a reason. It can be
added later without touching anything else. A carried seat is not that
exception: its ring is the helper, and it was not asked for a pane.

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
`published 1` **and** that a `.md` file appeared in the inbox. The answer
goes back on the phone with `alb --reply-to <letter-id> --text "..."`. A
reply typed only in the terminal does not reach the phone. Do that for the
first message, not only a later one. Tell the human:
replying to a message keeps the next message in that message's thread (Telegram: press and hold, then **Reply**; Slack: **Reply in thread**; Discord: **Reply**). Sending without Reply starts a new thread. Either way the agent receives the message, and starting a new thread is not a mistake; use Reply when one subject should stay together.

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

**Do not report the install as complete until you have looked at Test 1's
letter, answered it with `alb --reply-to`, and the human confirms that answer
is on the phone.** A letter file alone is not complete. A reply typed only in
this terminal is not complete. "The command exited 0" is not the same claim.

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

**Never start it as a background task of your own agent session.** A bridge
started that way is a child of your CLI: when your session ends or restarts,
the bridge stops and the phone goes quiet. Run that command in its own pane, or
under the service unit, where it outlives you.

`alb --status --root ~/.alb` shows the heartbeat moving while nobody runs
`--once`, and `--doctor` reports the lock file. Neither is proof: a heartbeat is
what the last cycle wrote, and a lock file is left behind by a crash too.

**Only observation proves the bell.** Ask the human to send a message while you
poll nothing by hand, and confirm two things happened: a letter appeared, and
the line was typed into the intended pane. `--status` reporting the ring as
`typed into the agent's pane` says the helper typed it, not that the right pane
received it — the human is the one who can see that, so ask them.

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

- `alb --reply-to <letter-id> --text "..."` is how it answers. The running
  bridge registers its root and config in a per-user registry, so the id alone
  finds it. For a bridge older than 0.5.2, or one run under another HOME, give
  **both flags**: `--config <root>/bridge.env --root <root>`. The destination
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

## Upgrading a running bridge

The config, allowlist and state carry forward untouched. What changes is the
code, and the running process keeps the code it started with until it restarts.

1. **Find the install that is running**, not the one on your `PATH`; they are
   often different. `alb --status --root <root>` names it from 0.5.3 on
   (`running 0.5.3 from <python>`). For an older bridge, read the process
   list: `ps -eo args | grep -- '--root <root>'` shows the executable. The
   `venv` path in Step 8.5 is an example, not a promise of where yours is.
2. **Install the new version beside it**, in a new venv whose name is not
   already in use, for example
   `python3 -m venv <root>/venv-<version>` (a 3.11+ Python, Step 1) then
   `<root>/venv-<version>/bin/pip install agent-letter-bridge==<version>`.
   Do not install over the running venv, and leave a shared `alb` on `PATH`
   alone: other bridges may run it.
3. **Note where it runs, then stop it.** `ps -o tty= -p <pid>` names its
   terminal; find that terminal in your multiplexer (cmux: `cmux tree --all`
   shows each pane's `tty=`; tmux:
   `tmux list-panes -a -F '#{pane_tty} #{pane_id}'`). That is the pane the new
   one starts in. `?` or `??` means it has no terminal; look at its parent,
   `ps -o comm= -p "$(ps -o ppid= -p <pid>)"`. `launchd` or `systemd` means a
   service: restart the service instead. Anything else, such as an agent's
   session, means it was started in the background: start the new one in a
   dedicated pane instead (Step 8.5). Then stop it with
   `<old alb> --stop --root <root>` and check that its process is gone.
4. **Start the new one the way the old one ran**: same `--config` and `--root`,
   in that pane or service. Not as a background task of your own session
   (Step 8.5). Type the command into that pane: cmux:
   `cmux send --surface <surface> '<command>\n'`; tmux:
   `tmux send-keys -t <pane> '<command>' Enter`. If your session cannot reach
   the multiplexer (some agent apps cannot), give the human the exact start
   command to run there; do not start it from your own session instead. If
   that pane closed when the old bridge stopped (it ran the bridge as its own
   command, for example with `exec`), open a new dedicated pane and start it
   there.
5. **Check** `<new alb> --status --root <root>`: the new version is named and
   the heartbeat is fresh. The `ring` line keeps the old bridge's wording
   until the new one rings. Then ask the human to send a message, as in
   Step 8.5.

Do not re-run `init` and do not edit the allowlist. To roll back, stop the new
one and start the old executable again; its venv is still there.

The ring runs whatever `ALB_BUS_BINARY` names. If that is the `letterbox` in
your Letterbox install, updating Letterbox updates the ring. If it names a copy
somewhere else, a Letterbox update does not reach it; leave it unless you mean
to change it.

### Moving an agent onto a Letterbox

An existing bridge can switch from its own inbox, or another mailbox, to a
Letterbox (integrated mode) by changing three keys in `bridge.env`, then
restarting as above:

- `ALB_MAIL_ROOT` is the agent's own mailbox inside the Letterbox,
  `<letterbox>/<agent>` (for example `~/.agent-letterbox/builder`), never the
  Letterbox itself. The bridge refuses the Letterbox itself.
- `ALB_TO` is that agent's name.
- `ALB_BUS_BINARY` is the absolute path of `letterbox`, so a service finds it.

The agent's pane must be registered for the ring: start the agent with
`letterbox <multiplexer> run <agent> -- <command>`, or run
`letterbox <multiplexer> register <agent>` from its pane and set
`LETTERBOX_AGENT=<agent>` where its commands run. `letterbox` uses
`LETTERBOX_DIR`, or else the Letterbox that `setup` recorded as the default; if
the agent's Letterbox is a different one, export `LETTERBOX_DIR=<letterbox>`
first, or the pane is registered in the wrong Letterbox.

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
