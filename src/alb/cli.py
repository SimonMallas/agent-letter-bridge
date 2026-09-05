"""Agent Letter Bridge - message the CLI agents on your machine from your phone.

Every message becomes part of their memory: a durable, deduplicated, enveloped
letter on disk before the platform is even acknowledged.

A message from your chat app becomes a durable file on disk BEFORE it is
acknowledged and BEFORE any agent is notified. The file is the source of truth;
the notification only makes it faster.

SET IT UP
  alb --init --root ~/.alb    creates the state directory, a mode-600 config
                              and a DENY-ALL allowlist, then asks for what no
                              program can derive. Never invents an allowlist
                              entry, never overwrites a file, and never reaches
                              the platform unless you ask it to.

RUN IT
  alb --config bridge.env --root ~/.alb        both flags are required
  The env file must be mode 600 and set ALB_TOKEN. ALB_SURFACE is optional:
  without it, mail lands and nothing rings.

WHEN SOMETHING IS WRONG
  alb --status   should I worry? reads files only, no token, no network
  alb --doctor   what is my environment doing? no token, no platform calls
  alb --canary   is the send path alive? sends to your own chat, then you
                 confirm it arrived - nothing here can prove that for you

REPLY TO A STORED LETTER
  alb --reply-to <letter-id> --text "..."

The bridge exits 0 on a platform conflict: that is a deliberate yield so the
token's holder keeps running, not a failure. Under restart-on-crash-only
supervision it will stay stopped, which is intended. See docs/operations.md.
"""
import argparse
import contextlib
import os
import pathlib
import signal
import sys
import time

from alb.adapters.cmux import transport as cmux_transport
from alb.adapters.tmux import transport as tmux_transport
from alb.adapters.telegram import api
from alb.bridge import run, singleton
from alb.poller import loop
from alb.send import reply


@contextlib.contextmanager
def lock_guard(lock):
    """Release the singleton lock on any exit path.

    The kernel would release it anyway when the process dies; this removes the
    question rather than relying on that.
    """
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _version():
    """The installed distribution's version, or the source tree's marker.

    importlib.metadata answers for an installed package; a source checkout
    that was never installed still deserves an answer rather than a stack
    trace, so that path degrades to "source"."""
    try:
        from importlib.metadata import version
        return version("agent-letter-bridge")
    except Exception:  # noqa: BLE001 - any failure means "not installed"
        return "source"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="alb", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"agent-letter-bridge {_version()}")
    parser.add_argument("--config", default="", help="path to the env file (mode 600)")
    parser.add_argument("--root", required=True, help="private state directory")
    parser.add_argument("--mail-root", default=None,
                        help="publish letters here instead (integrated mode); "
                             "private state always stays under --root")
    parser.add_argument("--init", action="store_true",
                        help="create the state directory, a mode-600 config and a "
                             "deny-all allowlist, asking for what cannot be derived")
    parser.add_argument("--once", action="store_true", help="one cycle, then exit")
    parser.add_argument("--canary", action="store_true",
                        help="prove the send path is alive; sends to your own chat")
    parser.add_argument("--status", action="store_true",
                        help="report bridge and ring health; reads only")
    parser.add_argument("--stop", action="store_true",
                        help="interrupt the bridge running on this root; "
                             "starts nothing")
    parser.add_argument("--check", action="store_true",
                        help="what a waking agent should DO about its relay; "
                             "exit 0 nothing, 2 restart it, 3 investigate")
    parser.add_argument("--doctor", action="store_true",
                        help="local diagnostics; holds no token, makes no platform call")
    parser.add_argument("--list", action="store_true",
                        help="the correspondence, both directions; reads only")
    parser.add_argument("--show", metavar="LETTER_ID",
                        help="one letter, envelope and body; reads only")
    parser.add_argument("--search", metavar="TEXT",
                        help="exact substring over bodies and envelopes; reads only")
    parser.add_argument("--thread", metavar="LETTER_ID",
                        help="a whole thread by any member letter; reads only")
    parser.add_argument("--export", metavar="LETTER_ID",
                        help="tar a thread's letters and receipts (with --out)")
    parser.add_argument("--out", metavar="PATH",
                        help="destination for --export")
    parser.add_argument("--reply-to", metavar="LETTER_ID",
                        help="reply to a stored letter instead of polling")
    parser.add_argument("--text", help="reply body, with --reply-to")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)

    # Setup runs before there is a config to load, which is the whole point:
    # it is what creates one. It never reads a token from anywhere, and never
    # reaches the platform unless the operator asks it to in that moment.
    if args.init:
        return _init(args)

    # Stopping is a signal to a process this product owns. It deliberately
    # knows nothing about panes: STARTING is a pane act and belongs to whoever
    # owns the multiplexer, but ending a process we started is ours, and
    # leaving it to each seat is how one agent ended up signalling a pid it
    # had found by hand.
    if args.stop:
        # No local import: singleton is already imported at module level, and
        # re-importing it here would make the name local to this whole
        # function - unbinding the module-level one for every branch after
        # this. Which is exactly what it did, twice, before this comment.
        pid = singleton.running_pid(args.root)
        if pid is None:
            print("nothing to stop: no bridge holds this root")
            return 0
        if pid < 0:
            print("a bridge holds this root but did not record its pid - "
                  "refusing to guess at one. Find it in its pane and "
                  "interrupt it there.")
            return 1
        os.kill(pid, signal.SIGINT)
        for _ in range(100):
            if singleton.running_pid(args.root) is None:
                print(f"stopped {pid}")
                return 0
            time.sleep(0.1)
        print(f"signalled {pid}, but it still holds the lock after 10s. "
              f"It may be mid-send; do not start a second one.")
        return 1

    # The wake-check. Deliberately separate from --status: status describes,
    # this one DECIDES, and the exit code carries the decision so a standing
    # instruction can branch on it without parsing prose. Reads one file, holds
    # no token and makes no platform call - the moment an agent most needs this
    # answer is when the platform is already refusing it.
    if args.check:
        from alb.watchdog import health

        v = health.verdict(pathlib.Path(args.root) / "state" / "health.json")
        print(f"{v.state}: {v.reason}")
        if v.action == "restart":
            # Names only what exists today. The first version of this line
            # recommended an alb flag that had not been built, so obeying the
            # verdict failed at the moment of obedience.
            print("action: restart the bridge in its own pane. Interrupt the "
                  "running one first (cmux send-key --surface <yours> ctrl+c), "
                  "then start it in that SAME pane. Never start a second one: "
                  "two pollers on one token is a conflict, not a backup.")
            return 2
        if v.action == "investigate":
            print("action: investigate. Absence is not death - this bridge may "
                  "never have run here.")
            return 3
        print("action: none")
        return 0

    # Status reads files only: no config, no token, no network. It is the
    # thing you run when you want to know whether to worry.
    if args.status:
        from alb.doctor import checks as _checks
        from alb.watchdog import health

        delivery = _checks.deliverability(pathlib.Path(args.root))
        if not delivery["can_deliver"]:
            print(f"DELIVERY: NOTHING WILL BE DELIVERED - {delivery['reason']}")
        state = pathlib.Path(args.root) / "state"
        bridge = health.status(state / "health.json", max_age=120)
        print(f"bridge : {bridge.state} - {bridge.reason}")
        # The canary is the only evidence the OUTBOUND path is alive, and its
        # last run is already a file. Without this line, --status answers
        # "should I worry" for inbound and the ring but not for sending.
        canary_log = state / "canary.log"
        if canary_log.is_file():
            lines = [l for l in canary_log.read_text(encoding="utf-8").splitlines() if l]
            print(f"canary : {lines[-1] if lines else 'log is empty'}")
        else:
            print("canary : never run - the send path is unproven")

        ring_path = state / "ring-health.json"
        if ring_path.is_file():
            import json as _json
            ring = _json.loads(ring_path.read_text(encoding="utf-8"))
            print(f"ring   : {ring['state']} - {ring['reason']}")
        else:
            print("ring   : unknown - no ring has been attempted yet")
        # A bridge that cannot deliver is not ok, however fresh its heartbeat.
        return 0 if (bridge.state == "ok" and delivery["can_deliver"]) else 1

    # Doctor runs BEFORE the config is loaded and never reads the token: it is
    # the tool you reach for when the bridge will not start.
    if args.doctor:
        import subprocess
        from alb.doctor import checks
        listing = subprocess.run(["ps", "-Ao", "uid,pid,command"],
                                 capture_output=True, text=True).stdout.splitlines()
        print(checks.summary(listing, os.getpid(), pathlib.Path(args.root), dict(os.environ)))
        return 0

    # W4 retrieval: read-only, no token, no config - the same standing as
    # --status. The mail root comes from the flag or defaults to --root.
    if args.list or args.show or args.search or args.thread or args.export:
        from alb import retrieval
        mail = pathlib.Path(args.mail_root) if args.mail_root else pathlib.Path(args.root)
        dirs = [mail / "inbox", mail / "processed", mail / "outbox"]
        try:
            if args.show:
                got = retrieval.show(dirs, args.show)
                print(pathlib.Path(got["where"]).read_text(encoding="utf-8"),
                      end="")
                return 0
            if args.export:
                if not args.out:
                    print("alb: --export needs --out PATH", file=sys.stderr)
                    return 2
                dest = retrieval.export_thread(
                    dirs, pathlib.Path(args.root) / "state", args.export,
                    args.out)
                print(f"alb: exported to {dest}")
                return 0
            rows = (retrieval.thread(dirs, args.thread) if args.thread
                    else retrieval.search(dirs, args.search) if args.search
                    else retrieval.list_letters(dirs))
            for r in rows:
                arrow = "->" if r["direction"] == "out" else "<-"
                print(f"{r['id']}  {arrow}  {r['from']} to {r['to']}  "
                      f"[{r['type']}]  thread {r['thread']}")
            if not rows:
                print("alb: no letters")
            return 0
        except retrieval.NoSuchLetter as exc:
            print(f"alb: {exc}", file=sys.stderr)
            return 1

    if not args.config:
        print("alb: --config is required", file=sys.stderr)
        return 2

    try:
        config = run.load_config(args.config)
    except run.ConfigError as exc:
        print(f"alb: {exc}", file=sys.stderr)
        return 2

    # Private from the first run: 0700 directories, not whatever the umask
    # happens to be.
    root = run.prepare_root(args.root)

    if args.canary:
        from alb.canary import probe
        try:
            rid = probe.run(api.Telegram(config["ALB_TOKEN"]), root)
        except probe.NoCanaryTarget as exc:
            print(f"alb: {exc}", file=sys.stderr)
            return 2
        except reply.AmbiguousOutcome as exc:
            print(f"alb: canary AMBIGUOUS, dead-lettered, NOT retried: {exc}",
                  file=sys.stderr)
            return 3
        except Exception as exc:
            print(f"alb: canary failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"alb: canary sent {rid} - now CONFIRM IT ARRIVED in your chat. "
              f"This proves the send path was accepted, not that it landed.")
        return 0

    # Replying is on the binary the operator starts, not a Python import they
    # have to reconstruct at 3am.
    # Where letters actually live, for every subcommand that reads one.
    mail = pathlib.Path(args.mail_root or config.get("ALB_MAIL_ROOT") or root)

    if args.reply_to:
        if not args.text:
            print("alb: --reply-to needs --text", file=sys.stderr)
            return 2
        try:
            rid = _reply_or_resume(
                api.Telegram(config["ALB_TOKEN"]), root / "inbox", root / "state",
                root / "allowlist.json", args.reply_to, args.text,
                # The SAME mailbox the poller writes to, including processed,
                # because a letter is filed there before anyone replies. In
                # integrated mode the letter is not under --root at all, and
                # searching only there refuses a reply to a letter that plainly
                # exists - the dogfood send bug in a new place.
                searched=[mail / "inbox", mail / "processed"],
                # v0.2: the reply becomes a durable outbound LETTER in the
                # mail root's outbox before the platform hears anything, and
                # its creation is the claim.
                outbox=mail / "outbox",
                agent=config.get("ALB_TO", "agent"))
        except reply.AmbiguousOutcome as exc:
            print(f"alb: AMBIGUOUS - dead-lettered for a human, NOT retried: {exc}",
                  file=sys.stderr)
            return 3
        except reply.Throttled as exc:
            # Not a refusal, and saying "refused" here would undo the whole
            # point of the outcome: nothing was delivered, nothing was lost,
            # and the letter is still claimed and waiting.
            wait = getattr(exc, "retry_after", None)
            when = f" for {wait}s" if wait else ""
            print(f"alb: DEFERRED - the platform asked us to wait{when}. "
                  f"The reply is composed and still claimed; nothing was sent "
                  f"and nothing was lost: {exc}\n"
                  f"alb: run the same --reply-to again to finish it. A reply "
                  f"that already went is still refused, never re-sent.",
                  file=sys.stderr)
            return 4
        except Exception as exc:
            print(f"alb: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"alb: sent {rid}")
        return 0

    platform = api.Telegram(config["ALB_TOKEN"],
                            offset_path=root / "state" / "offset.json")
    # Selection is honoured, not merely accepted. load_config has already
    # refused any value that is not a transport we ship.
    if config.get("ALB_NOTIFIER", "cmux") == "tmux":
        transport = tmux_transport.Tmux()
    else:
        transport = cmux_transport.Cmux()
    surface = config.get("ALB_SURFACE", "")

    try:
        lock = singleton.hold(root)
        lock.__enter__()
    except singleton.AlreadyRunning as exc:
        # Refuse locally rather than race until the platform notices.
        print(f"alb: {exc}", file=sys.stderr)
        return 4

    with lock_guard(lock):
        return _poll_forever(platform, transport, surface, root, args, config)


class Console:
    """Questions to a person at a terminal.

    ask_secret is a separate method rather than a flag on ask, so that the
    no-echo path is a different call and cannot be reached by accident with the
    wrong argument.
    """

    def say(self, text=""):
        print(text)

    def ask(self, question, default=""):
        answer = input(f"{question}: " if not question.endswith(": ") else question)
        return answer if answer.strip() else default

    def ask_secret(self, question):
        import getpass
        return getpass.getpass(question)


def _init(args):
    """alb --init. Interactive by definition."""
    from alb.setup import discover, wizard

    if not sys.stdin.isatty():
        # Refuse rather than consume a pipe. Every question here has a
        # consequence a script cannot consent to on someone's behalf, and the
        # token prompt would silently read whatever was piped in.
        print("alb: --init asks questions and needs a terminal", file=sys.stderr)
        return 2

    try:
        summary = wizard.init(
            args.root, Console(),
            chat_id_reader=discover.read_chat_ids,
            panes=discover.list_panes(),
        )
    except KeyboardInterrupt:
        # Ctrl-C during setup must not leave a token half-written.
        print("\nalb: setup cancelled", file=sys.stderr)
        return 1
    return 0 if summary else 1


# A log that grows without bound becomes the disk problem it was meant to
# diagnose. One rotation keeps the previous window - enough to cover a failure
# that began before anyone noticed, which is the whole point.
LOG_MAX_BYTES = 1 << 20


def log(root, message):
    """Print to the terminal AND record it under --root, timestamped.

    The record has to be the product's job. A relay died and the only trace
    was pane scrollback nobody kept: the alb.log beside it was shell
    redirection, so a restart without the redirect ended the record silently
    and the failure had to be reconstructed from a heartbeat and a memory.

    Never raises. A logging failure that stops the bridge is the same defect
    as dying on a rate limit - ending a process over something incidental -
    and it would arrive precisely when the platform is already failing, which
    is when the bridge is most worth keeping alive.
    """
    print(f"alb: {message}", file=sys.stderr)
    try:
        path = pathlib.Path(root) / "alb.log"
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_suffix(".log.1"))
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 - see docstring; never fail while failing
        pass


def _reply_or_resume(sender, inbox, state, allowlist_path, letter_id, text,
                     searched=None, outbox=None, agent="agent"):
    """Send the reply - or finish the one a throttle interrupted.

    The operator's way back from a deferred send is the gesture they already
    make: type the same reply again. Explicit and human, never an automatic
    retry, and safe because resume refuses anything that is not actually
    waiting - a delivered reply still meets AlreadyClaimed and is never
    re-sent.

    This lives in the CLI rather than in send_reply so the library keeps its
    strict one-reply-per-source semantics: it is the human repeating the
    command that authorises the second attempt, not the code deciding to.
    """
    from alb.outbound import store as outbound

    try:
        return reply.send_reply(
            sender, inbox, state, allowlist_path, letter_id, text,
            searched=searched, outbox=outbox, agent=agent)
    except outbound.AlreadyClaimed:
        out_id = f"reply-{letter_id}"
        if outbound.reconcile(state).get(out_id) != "throttled":
            raise
        # The text goes with it: retyping is the gesture, so the operator may
        # retype something different, and resume refuses rather than sending
        # the original body under a new instruction.
        return reply.resume_throttled(
            sender, inbox, state, allowlist_path, out_id, outbox=outbox,
            searched=searched, text=text)


def _report(cycle, once):
    """What the cycle did, for the operator standing at the terminal.

    A denied sender is told nothing - that is the security property and it is
    untouched. But the operator sees the same nothing whether the gate is
    working or the bridge is dead, and the only lever that looks relevant to
    them is the allowlist. This is the difference between an operator who can
    see the deny working and one who widens it to find out.

    Counts only. A denied chat id printed here would be a log of everyone who
    messaged the bot: not asked for by the operator, not consented to by the
    sender.
    """
    fetched = getattr(cycle, "fetched", len(cycle))
    denied = getattr(cycle, "denied", 0)
    duplicate = getattr(cycle, "duplicate", 0)

    parts = [f"fetched {fetched}", f"published {len(cycle)}"]
    if denied:
        # Named, because "denied 1" alone sends an operator looking for a
        # network fault. The allowlist is the cause and saying so is what stops
        # it being dismantled.
        parts.append(f"denied {denied} (allowlist)")
    if duplicate:
        # Deliberately distinct from a deny. Both publish nothing; only one of
        # them is the gate, and an operator sent to the allowlist to explain a
        # duplicate will widen it for no reason.
        parts.append(f"duplicate {duplicate} (already delivered)")

    # A quiet cycle prints on --once and stays quiet when running as a daemon:
    # a service that logs a line per idle long-poll buries the cycles that
    # matter. --once is a person asking a question and deserves an answer even
    # when the answer is nothing.
    if once or fetched:
        print("alb: " + " · ".join(parts))


def _poll_forever(platform, transport, surface, root, args, config):
    # First act on rising: reconcile outbound letters left in flight by a
    # crash - each dead-letters for a human, once, before any new work.
    from alb.outbound import store as outbound
    # Before the first blocking poll. Until this line existed, a freshly
    # restarted bridge presented the PREVIOUS process's timestamp for as long
    # as its first long poll blocked - so the healthiest moment in the
    # process's life read as its deadest.
    from alb.poller import loop as _loop
    _loop._write_heartbeat(root / "state" / "health.json",
                           state="starting", reason="starting")
    flagged = outbound.reconcile_at_startup(root / "state")
    for letter_id in flagged:
        log(root, f"reconciled in-flight outbound {letter_id} -> dead-letter")
    while True:
        try:
            published = run.run_once(
                platform, transport, surface, root,
                sender=config.get("ALB_FROM", "telegram-bridge"),
                recipient=config.get("ALB_TO", "agent"),
                mail_root=args.mail_root or config.get("ALB_MAIL_ROOT") or None,
                bus_binary=config.get("ALB_BUS_BINARY") or None,
            )
        except loop.PlatformConflict as exc:
            # A conflict is a YIELD, not an error: exit 0 so the token's holder
            # keeps running. Under a restart-on-crash-only policy this stays
            # down by design - that is the intended behaviour, not a failure.
            log(root, f"yielding, {exc}")
            # Record the stand-down before leaving. Without this the last
            # word in the health file is whatever we were doing before, it
            # goes stale, and a wake-check reads a deliberate yield as a
            # death - restarting a bridge to fight for a token it refused to
            # fight for. The conflict rule undone from outside.
            _loop._write_heartbeat(root / "state" / "health.json",
                                   state="yielded", reason="conflict")
            return 0
        except api.TransientFailure as exc:
            # Ordinary on a long poll. Wait it out rather than dying: the
            # bridge exists to survive exactly this.
            # The platform's own number is a FLOOR, not a suggestion: sleeping
            # our default 10s against a stated 17s just earns the next 429, and
            # capping at 30 silently violates any longer wait it asks for. Our
            # backoff applies on top, because a server naming a time for us has
            # not told us how many others it named it for.
            backoff = min(args.interval * 5, 30)
            floor = getattr(exc, "retry_after", None) or 0
            log(root, f"transient, retrying: {exc}")
            # Still going round. No cycle will complete for as long as we
            # correctly wait, and without this the bridge would look
            # progressively deader the longer it behaved correctly.
            _loop._write_heartbeat(
                root / "state" / "health.json", state="degraded",
                reason="throttled_429" if "429" in str(exc) else "upstream_5xx")
            time.sleep(max(backoff, floor))
            continue
        except api.FetchFailed as exc:
            # NOT a conflict. Do not send an operator hunting a second poller.
            log(root, f"fetch failed: {exc}")
            return 1

        _report(published, args.once)
        if args.once:
            return 0
        # NO SLEEP ON SUCCESS. getUpdates already blocked for up to the poll
        # timeout waiting for a message, so a sleep here adds latency to every
        # quiet cycle and buys nothing. The wait is the long poll. --interval
        # is only the backoff after a transient failure.


