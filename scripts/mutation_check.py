#!/usr/bin/env python3
"""Mutation gate: every invariant must have a test that fails when it is off.

A test that still passes with the invariant disabled is not a test of that
invariant - it is coverage without proof. This gate breaks each invariant in
turn and asserts the suite goes red.

Exit 0 = every invariant is genuinely pinned. Exit 1 = one is not.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "letter" / "store.py"
SEND = ROOT / "src" / "send" / "reply.py"
POLL = ROOT / "src" / "poller" / "loop.py"
NOTIFY = ROOT / "src" / "notifier" / "ring.py"
ALLOW = ROOT / "src" / "allowlist" / "gate.py"

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
        '    chat_id = stored.meta.get("chat_id")', '    chat_id = "111"'),
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
}


def _run(name, target, tests, old, new, failures):
    original = target.read_text(encoding="utf-8")
    if old not in original:
        failures.append(f"{name}: mutation anchor no longer present")
        print(f"  FAIL {name} (anchor missing)")
        return
    try:
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", tests],
            capture_output=True, text=True, cwd=ROOT,
        )
    finally:
        target.write_text(original, encoding="utf-8")
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
                capture_output=True, text=True, cwd=SRC.parents[2],
            )
            if result.returncode == 0:
                failures.append(f"{name}: DISABLED BUT NO TEST FAILED")
            print(f"  {'ok  ' if result.returncode else 'FAIL'} {name}")
    finally:
        SRC.write_text(original, encoding="utf-8")

    if failures:
        print("\nMUTATION GATE FAILED\n")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"\nmutation gate: {len(MUTATIONS) + len(EXTRA)} invariants pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
