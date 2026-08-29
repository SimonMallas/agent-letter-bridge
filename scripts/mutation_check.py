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

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "letter" / "store.py"

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
        "    letter_id = publish(inbox, body, meta)\n\n    delivered.append(update_id)",
        "    delivered.append(update_id)\n"
        '    tmp0 = pathlib.Path(f"{ledger}.tmp")\n'
        '    tmp0.write_text(json.dumps(delivered[-cap:]), encoding="utf-8")\n'
        "    os.replace(tmp0, ledger)\n"
        "    letter_id = publish(inbox, body, meta)"),
}


def main():
    original = SRC.read_text(encoding="utf-8")
    failures = []
    try:
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
    print(f"\nmutation gate: {len(MUTATIONS)} invariants pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
