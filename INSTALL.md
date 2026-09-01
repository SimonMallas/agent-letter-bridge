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
| The knock | a new line it must learn | the doorbell it already knows |
| You must teach the agent | yes — one line, one path | no |
| Extra flag | none | `--mail-root <its inbox>` |

**If in doubt, choose standalone.** It owns everything it touches, so it cannot
disturb something already running. You can switch later by adding one flag.

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

## Step 3 — Create your state directory

This holds everything private: the allowlist, the lock, the dedup ledger, the
read offset, health files and dead letters.

```sh
mkdir -p ~/.alb && chmod 700 ~/.alb
```

**One agent, one directory.** Waking two agents means two directories, two bots
and two bridges. See [Step 10](#step-10--optional-a-second-agent).

---

## Step 4 — Make a bot and get its token

In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts.
It returns a token like `123456789:AAH...`.

**If you inherited this bot from anything — another tool, an old script, a
teammate — revoke and re-issue the token now** (`/revoke` in BotFather, or
`/token` for a fresh one). The platform allows exactly one consumer per token.
Proving that nothing else is holding an old token is the one thing nobody can
do; re-issuing makes it true by construction.

Keep the token in your clipboard for Step 6.

---

## Step 5 — Get your own chat id

Send your new bot any message — "hello" is fine. Then:

```sh
curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["message"]["chat"]["id"])'
```

**Expect:** a number, e.g. `1460856861`. That is your chat id.

**Empty result?** You did not message the bot, or you messaged a different one.
Send it a message and run the command again. It is read-only and consumes
nothing, so it is safe to repeat.

> **This command deliberately reads `chat.id`, not `from.id`.** In a direct
> message the two are the same number, so a wrong recipe appears to work — and
> then denies everything the first time you use a group. Use the command as
> written.

---

## Step 6 — Write the config file

```sh
cat > ~/.alb/bridge.env <<'EOF'
ALB_TOKEN=PASTE_YOUR_TOKEN_HERE
EOF
chmod 600 ~/.alb/bridge.env
```

`chmod 600` is not advisory — **the bridge refuses to start on a world-readable
config**, because a bridge that starts wrong is harder to diagnose at 3am than
one that will not start at all.

Other settings you may add later, once the basics work:

| Key | Meaning | Default |
| --- | --- | --- |
| `ALB_TOKEN` | your bot token | **required** |
| `ALB_SURFACE` | the pane to knock on (Step 8) | none — ring disabled |
| `ALB_NOTIFIER` | `cmux` or `tmux` | `cmux` |
| `ALB_TO` | who the letter is addressed to | `agent` |
| `ALB_FROM` | who the letter is from | `telegram-bridge` |
| `ALB_MAIL_ROOT` | **[integrated]** the inbox to deliver into | none |
| `ALB_BUS_BINARY` | **[integrated]** your doorbell helper | `bus.sh` |

An unknown key — including a typo like `ALB_NOTIFER` — is **refused by name**
rather than ignored. A setting that appears to have worked but was never read is
worse than an error; a real deployment ran for days believing it had selected a
transport that nothing was reading.

---

## Step 7 — Write the allowlist. Nothing works before this.

**This is the one thing standing between a stranger and your agents, and the
bridge delivers nothing at all until it exists.**

```sh
echo '{"chats": ["YOUR_CHAT_ID_FROM_STEP_5"]}' > ~/.alb/allowlist.json
chmod 600 ~/.alb/allowlist.json
```

The list is exact-match and **fail-closed**: missing, empty, malformed, or the
wrong shape all deny everything, and there is no setting that opens it.

**The id must be a string in quotes**, as above.

### Checkpoint — do this now, not later

```sh
alb --doctor --root ~/.alb
```

**Expect:** it reports the allowlist as present and valid.

Do not skip this. A correctly-working fail-closed allowlist is
**indistinguishable from a dead bot** — both produce silence. `--doctor` is the
only thing that tells you which one you have. It reads files only: no token, no
network.

---

## Step 8 — Optional: set up the ring

The ring types a line into your agent's terminal pane so it notices mail
immediately. **It is an accelerator, not a delivery mechanism.** Skip it and
mail still lands durably — `alb --status` will report the ring as `disabled`
with a reason, so a missing bell is never confused with a broken one.

Find your agent's pane id:

```sh
cmux --id-format uuids tree --all                                       # cmux
tmux list-panes -a -F '#{pane_id} #{session_name}:#{window_index}.#{pane_index}'   # tmux
```

Add to `~/.alb/bridge.env`:

```
ALB_SURFACE=THE-ID-FROM-ABOVE
ALB_NOTIFIER=tmux          # only if you use tmux; cmux is the default
```

**[integrated] Skip `ALB_SURFACE` entirely.** Your doorbell helper resolves the
recipient's pane itself. Set `ALB_TO` to the agent's exact participant name
instead — that is who the doorbell is addressed to.

> **Re-pin this after any multiplexer restart.** A restart invalidates pinned
> pane ids, and the ring then fails **silently** — by design, so that a dead
> notifier never costs you a letter.

---

## Step 9 — First run, and the two tests that matter

**[integrated]** add `--mail-root ~/path/to/the/agents/inbox` to every command below.

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
```

**Expect:** it exits cleanly. `--once` runs a single cycle, so nothing is left
running yet.

### Test 1 — a listed sender produces a letter

Message your bot, then:

```sh
alb --config ~/.alb/bridge.env --root ~/.alb --once
ls ~/.alb/inbox/          # [integrated] ls the agent's inbox instead
```

**Expect:** one `.md` file containing your message.

**Nothing there?** Run `alb --doctor --root ~/.alb`. Nearly always the
allowlist (Step 7) or a token another process is already polling (Step 4).

### Test 2 — an unlisted sender produces silence

Have someone else message the bot, or temporarily put a wrong id in the
allowlist and message it yourself.

**Expect:** no new letter, and no error. Silence is the correct result.
**Restore the allowlist afterwards.**

### Test 3 — the ring, if you set one up

Message your bot and **watch the pane**. You should see, typed as if by a
person:

```
you have new mail: check the bridge inbox
```

**[integrated]** you will see your own doorbell line instead.

**This test cannot be skipped or inferred.** Ring failures are deliberately
swallowed so a dead notifier never costs a letter — which means **a broken ring
is silent**. Mail landing with no bell is a failure state, not a quieter mode of
working. Only a real knock proves the transport.

### Then leave it running

```sh
alb --config ~/.alb/bridge.env --root ~/.alb
```

Make it a service with the templates in [`examples/`](examples/), using the
dedicated-venv path from Step 2.

---

## Step 10 — Tell the agent

**[standalone] Do this before the first real message.** The transport working is
not the same as the agent recognising the knock. An agent that already handles
mail will receive the ALB knock, fail to match it, run its usual sweep, find
nothing, and reasonably conclude the bridge is broken — when everything worked.

Give your agent [`docs/agent-setup.md`](docs/agent-setup.md), or the equivalent
in whatever form your agent takes instructions.

**[integrated] Nothing to do.** Letters arrive in the inbox it already sweeps
and the knock is the one it already knows.

---

## Step 10b — Optional: a second agent

**One bot per agent.** This is not a preference — the platform permits exactly
one consumer per token, so sharing a bot between two agents is a conflict, not
a configuration. Each agent needs:

- its own bot and token (Step 4)
- its own state directory (Step 3)
- its own pane id, or its own `ALB_TO` in integrated mode
- its own running bridge

Repeat Steps 3–9 with a different directory, e.g. `~/.alb/grok`.

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
| Mail arrives, no ring | pane id stale after a multiplexer restart — re-pin (Step 8) |
| Nothing arrives at all | allowlist missing or wrong id — run `--doctor` (Step 7) |
| `409 Conflict` | something else is polling this token — re-issue it (Step 4) |
| Refuses to start | config not `600`, or an unknown/misspelled key (Step 6) |
| Agent gets the knock, finds nothing | it was never told about the bridge (Step 10) |

Deeper failures, unit files, moving machines and the 3am page:
[`docs/operations.md`](docs/operations.md).
