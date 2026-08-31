"""Agent Letter Bridge - your agents, reachable from your phone, as durable mail.

A message from your chat app becomes a durable file on disk BEFORE it is
acknowledged and BEFORE any agent is notified. The file is the source of truth;
the notification only makes it faster.

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
import pathlib
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="alb", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="", help="path to the env file (mode 600)")
    parser.add_argument("--root", required=True, help="state directory")
    parser.add_argument("--once", action="store_true", help="one cycle, then exit")
    parser.add_argument("--canary", action="store_true",
                        help="prove the send path is alive; sends to your own chat")
    parser.add_argument("--status", action="store_true",
                        help="report bridge and ring health; reads only")
    parser.add_argument("--doctor", action="store_true",
                        help="local diagnostics; holds no token, makes no platform call")
    parser.add_argument("--reply-to", metavar="LETTER_ID",
                        help="reply to a stored letter instead of polling")
    parser.add_argument("--text", help="reply body, with --reply-to")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)

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
        import os
        import subprocess
        from alb.doctor import checks
        listing = subprocess.run(["ps", "-Ao", "uid,pid,command"],
                                 capture_output=True, text=True).stdout.splitlines()
        print(checks.summary(listing, os.getpid(), pathlib.Path(args.root), dict(os.environ)))
        return 0

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
    if args.reply_to:
        if not args.text:
            print("alb: --reply-to needs --text", file=sys.stderr)
            return 2
        try:
            rid = reply.send_reply(
                api.Telegram(config["ALB_TOKEN"]), root / "inbox", root / "state",
                root / "allowlist.json", args.reply_to, args.text,
                searched=[root / "inbox", root / "processed"])
        except reply.AmbiguousOutcome as exc:
            print(f"alb: AMBIGUOUS - dead-lettered for a human, NOT retried: {exc}",
                  file=sys.stderr)
            return 3
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


def _poll_forever(platform, transport, surface, root, args, config):
    while True:
        try:
            published = run.run_once(
                platform, transport, surface, root,
                sender=config.get("ALB_FROM", "telegram-bridge"),
                recipient=config.get("ALB_TO", "agent"),
            )
        except loop.PlatformConflict as exc:
            # A conflict is a YIELD, not an error: exit 0 so the token's holder
            # keeps running. Under a restart-on-crash-only policy this stays
            # down by design - that is the intended behaviour, not a failure.
            print(f"alb: yielding, {exc}", file=sys.stderr)
            return 0
        except api.TransientFailure as exc:
            # Ordinary on a long poll. Wait it out rather than dying: the
            # bridge exists to survive exactly this.
            print(f"alb: transient, retrying: {exc}", file=sys.stderr)
            time.sleep(min(args.interval * 5, 30))
            continue
        except api.FetchFailed as exc:
            # NOT a conflict. Do not send an operator hunting a second poller.
            print(f"alb: fetch failed: {exc}", file=sys.stderr)
            return 1

        if published:
            print(f"alb: {len(published)} letter(s)")
        if args.once:
            return 0
        # NO SLEEP ON SUCCESS. getUpdates already blocked for up to the poll
        # timeout waiting for a message, so a sleep here adds latency to every
        # quiet cycle and buys nothing. The wait is the long poll. --interval
        # is only the backoff after a transient failure.


