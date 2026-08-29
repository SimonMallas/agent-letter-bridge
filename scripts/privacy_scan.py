#!/usr/bin/env python3
"""Privacy scan — refuse to commit anything that would leak on a public flip.

A private repo's ENTIRE history becomes public if it is ever made public. So this
runs from commit one, not at flip time. A pre-flip audit then verifies the
discipline held; it is not a cleanup pass.

Patterns here are STRUCTURAL on purpose. Listing our own private paths or tool
names would put them in the repo, making the scanner the leak it exists to
prevent. Supply site-specific patterns out of band via ALB_EXTRA_PATTERNS (a path
to a newline-separated regex file, never committed).

Exit 0 = clean. Exit 1 = findings.
"""
import os
import re
import subprocess
import sys

PATTERNS = [
    (r"/Users/[A-Za-z0-9._-]+/", "absolute macOS home path"),
    (r"/home/[A-Za-z0-9._-]+/", "absolute Linux home path"),
    (r"/Volumes/[A-Za-z0-9._-]+", "external volume path"),
    (r"\bgui/\d+/", "launchd GUI domain path"),
    (r"\b\d{8,10}:[A-Za-z0-9_-]{30,}", "bot-token-shaped string"),
    # Assembled from fragments so this file does not match its own pattern.
    # That is what lets the scanner run with NO exemptions - see the SELF note.
    (r"(?i)co-authored" + r"-by:.*\b(claude|copilot|gpt|gemini|cursor)\b",
     "AI assistant commit trailer"),
    (r"(?i)\bclaude-session\b", "assistant session trailer"),
    (r"(?i)generated with \[?(claude|copilot)", "assistant attribution"),
    (r"(?i)\bclaude\.ai/code\b", "assistant session URL"),
    (r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", "uppercase UUID (session/surface id)"),
]

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}

# NO EXEMPTIONS. This file is scanned like every other. Patterns that would
# otherwise match their own source are assembled from fragments above. An
# exempted file is a place to hide a real leak, including this one.


def load_extra():
    path = os.environ.get("ALB_EXTRA_PATTERNS")
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append((line, "site-specific pattern"))
    return out


def tracked_files():
    files = set()
    for args in (["git", "ls-files"], ["git", "diff", "--cached", "--name-only"]):
        try:
            out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
            files.update(f for f in out.splitlines() if f)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if files:
        return sorted(files)
    # No tracked or staged files: scan the working tree rather than pass vacuously.
    found = []
    for root, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [os.path.join(root, n)[2:] for n in names]
    return found


def scan_commit_message(path):
    """Trailers must never reach a commit message, not just the tree."""
    findings = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return findings
    for pattern, label in PATTERNS:
        for m in re.finditer(pattern, text):
            findings.append(("COMMIT_MSG", m.group(0), label))
    return findings


def main():
    if len(sys.argv) > 1:
        findings = scan_commit_message(sys.argv[1])
    else:
        findings = []
        checks = PATTERNS + load_extra()
        for rel in tracked_files():
            if not os.path.exists(rel):
                continue
            try:
                with open(rel, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                for pattern, label in checks:
                    m = re.search(pattern, line)
                    if m:
                        findings.append((f"{rel}:{n}", m.group(0), label))

    if findings:
        print("PRIVACY SCAN FAILED\n")
        for where, hit, label in findings:
            # Never echo the match. A caught token printed here is copied into
            # CI logs - a second store, public on flip. Location and class only.
            print(f"  {where}\n    {label} ({len(hit)} chars)")
        print(f"\n{len(findings)} finding(s). Nothing is committed.")
        return 1
    print("privacy scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
