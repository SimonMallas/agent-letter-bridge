#!/usr/bin/env python3
"""Mutation gate: every invariant must have a test that fails when it is off.

A test that still passes with the invariant disabled is not a test of that
invariant - it is coverage without proof. This gate breaks each invariant in
turn and asserts the suite goes red.

Exit 0 = every invariant is genuinely pinned. Exit 1 = one is not.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

# SOURCE_ROOT is the operator's tree. Mutations never write here.
# ROOT is rebound for EXTRA path construction at import; _run maps those
# paths into the isolated copy.
SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT
SRC = ROOT / "src" / "alb" / "letter" / "store.py"
SEND = ROOT / "src" / "alb" / "send" / "reply.py"
MSGINDEX = ROOT / "src" / "alb" / "msgindex.py"
RETRIEVAL = ROOT / "src" / "alb" / "retrieval.py"
POLL = ROOT / "src" / "alb" / "poller" / "loop.py"
NOTIFY = ROOT / "src" / "alb" / "notifier" / "ring.py"
ALLOW = ROOT / "src" / "alb" / "allowlist" / "gate.py"
TG = ROOT / "src" / "alb" / "adapters" / "telegram" / "api.py"
CMUX = ROOT / "src" / "alb" / "adapters" / "cmux" / "transport.py"
BRIDGE = ROOT / "src" / "alb" / "bridge" / "run.py"
OUTBOUND = ROOT / "src" / "alb" / "outbound" / "store.py"
WIZARD = ROOT / "src" / "alb" / "setup" / "wizard.py"
DISCOVER = ROOT / "src" / "alb" / "setup" / "discover.py"

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
    "a placeholder surface is refused": (
        BRIDGE, "tests.test_bridge",
        "    if surface and surface.lower().strip(\"<>\") in PLACEHOLDER_SURFACES:",
        "    if False:"),
    "a deny is counted for the operator": (
        POLL, "tests.test_poller",
        "        else:\n            result.denied += 1",
        "        else:\n            pass"),
    "a duplicate is not counted as a deny": (
        POLL, "tests.test_poller",
        "                result.duplicate += 1",
        "                result.denied += 1"),
    "setup writes a deny-all allowlist": (
        WIZARD, "tests.test_setup",
        '    chats = _chat_ids(console, token, chat_id_reader)',
        '    chats = _chat_ids(console, token, chat_id_reader) or ["111"]'),
    "setup never overwrites an existing file": (
        WIZARD, "tests.test_setup",
        "    if allow_path.exists():", "    if False:"),
    "setup does not reach the network unless asked": (
        WIZARD, "tests.test_setup",
        '    if choice != "read" or reader is None:', "    if False:"),
    "setup asks for the token without echo": (
        WIZARD, "tests.test_setup",
        '    token = console.ask_secret("  token (not echoed): ").strip()',
        '    token = console.ask("  token: ").strip()'),
    "the chat id lookup sends no offset": (
        DISCOVER, "tests.test_setup_discover",
        '{"timeout": 0}', '{"timeout": 0, "offset": 1}'),
    "the chat id lookup returns chat not from": (
        DISCOVER, "tests.test_setup_discover",
        '        chat = message.get("chat") or {}',
        '        chat = message.get("from") or {}'),
    "saying yes without both details is not silently downgraded": (
        WIZARD, "tests.test_setup",
        '        integrated = False\n        mailbox = recipient = ""',
        '        pass'),
    "the helper is asked for only when it is missing": (
        WIZARD, "tests.test_setup",
        '        if not found:', '        if True:'),
    "the outbound letter create is the claim": (
        OUTBOUND, "tests.test_outbound",
        "    except FileExistsError:\n        raise AlreadyClaimed",
        "    except FileExistsError:\n        pass\n    if False:\n        raise AlreadyClaimed"),
    "delivery events never overwrite": (
        OUTBOUND, "tests.test_outbound",
        "    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n    with os.fdopen(fd, \"w\", encoding=\"utf-8\") as handle:\n        json.dump(payload, handle)",
        "    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)\n    with os.fdopen(fd, \"w\", encoding=\"utf-8\") as handle:\n        json.dump(payload, handle)"),
    "in-flight reconciles ambiguous, never clean": (
        OUTBOUND, "tests.test_outbound",
        '        verdicts[d.name] = "ambiguous" if "sending" in events else "unsent"',
        '        verdicts[d.name] = "unsent"'),
    "the correspondent store is authoritative over the derivation": (
        OUTBOUND, "tests.test_outbound",
        "    if origin in table:\n        return table[origin]",
        "    if False:\n        return table[origin]"),
    "the reply path is letter-first, not legacy": (
        SEND, "tests.test_send",
        "    out_id = outbound.compose(",
        "    return _send_legacy(sender, state, letter_id, chat_id, text)\n    out_id = outbound.compose("),
    "the platform message id lands in the sent event": (
        SEND, "tests.test_send",
        '    outbound.record_event(state, out_id, "sent",\n                          platform_message_id=str(platform_id))',
        '    outbound.record_event(state, out_id, "sent")'),
    "ambiguous still dead-letters on the letter-first path": (
        SEND, "tests.test_send",
        '        outbound.record_event(state, out_id, "ambiguous", detail=str(exc))\n        _dead_letter(state, out_id, letter_id, str(exc))',
        '        outbound.record_event(state, out_id, "ambiguous", detail=str(exc))'),
    "startup reconciliation dead-letters, once": (
        OUTBOUND, "tests.test_outbound",
        '        record_event(state, letter_id, "dead", detail="reconciled at restart")',
        '        pass'),
    "the index key is the full triple, never the bare id": (
        MSGINDEX, "tests.test_w2_identity_threading",
        '    return f"{platform}|{origin}|{message_id}"',
        '    return f"{platform}|{message_id}"'),
    "reply-to joins the targets thread without moving the pointer": (
        POLL, "tests.test_w2_identity_threading",
        "            if not reply_target:",
        "            if True:"),
    "slash-new only cuts at position zero": (
        POLL, "tests.test_w2_identity_threading",
        'cut=text.split(" ", 1)[0] == "/new" if text else False',
        'cut=("/new" in text) if text else False'),
    "search is exact substring, never everything": (
        RETRIEVAL, "tests.test_retrieval",
        "        if any(text in h for h in haystacks):",
        "        if True:"),
    "show refuses on anything but the exact id": (
        RETRIEVAL, "tests.test_retrieval",
        '        if r["id"] == letter_id:',
        '        if r["id"].startswith(letter_id[:8]):'),
    "the archive lists in publish order": (
        RETRIEVAL, "tests.test_retrieval",
        '    rows.sort(key=lambda r: (r["mtime"], r["id"]))',
        '    rows.sort(key=lambda r: r["id"], reverse=True)'),
    "deny still consumes the update": (
        POLL, "tests.test_poller",
        '        platform.ack(item["update_id"])\n\n    # Only after a poll',
        '        if result:\n            platform.ack(item["update_id"])\n\n    # Only after a poll'),
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
        "            ring.notify(transport, surface, mail / \"inbox\", published[-1])",
        "            for _p in published:\n                ring.notify(transport, surface, mail / \"inbox\", _p)"),
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
    "persistence does not move back into ack": (
        TG, "tests.test_offset_state_machine",
        "        if self._acked is None or update_id > self._acked:\n            self._acked = update_id",
        "        if self._acked is None or update_id > self._acked:\n            self._acked = update_id\n            self._save_offset()"),
    "a failed cycle does not claim liveness": (
        BRIDGE, "tests.test_offset_state_machine",
        '    loop._write_heartbeat(root / "state" / "health.json")', "    pass"),
    "the mailbox never receives private state": (
        BRIDGE, "tests.test_mail_root",
        '        (mail_root / name).mkdir(exist_ok=True)\n    return mail_root',
        "        (mail_root / name).mkdir(exist_ok=True)\n    prepare_root(mail_root)\n    return mail_root"),
    "a missing mailbox is refused, not invented": (
        BRIDGE, "tests.test_mail_root",
        "    if not mail_root.is_dir():", "    if False:"),
    "integrated mode rings through the letterbox helper": (
        BRIDGE, "tests.test_mail_root",
        '            _bus_ring(recipient, "info", published[-1], binary=bus_binary)',
        '            ring.notify(transport, surface, mail / "inbox", published[-1])'),
    "standalone keeps its own knock and store": (
        BRIDGE, "tests.test_mail_root",
        "    integrated = mail_root is not None and pathlib.Path(mail_root) != root",
        "    integrated = True"),
    "the ring outcome is parsed, not assumed from the exit code": (
        BRIDGE, "tests.test_mail_root",
        '    if result.returncode != 0 or "doorbell submitted" not in output:',
        "    if result.returncode != 0:"),
    "the BINARY replies to letters where they live": (
        ROOT / "src" / "alb" / "cli.py", "tests.test_mail_root",
        '    mail = pathlib.Path(args.mail_root or config.get("ALB_MAIL_ROOT") or root)',
        "    mail = root"),
    "a rate limit is waited out, not died on": (
        TG, "tests.test_telegram_adapter",
        '            if exc.code == 429 or exc.code >= 500:\n'
        '                # The platform asking for time, or failing at its own gateway.\n'
        '                # Neither is a verdict about us and both clear on their own.\n'
        '                raise TransientFailure(f"getUpdates deferred: HTTP {exc.code}",\n'
        '                                       _retry_after(exc)) from None\n',
        ""),
    "a throttled send is not a refusal": (
        TG, "tests.test_telegram_adapter",
        '            if exc.code == 429:',
        "            if False:"),
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


def _git_status(root):
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root,
        capture_output=True, text=True)
    return result.stdout


def _purge_bytecode(root):
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
    for cache in pathlib.Path(root).rglob("__pycache__"):
        for f in cache.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            cache.rmdir()
        except OSError:
            pass


def _isolate():
    """Throwaway tree: git archive HEAD + overlay of working-tree src/tests/scripts.

    Never copies source .git (a linked worktree's .git is a pointer; git in the
    copy would mutate the real index). Mutations live only here, under /tmp,
    so SIGKILL cannot leave the source tree contaminated.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="alb-mut.", dir="/tmp"))
    source = str(SOURCE_ROOT.resolve())
    if str(tmp.resolve()).startswith(source + os.sep) or tmp.resolve() == SOURCE_ROOT.resolve():
        shutil.rmtree(tmp, ignore_errors=True)
        print("FAIL: mutation temp dir is inside the scanned tree", file=sys.stderr)
        sys.exit(1)
    archive = subprocess.run(
        ["git", "archive", "HEAD"], cwd=SOURCE_ROOT,
        capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive.stdout, check=True)
    for name in ("src", "tests", "scripts"):
        src, dst = SOURCE_ROOT / name, tmp / name
        if not src.is_dir():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return tmp


def _in_work(work, target):
    return work / target.relative_to(SOURCE_ROOT)


def _run(name, target, tests, old, new, failures, work):
    target = _in_work(work, target)
    original = target.read_text(encoding="utf-8")
    if old not in original:
        failures.append(f"{name}: mutation anchor no longer present")
        print(f"  FAIL {name} (anchor missing)")
        return
    try:
        _purge_bytecode(work)
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", tests],
            capture_output=True, text=True, cwd=work,
            env={**os.environ, "PYTHONPATH": str(work / "src"),
                 "PYTHONDONTWRITEBYTECODE": "1"},
        )
    finally:
        target.write_text(original, encoding="utf-8")
        _purge_bytecode(work)
    if result.returncode == 0:
        failures.append(f"{name}: DISABLED BUT NO TEST FAILED")
    print(f"  {'ok  ' if result.returncode else 'FAIL'} {name}")


def main():
    before = _git_status(SOURCE_ROOT)
    work = _isolate()
    failures = []
    src = _in_work(work, SRC)
    original = src.read_text(encoding="utf-8")
    try:
        for name, (target, tests, old, new) in EXTRA.items():
            _run(name, target, tests, old, new, failures, work)
        for name, (old, new) in MUTATIONS.items():
            if old not in original:
                failures.append(f"{name}: mutation anchor no longer present")
                continue
            src.write_text(original.replace(old, new, 1), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "tests.test_letter"],
                capture_output=True, text=True, cwd=work,
                env={**os.environ, "PYTHONPATH": str(work / "src"),
                     "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if result.returncode == 0:
                failures.append(f"{name}: DISABLED BUT NO TEST FAILED")
            print(f"  {'ok  ' if result.returncode else 'FAIL'} {name}")
    finally:
        src.write_text(original, encoding="utf-8")
        _purge_bytecode(work)
        shutil.rmtree(work, ignore_errors=True)

    after = _git_status(SOURCE_ROOT)
    if after != before:
        print("FAIL: source tree contaminated by the mutation gate", file=sys.stderr)
        print(after, file=sys.stderr)
        failures.append("source tree status changed")

    if failures:
        print("\nMUTATION GATE FAILED\n")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"\nmutation gate: {len(MUTATIONS) + len(EXTRA)} invariants pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
