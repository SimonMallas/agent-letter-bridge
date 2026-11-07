# Filing standalone relay letters

`alb file <full-letter-id> --root <root> [--config <path>]`

This is local filing, not a reply or acknowledgement to the platform. It moves
exactly `<root>/inbox/<id>.md` to `<root>/processed/<id>.md`, without rewriting
the linked file's bytes.
It never constructs an extra agent mailbox under the state directory.
The default config is `<root>/bridge.env`. The root must be inside the current
HOME; root, config, inbox, source letter and processed directory must be owned
by the current OS user, owner-only, and not symlinks. This is an OS-user/HOME
boundary, not isolation between agents sharing the same user and HOME.

The source check and the later hardlink are not one snapshot: the command does
not bind the validated source inode to the link operation. A same-user process
can replace the source in that window, and the replacement may be filed instead
of the checked file. Such concurrent same-user replacement is outside this
boundary; byte preservation is not a guarantee that the filed inode is the one
whose envelope was validated.

Only a letter whose `to:` matches `ALB_TO` (default `agent`) can be filed.
Path-shaped IDs, mismatched envelope IDs and existing destinations are refused.
Publication into processed is exclusive, followed by removing the inbox name:
a crash between these steps may leave both names, but never overwrites a record.
Do not delete either copy blindly; investigate a destination-exists refusal.

For an integrated mailbox (`ALB_MAIL_ROOT` or `--mail-root` differs from root),
this command refuses and directs you to `letterbox file`. Use that mailbox's
normal Letterbox environment. There is only one filing path for each mode.
No registry auto-discovery is provided for filing; specify the root explicitly.

`alb --status --root <root> [--config <path>]` also reports:

```
unfiled: 3 in builder's inbox · oldest 2d · 1 with no sent time · next: letterbox check, then letterbox file <id> for each one handled
```

The count includes only inbox letters addressed to the configured seat
(`ALB_TO`), including in integrated mode; processed letters and other seats are
excluded. Age uses `sent:` with a timezone and reads in minutes, hours or days.
A letter with a missing or invalid time is counted as `with no sent time`,
never given an invented age, and the oldest age covers the letters that have
one. The next step names the filing path for the mode: `letterbox check` and
`letterbox file` for an integrated mailbox, `alb --list` and `alb file` for a
standalone root. An empty inbox prints only the count. Unsafe/unreadable config
or malformed letters yield `unfiled: unknown`, not a misleading zero. Status
reads the private config for mailbox/seat identity but does not send, poll, or
print its credential.

## Automatic close-out after a proven-sent reply

After a successful `alb --reply-to` (including an explicitly resumed throttled
reply), the CLI checks the durable receipt history and files the source. The
latest numbered receipt must record `sent` in both filename and JSON content;
the outbound letter must name the same source (`re`), seat (`from`), own reply
ID (`id`) and canonical platform reply route (`to`, e.g. `telegram-bridge`).
Producer and validator share that route definition. `ALB_FROM` customizes the
inbound sender label, not the outbound recipient; existing outbound envelopes
remain compatible and recipient binding is still enforced. Ambiguous, dead,
refused, deferred, missing or malformed receipts do not authorize filing.
Platform acceptance is not proof that the human read the reply.

This internal close-out also operates inside the configured integrated seat's
mailbox, inbox to processed. Manual `alb file` still refuses integrated mode:
`letterbox file` remains the only agent-facing manual filing path there. The
same envelope and no-clobber checks apply. For the configured integrated seat
only, mailbox/inbox/outbox/processed directories and letters must have our uid,
be real directories or regular files (no symlinks), and have no group/other
write bits (`mode & 0022` must be zero). Shared readability is accepted: 0755
directories and 0644 letters are valid. Standalone mailboxes, bridge root,
credentials/config, receipt state directories and receipt files remain strict
owner-only. Newly created receipt directories are 0700. At operational startup
(including a reply invocation), the bridge migrates only its own
`<root>/state/receipts` directory from exactly 0755 to 0700 and prints
`alb: migrated own state/receipts permissions 0755 -> 0700`. Already-0700
is untouched. Other modes on an accessible, current-user-owned real directory
skip migration with a notice: polling/reply continue, while auto-filing still
refuses nonprivate receipt state. For example, legacy 0750/0775 directories
are not upgrade startup blockers and are not silently chmodded. Wrong owners,
symlinks beneath the resolved root (at `state` or `state/receipts`),
non-directories and inaccessible paths still refuse startup, naming the exact
component in the error. The root itself may be an alias: operational startup
resolves it once and proceeds with that resolved path. The manual `alb file`
root restriction is unchanged. Absent receipts are not created by the migration. No recursive chmod:
existing child receipt directories/files and mailboxes are never changed by
this migration. Nonprivate child receipt directories still require an explicit
operator decision; this narrow migration does not silently repair them.

0755 permits other local accounts to list/traverse where ancestor access also
permits it; 0644 permits reading contents. These modes are not isolation among
agents sharing one uid. Paths must have owner-controlled ancestors and no ACL
grants permitting others to replace them; mode-bit checks do not audit ACLs.
If another local account is introduced, reconsider the read exposure/0700
policy. Never make a different user's mailbox writable to pass these checks.

The reply outcome is one stdout line containing `sent <reply-id>; alb: filed
<source-id>` on success, or `sent <reply-id>; source not auto-filed: <reason>`
on refusal, so the agent sees the close-out outcome with the send outcome.
The next mailbox scan sees a successfully filed letter in processed. Already
processed sources are validated and left alone.

If filing fails after sending, the CLI reports
`alb: sent <reply-id>; source not auto-filed: <reason>` and retains send success.
Never resend to repair a filing problem:
the outbound claim still prevents duplicate sends. Tidy using the normal manual
filing path. A crash between receipt persistence and close-out can leave the
source unfiled; this change does not sweep historical receipts at startup or
retroactively migrate the backlog. Direct Python send-library calls without
the CLI do not perform this owner/config-aware close-out.

## Denied mail

Verified in `src/alb/poller/loop.py`: the allowlist gate precedes media processing,
letter publication and message-index writes. A denied update creates no letter
in inbox, processed, or a quarantine mailbox. It increments an operator-facing
denied count and is acknowledged to advance the platform cursor. Consequently
there is no denied-mail folder to subtract from the unfiled count. Existing
`tests/test_poller.py` tests enforce denial silence, cursor progress and allowed
mail following denied updates.
