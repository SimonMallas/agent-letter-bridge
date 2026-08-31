#!/usr/bin/env python3
"""Mutation gate: every invariant must have a test that fails when it is off.

A test that still passes with the invariant disabled is not a test of that
invariant - it is coverage without proof. This gate breaks each invariant in
turn and asserts the suite goes red.

Exit 0 = every invariant is genuinely pinned. Exit 1 = one is not.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "alb" / "letter" / "store.py"
SEND = ROOT / "src" / "alb" / "send" / "reply.py"
POLL = ROOT / "src" / "alb" / "poller" / "loop.py"
NOTIFY = ROOT / "src" / "alb" / "notifier" / "ring.py"
ALLOW = ROOT / "src" / "alb" / "allowlist" / "gate.py"
TG = ROOT / "src" / "alb" / "adapters" / "telegram" / "api.py"
CMUX = ROOT / "src" / "alb" / "adapters" / "cmux" / "transport.py"
BRIDGE = ROOT / "src" / "alb" / "bridge" / "run.py"

# invariant -> (file, tests module, old, new)
EXTRA = {
    "allowlist denies on a missing file": (
        ALLOW, "tests.test_allowlist", "    except OSError:\n        return False",
        "    except OSError:\n        return True"),
    "ack only after the letter lands": (
        POLL, "tests.test_poller",
        '        letter_id = store.publish_once(', '        platform.ack(item["update_id"])\n        letter_id = store.publish_once('),
    "denied sender produces no letter": (
        POLL, "tests.test_poller",
        "        if gate.allows(allowlist_path, chat_id):",
        "        if False:"),
    "reply destination read from the letter": (
        SEND, "tests.test_send",
        "    chat_id = destination(stored.meta)", '    chat_id = "not-the-test-chat"'),
    "allowlist refuses a non-list chats value": (
        ALLOW, "tests.test_allowlist",
        "if not isinstance(chats, list) or not chats:", "if False:"),
    "allowlist rechecked at send": (
        SEND, "tests.test_send",
        "    if not gate.allows(allowlist_path, chat_id):", "    if False:"),
    "claim before send blocks replay": (
        SEND, "tests.test_send",
        "        raise AlreadyClaimed(f\"{reply_id}: already attempted\")",
        "        return path"),
    "ambiguous outcome dead-letters": (
        SEND, "tests.test_send",
        "        _dead_letter(state, claim.stem, letter_id, str(exc))", "        pass"),
    "ring carries no content": (
        NOTIFY, "tests.test_notifier",
        "    transport.deliver(surface, DOORBELL_LINE)",
        "    transport.deliver(surface, DOORBELL_LINE + store.resolve(inbox, letter_id).body)"),
    "deny still consumes the update": (
        POLL, "tests.test_poller",
        '        platform.ack(item["update_id"])\n\n    # Only after a poll',
        '        if published:\n            platform.ack(item["update_id"])\n\n    # Only after a poll'),
    "every completed poll writes a heartbeat": (
        POLL, "tests.test_poller",
        "    if health_path is not None:\n        _write_heartbeat(health_path)",
        "    if False:\n        _write_heartbeat(health_path)"),
    "unclassified failure dead-letters": (
        SEND, "tests.test_send",
        "        _dead_letter(state, claim.stem, letter_id,\n"
        '                     f"unclassified {type(exc).__name__}: {exc}")',
        "        pass"),
    "conflict maps to a yield": (
        TG, "tests.test_telegram_adapter",
        '                raise loop.PlatformConflict("another consumer holds this token") from None',
        "                pass"),
    "server error is ambiguous, not refused": (
        TG, "tests.test_telegram_adapter",
        "            if exc.code >= 500:", "            if False:"),
    "network failure is ambiguous": (
        TG, "tests.test_telegram_adapter",
        '            raise reply.AmbiguousOutcome(f"network failure: {exc}") from None',
        '            raise reply.DefiniteRefusal("network") from None'),
    "offset sent is last-acked plus one": (
        TG, "tests.test_telegram_adapter",
        'params["offset"] = self._acked + 1', 'params["offset"] = self._acked'),
    "ring payload must be a single line": (
        CMUX, "tests.test_cmux_adapter",
        'if "\\n" in line or "\\r" in line:', "if False:"),
    "ring addresses an explicit surface": (
        CMUX, "tests.test_cmux_adapter",
        '        if not surface:\n            raise ring.NoTargetSurface("no surface; refusing to guess a pane")',
        "        if False:\n            pass"),
    "config refuses a readable-by-others token file": (
        BRIDGE, "tests.test_bridge",
        "    if mode & (stat.S_IRGRP | stat.S_IROTH):", "    if False:"),
    "config refuses when a required setting is missing": (
        BRIDGE, "tests.test_bridge",
        "    if missing:", "    if False:"),
    "a batch rings once, not once per letter": (
        BRIDGE, "tests.test_bridge",
        "        ring.notify(transport, surface, root / \"inbox\", published[-1])",
        "        for _p in published:\n            ring.notify(transport, surface, root / \"inbox\", _p)"),
    "a dead notifier never costs a letter": (
        BRIDGE, "tests.test_bridge",
        "    except Exception as exc:\n        # Letters are authoritative",
        "    except ZeroDivisionError as exc:\n        # Letters are authoritative"),
    "the offset survives a restart": (
        TG, "tests.test_telegram_adapter",
        "            self._save_offset()", "            pass"),
    "a cycle transmits consumption to the platform": (
        BRIDGE, "tests.test_bridge",
        "    if confirm is not None:\n        confirm()", "    if False:\n        confirm()"),
    "non-message updates are consumed, not dropped": (
        TG, "tests.test_telegram_adapter",
        '"chat_id": str(message.get("chat", {}).get("id", "")) if message else "",',
        '"chat_id": str(message["chat"]["id"]),'),
    "ring failure is recorded, not merely swallowed": (
        BRIDGE, "tests.test_bridge",
        '        _record_ring(root, "failing", f"{type(exc).__name__}: {exc}")',
        "        pass"),
    "offset persists only after the platform accepts": (
        TG, "tests.test_telegram_adapter",
        "            self._save_offset()\n        except urllib.error.HTTPError as exc:",
        "        except urllib.error.HTTPError as exc:"),
    "a transient network failure is not fatal": (
        TG, "tests.test_telegram_adapter",
        '        except OSError as exc:',
        "        except ZeroDivisionError as exc:"),
    "one bridge per state directory": (
        ROOT / "src" / "alb" / "bridge" / "singleton.py", "tests.test_singleton",
        "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "            pass"),
    "the probe matches an executable, not a mention": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor_probe",
        "        head = argv_for_match[:2]", "        head = argv_for_match"),
    "the doctor names what it cannot prove": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor_probe",
        '    lines.append("  A consumer on ANOTHER MACHINE is not detectable from here.")',
        "    pass"),
    "canary refuses without an allowlisted target": (
        ROOT / "src" / "alb" / "canary" / "probe.py", "tests.test_canary",
        '        raise NoCanaryTarget("no allowlisted chat to send a canary to")',
        '        return "999"'),
    "canary goes through the real send path": (
        ROOT / "src" / "alb" / "canary" / "probe.py", "tests.test_canary",
        "        reply_id = reply.send_reply(", "        reply_id = 'faked'  # noqa\n        _unused = (reply.send_reply,) and ("),
    "canary fixtures never enter the inbox": (
        ROOT / "src" / "alb" / "canary" / "probe.py", "tests.test_canary",
        '    fixtures = root / "state" / "canary"', '    fixtures = root / "inbox"'),
    "the credential check is scoped to this tool": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor",
        "        key.upper().startswith(_OUR_PREFIX)\n        and any(",
        "        any("),
    "the probe finds an env-prefixed invocation": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor_probe",
        '        if argv and pathlib.PurePath(argv[0]).name == "env":',
        "        if False:"),
    "an undeliverable bridge is not reported as healthy": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor_probe",
        '        lines.append("*** NOTHING WILL BE DELIVERED ***")', "        pass"),
    "a missing allowlist means it cannot deliver": (
        ROOT / "src" / "alb" / "doctor" / "checks.py", "tests.test_doctor_probe",
        '    if not path.is_file():\n        return {"can_deliver": False,',
        '    if False:\n        return {"can_deliver": False,'),
    "letters carry the routing envelope": (
        ROOT / "src" / "alb" / "letter" / "store.py", "tests.test_envelope",
        '    if "id" in meta:\n        meta["id"] = letter_id',
        "    if False:\n        meta[\"id\"] = letter_id"),
    "the envelope precedes platform fields": (
        ROOT / "src" / "alb" / "letter" / "store.py", "tests.test_envelope",
        "    out = {k: meta[k] for k in ENVELOPE_ORDER if k in meta}", "    out = {}"),
    "the id timestamp is dashed for the shared parser": (
        ROOT / "src" / "alb" / "letter" / "store.py", "tests.test_envelope",
        'stamp = time.strftime("%Y-%m-%dT%H%M%S")',
        'stamp = time.strftime("%Y%m%dT%H%M%S")'),
    "an empty value is a bare key": (
        ROOT / "src" / "alb" / "letter" / "store.py", "tests.test_envelope",
        'lines.append(f"{key}: {text}" if text else f"{key}:")',
        'lines.append(f"{key}: {text}")'),
    "it runs without a multiplexer": (
        BRIDGE, "tests.test_bridge",
        'REQUIRED = ("ALB_TOKEN",)', 'REQUIRED = ("ALB_TOKEN", "ALB_SURFACE")'),
    "a missing surface is recorded, not silent": (
        BRIDGE, "tests.test_bridge",
        '        _record_ring(root, "disabled", "no ALB_SURFACE configured; mail lands, nothing rings")',
        "        pass"),
    "the letter's NAME is made durable, not just its bytes": (
        ROOT / "src" / "alb" / "letter" / "store.py", "tests.test_letter",
        "        _fsync_dir(inbox)", "        pass"),
    "the claim's NAME is made durable": (
        SEND, "tests.test_send",
        "    _fsync_dir(path.parent)", "    pass"),
    "the dependency gate itself can fail": (
        ROOT / "scripts" / "deps_check.py", "tests.test_deps_gate",
        "        if deps:", "        if False:"),
    "a smuggled manifest is refused": (
        ROOT / "scripts" / "deps_check.py", "tests.test_deps_gate",
        "        if (ROOT / name).exists():", "        if False:"),
    "an unknown setting is refused, not ignored": (
        BRIDGE, "tests.test_bridge", "    if unknown:", "    if False:"),
    "a reply finds a letter that has been swept": (
        SEND, "tests.test_send",
        "    for directory in (searched or [inbox]):", "    for directory in [inbox]:"),
    "a reply resolves the destination the poller actually writes": (
        SEND, "tests.test_send",
        'DESTINATION_KEYS = ("telegram_chat_id", "chat_id")',
        'DESTINATION_KEYS = ("chat_id",)'),
    "a missing pyproject is refused": (
        ROOT / "scripts" / "deps_check.py", "tests.test_deps_gate",
        '        failures.append("pyproject.toml is missing: the package must be installable")',
        "        pass"),
    "the state layout is created private": (
        BRIDGE, "tests.test_permissions",
        "            path.chmod(DIR_MODE)", "            pass"),
    "state files are created private": (
        BRIDGE, "tests.test_permissions",
        "    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)",
        "    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)"),
    "an unsupported notifier is refused": (
        BRIDGE, "tests.test_bridge",
        "    if notifier not in NOTIFIERS:", "    if False:"),
    "the tmux payload is sent literally": (
        ROOT / "src" / "alb" / "adapters" / "tmux" / "transport.py",
        "tests.test_tmux_adapter",
        '_run([self._binary, "send-keys", "-t", surface, "-l", line])',
        '_run([self._binary, "send-keys", "-t", surface, line])'),
    "the platform destination field takes precedence": (
        SEND, "tests.test_send",
        'DESTINATION_KEYS = ("telegram_chat_id", "chat_id")',
        'DESTINATION_KEYS = ("chat_id", "telegram_chat_id")'),
    "no surface means no ring": (
        NOTIFY, "tests.test_notifier",
        '        raise NoTargetSurface("no registered surface; refusing to guess")',
        "        surface = \"GUESS\""),
}

MUTATIONS = {
    "two-fence required": (
        'if len(parts) < 3 or parts[0] != "":', "if False:"),
    "path-shaped identifier refused": (
        "if not isinstance(letter_id, str) or not _SAFE_ID.match(letter_id):",
        "if False:"),
    "exact resolution, no substring match": (
        "    if not path.is_file():",
        '    if not path.is_file():\n'
        '        _m = sorted(pathlib.Path(inbox).glob(f"{letter_id}*.md"))\n'
        "        if _m:\n            path = _m[0]\n        else:"),
    "atomic publish via temp + hardlink": (
        "        os.link(temp, dest)",
        '        dest.write_text(_serialise(meta, body), encoding="utf-8")'),
    "ledger recorded AFTER the letter": (
        "    letter_id = publish(inbox, body, meta, update_id=update_id)\n"
        "    _record_delivered(ledger, delivered, update_id, cap)",
        "    _record_delivered(ledger, delivered, update_id, cap)\n"
        "    letter_id = publish(inbox, body, meta, update_id=update_id)"),
    "durable update lookup closes the crash window": (
        "    if find_by_update(update_id, searched) is not None:", "    if False:"),
    "token match is VERIFIED, never trusted": (
        '            if str(found.meta.get("source_id", "")) == str(update_id):\n'
        "                return path",
        "            return path"),
    "the letter records its own update id": (
        '    meta["source_id"] = update_id', "    pass"),
}


def _purge_bytecode():
    """Remove every __pycache__ under the tree.

    THIS IS NOT TIDINESS. Python invalidates cached bytecode on (mtime, size).
    A mutation writes a file, the test run compiles it, and the restore writes
    a file of a DIFFERENT size but often within the same mtime granularity - so
    a stale .pyc can survive in either direction:

      - after a run, the tree behaves as MUTATED while the source is correct,
        which is how a green suite and a red one disagreed and a broken commit
        got pushed;
      - during a run, a mutation may not take effect at all, and the gate
        reports a pin that was never actually exercised.

    The second is the dangerous one: a gate that silently stops mutating still
    prints ok. So caches are purged around every mutation, and the subprocess
    is told not to write new ones.
    """
    for cache in ROOT.rglob("__pycache__"):
        for f in cache.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            cache.rmdir()
        except OSError:
            pass


def _run(name, target, tests, old, new, failures):
    original = target.read_text(encoding="utf-8")
    if old not in original:
        failures.append(f"{name}: mutation anchor no longer present")
        print(f"  FAIL {name} (anchor missing)")
        return
    try:
        _purge_bytecode()
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", tests],
            capture_output=True, text=True, cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
    finally:
        target.write_text(original, encoding="utf-8")
        _purge_bytecode()
    if result.returncode == 0:
        failures.append(f"{name}: DISABLED BUT NO TEST FAILED")
    print(f"  {'ok  ' if result.returncode else 'FAIL'} {name}")


def main():
    original = SRC.read_text(encoding="utf-8")
    failures = []
    try:
        for name, (target, tests, old, new) in EXTRA.items():
            _run(name, target, tests, old, new, failures)
        for name, (old, new) in MUTATIONS.items():
            if old not in original:
                failures.append(f"{name}: mutation anchor no longer present")
                continue
            SRC.write_text(original.replace(old, new, 1), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.test_letter"],
                capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                 "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if result.returncode == 0:
                failures.append(f"{name}: DISABLED BUT NO TEST FAILED")
            print(f"  {'ok  ' if result.returncode else 'FAIL'} {name}")
    finally:
        SRC.write_text(original, encoding="utf-8")
        _purge_bytecode()

    if failures:
        print("\nMUTATION GATE FAILED\n")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"\nmutation gate: {len(MUTATIONS) + len(EXTRA)} invariants pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
