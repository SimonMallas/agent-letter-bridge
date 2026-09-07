#!/usr/bin/env python3
"""Editorial tripwire — known banned phrases in Markdown and commit messages.

This is a TRIPWIRE, not a rule engine. It catches exact recurrences of
phrases the project has decided must not appear in public prose. A pass means
no KNOWN banned phrase was found; whether prose follows the editorial policy
in meaning remains human review, and a clean result must never be cited as
proof of it. The policy itself, with examples and remedies, is the
"Repository editorial checks" section of CONTRIBUTING.md.

Scope and honesty:
- scans tracked Markdown as staged in the git INDEX (the bytes a commit would
  actually record), or the working tree with --worktree (for CI on a checkout);
- scans a commit message when given a file argument;
- code fences and quotations are scanned like everything else - a banned
  phrase in an example is still published;
- a failure to inspect (git unavailable, unreadable input) exits 2 loudly.
  Inability to check is not a pass.

Patterns are added only for phrasing that has actually recurred, and are
assembled from fragments so this file cannot match itself.
"""
import re
import subprocess
import sys

_OPT = "option" + "al"
_OPTLY = "option" + "ally"

PATTERNS = [
    # The doorbell must not be described as something a deployment can skip.
    (rf"(?i)\b({_OPT}|discretion" + rf"ary)[- ](bell|doorbell|ring\b)",
     "bell described as skippable"),
    (rf"(?i)\b(bell|doorbell|ring) (is|as) ({_OPT}|discretion" + r"ary)\b",
     "bell described as skippable"),
    (rf"(?i)\b(ring|bell|doorbell)s? as {_OPT}\b",
     "bell described as skippable"),
    (rf"(?i)\b{_OPT} \w+ (ring|bell|doorbell)\b",
     "bell described as skippable"),
    (rf"(?i)\b{_OPTLY}[ ,—–-]+a (ring|bell|doorbell)\b",
     "bell described as skippable"),
    # Comparisons cite named tools; category-wide assertions are refused.
    (r"(?i)\bevery tool of (this|its|the) (kind|class)\b",
     "universal tool-category comparison"),
    # Public prose carries no numeric test/invariant totals.
    (r"(?<![-\w])\d+\s+(tests?|invariants?|subtests?|pins)\b",
     "numeric test/invariant total"),
]

NOTE = "see CONTRIBUTING.md: Repository editorial checks"


def _run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def _markdown_names():
    """Tracked .md names, NUL-separated so no name is ever quoted.

    Without -z, git quotes a name containing a double quote or non-ASCII, the
    quoted form no longer ends in .md, and the file silently drops out of the
    scan - a byte-selection failure, not a pattern limitation.
    """
    out = _run(["git", "ls-files", "-z"])
    return [f for f in out.split("\0") if f.endswith(".md")]


def index_markdown():
    """Names and CONTENT from the index: the bytes a commit records.

    Reading the working tree here is a fail-open: with partial staging, the
    staged bytes and the file on disk differ, and the commit takes the staged
    ones. The tripwire reads what will actually be published.
    """
    return [(name, _run(["git", "show", f":{name}"]))
            for name in _markdown_names()]


def worktree_markdown():
    names = _markdown_names()
    out = []
    for name in names:
        try:
            with open(name, encoding="utf-8", errors="replace") as fh:
                out.append((name, fh.read()))
        except FileNotFoundError:
            continue
    return out


def scan(source, text, findings):
    for n, line in enumerate(text.splitlines(), 1):
        for pattern, label in PATTERNS:
            m = re.search(pattern, line)
            if m:
                findings.append((source, n, m.group(0), label))


def main(argv):
    findings = []
    try:
        if argv and argv[0] != "--worktree":
            with open(argv[0], encoding="utf-8", errors="replace") as fh:
                scan("COMMIT_MSG", fh.read(), findings)
        else:
            files = worktree_markdown() if argv else index_markdown()
            for name, text in files:
                scan(name, text, findings)
    except (OSError, RuntimeError) as exc:
        print(f"editorial check: could not inspect: {exc}", file=sys.stderr)
        return 2
    if findings:
        print(f"editorial check: known banned phrase ({NOTE})\n", file=sys.stderr)
        for source, n, match, label in findings:
            print(f"  {source}:{n}: {match!r} — {label}", file=sys.stderr)
        return 1
    print("editorial check: no known banned phrase; semantic review stays human")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
