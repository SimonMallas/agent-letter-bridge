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
| install the package, create directories, set modes | create or edit `allowlist.json` from a value you inferred |
| write `bridge.env` from values the human gave you | put a token in a transcript, log, commit, or message |
| run `--doctor`, `--status`, `--once` | run `--canary` or `--reply-to` without being asked — both send |
| read letters in the inbox | commit anything under the state directory |
| report what failed and why | conclude "installed" without the Step 9 checkpoints passing |

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

Do not decide this by inspecting the filesystem. An inbox existing does not mean
the operator wants letters delivered into it.

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

## Step 3 — State directory

```sh
mkdir -p ~/.alb && chmod 700 ~/.alb
```

Use a per-agent path if more than one agent is being set up: `~/.alb/<agent>`.
Everything private lives here and **nothing private goes anywhere else** — that
separation is load-bearing, so do not relocate individual files.

---

## Step 4 — ASK: the bot token

> "I need a bot token. In Telegram, message @BotFather, send `/newbot`, and
> paste me the token it gives you. **If this bot already existed for anything
> else, please revoke and re-issue the token first** — the platform allows one
> consumer per token, and I cannot prove an old one isn't still being polled."

You cannot do this step. BotFather is an interactive chat the human is in.

When you receive the token: write it to the file in Step 6 and do not repeat it.

---

## Step 5 — ASK: the chat id

Give the human this command to run, with their token:

```sh
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["message"]["chat"]["id"])'
```

> "Message the bot anything first, then run this and paste me the number."

You may run it yourself **only if the human has already given you the token and
asked you to**. It is read-only and consumes nothing.

If the result is empty, they have not messaged the bot yet. Ask again rather
than searching elsewhere for an id.

> Use `chat.id`, exactly as the command does — not `from.id`. In a direct
> message they are the same number, so a wrong value passes this step and then
> denies everything the first time a group is used.

---

## Step 6 — Config

```sh
cat > ~/.alb/bridge.env <<'EOF'
ALB_TOKEN=<the token>
EOF
chmod 600 ~/.alb/bridge.env
```

**`600` is enforced, not advised** — the bridge refuses to start on a
world-readable config.

Add only keys you were given values for. Valid keys, and nothing else:

```
ALB_TOKEN ALB_SURFACE ALB_NOTIFIER ALB_TO ALB_FROM ALB_MAIL_ROOT ALB_BUS_BINARY
```

An unknown key — including a misspelling — is **refused by name**. Do not work
around a refusal by removing the check; the refusal is the feature. A setting
that is silently ignored is the failure this prevents.

**[integrated]** set `ALB_MAIL_ROOT` to the agent's inbox and `ALB_TO` to its
exact participant name.

---

## Step 7 — Allowlist. Nothing is delivered before this exists.

```sh
echo '{"chats": ["<the id from Step 5>"]}' > ~/.alb/allowlist.json
chmod 600 ~/.alb/allowlist.json
```

The id is a **string in quotes**. The gate is exact-match and fail-closed:
missing, empty, malformed or wrong-shaped all deny everything, and there is no
setting that opens it.

**Checkpoint — run it, do not assume it:**

```sh
alb --doctor --root ~/.alb
```

It must report the allowlist present and valid. A working fail-closed allowlist
and a dead bot both produce silence; `--doctor` is what distinguishes them.
Reads files only — no token, no network.

---

## Step 8 — ASK: the ring

> "Do you want a knock typed into a terminal pane when mail arrives? If so, I
> need the pane id of the agent's terminal."

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
```

You may list panes. **Do not choose one.** You cannot tell from a listing which
pane holds the agent the human means, and a knock typed into the wrong pane
lands in someone else's session.

Then set `ALB_SURFACE`, and `ALB_NOTIFIER=tmux` if applicable.

**Optional throughout.** Without it, mail lands durably and nothing pings;
`--status` reports the ring as `disabled` with a reason. If the human is unsure,
skip it — it can be added later without touching anything else.

**[integrated]** do not set `ALB_SURFACE`. The doorbell helper resolves the pane
from `ALB_TO`.

---

## Step 9 — Verify. All three, by running them.

**[integrated]** add `--mail-root <inbox>` to each command.

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
```

**Test 1 — a listed sender produces a letter.** Ask the human to message the
bot, run one cycle, and confirm a `.md` file appeared in the inbox.

**Test 2 — an unlisted sender produces silence.** Ask the human whether they
want this tested; it needs a second sender. Expect no letter and no error.

**Test 3 — the ring, if configured.** Ask the human to message the bot and
**watch their pane**. Only they can confirm this. You cannot verify it from
files: ring failures are deliberately swallowed so a dead notifier never costs a
letter, which means a broken ring is silent. `state/ring-health.json` records
the last outcome; it is not proof the pane received anything.

**Do not report the install as complete until Test 1 has actually produced a
file you looked at.** "The command exited 0" is not the same claim.

---

## Step 10 — Hand over

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
| knock lands, agent finds nothing | Step 10 was skipped |

Report the symptom and what you checked. **Do not disable a check to get past
it** — every refusal in this tool exists because something failing silently once
cost more than the refusal does.
