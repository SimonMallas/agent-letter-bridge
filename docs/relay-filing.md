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
unfiled: 3 (oldest: PT86400S)
```

The count includes only inbox letters addressed to the configured seat, including
in integrated mode; processed letters and other seats are excluded. Age uses
`sent:` with a timezone and is expressed as an ISO-8601 duration in seconds.
Missing/invalid times yield `oldest: unknown`, never an invented age. An empty
inbox yields `oldest: none`. Unsafe/unreadable config or malformed letters yield
`unfiled: unknown`, not a misleading zero. Status reads the private config for
mailbox/seat identity but does not send, poll, or print its credential.

## Denied mail

Verified in `src/alb/poller/loop.py`: the allowlist gate precedes media processing,
letter publication and message-index writes. A denied update creates no letter
in inbox, processed, or a quarantine mailbox. It increments an operator-facing
denied count and is acknowledged to advance the platform cursor. Consequently
there is no denied-mail folder to subtract from the unfiled count. Existing
`tests/test_poller.py` tests enforce denial silence, cursor progress and allowed
mail following denied updates.
