# Agent Letter Bridge 0.3.1 — draft

Not published.

Phone-path traps from the live 0.3.0 seat:

- Message-id index writes are flocked; unique temp names so two writers cannot drop an entry.
- `--check` and `--status` show the last doorbell-ring outcome and its age from `ring-health.json`, even when nothing inbound has arrived.
- Grants validate binding identity, not POLICY equality. Optional per-grant `overrides` are operator-set in the 0700 grants directory / 0600 grant files and may exceed POLICY; that directory is the trust boundary. Existing grants stay valid when a POLICY limit changes.
- `--reply-to --photo` with the same allowlisted-file + preflight rules as `--send --photo`. Still one reply per inbound letter. `attach-roots.json` is the trust boundary: a hard link whose directory entry sits inside an attach root is sendable; listing `/` allows everything.
- `--init --token-file PATH`: read a mode-600 file, delete it, never echo the token. Empty, whitespace-only, or multi-line files are refused and kept. Wizard otherwise unchanged.

Docs: `pipx install agent-letter-bridge` is the new-user line; checkout install is the developer path. On a ring, sweep every unfiled telegram-bridge letter oldest first (the ring names the newest). Vocabulary: the doorbell rings.

`--status` does not yet report unanswered inbound letters.
