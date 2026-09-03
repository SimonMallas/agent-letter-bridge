# Retention

Written for 3am. This page is what **durable** means here, and what it
does not. It is not a memory product, a backup product, or a promise
about anyone else's copies.

Installing? Use [`../INSTALL.md`](../INSTALL.md).
Something is on fire? Use [`operations.md`](operations.md).

---

## What durable means

A letter on disk in the mailbox (inbound `inbox/`, outbound `outbox/`
when that exists) is the record. If the process dies, the multiplexer
restarts, or the doorbell never fires, **the file is still there**. That
is the whole claim.

Receipts and claim ledgers under `--root` are the private account of
what this bridge attempted. They are not the letter. They do not travel
with mail.

`alb export` (v0.2, lands with the retrieval CLI) is how you take a
copy **you** can keep. It does not change the live store.

---

## What durable does not mean

- **Not kept forever.** Durable is "survives a crash tonight," not
  "we are your archive policy." If you need a retention period, you
  enforce it. This tool will not silently expire letters in v0.2.
- **Not a librarian.** No promotion, summaries, embeddings, or search
  ranking. `alb list` / `show` / `search` / `thread` (when shipped) scan
  files. That is a filing cabinet.
- **Not the chat app's history.** Telegram (or any platform) keeps
  whatever it keeps. We cannot delete, export, or prove their copy.
  A letter here and a bubble on your phone are two records of one
  exchange. Losing one does not lose the other; deleting one does not
  delete the other.
- **Not your backups.** Time Machine, snapshots, disk images, and
  copies of the state directory are outside this process. If those
  exist, they may still hold letters after you think they are gone.
- **Not human receipt.** Platform acceptance (`sent`) means the API
  took the message. It does not mean anyone read it. Ambiguous means
  we do not know; do not retry.
- **Not encryption we invented.** At rest, use the disk's encryption.
  We do not roll our own.

---

## What `alb export` gives you (v0.2 contract)

Non-destructive. It does not delete, rewrite, or tombstone anything.

```sh
alb export --root /path/to/state [--mail-root /path/to/mailbox] thread <id>
alb export --root /path/to/state [--mail-root /path/to/mailbox] origin <key>
```

The archive is a tar of:

- the letters in that thread or origin (inbound and outbound), and
- the receipt logs that belong to those letters.

It does **not** include: bot token, `bridge.env`, allowlist, offset,
locks, canary fixtures, or the claim ledger. Those are private state.
If you need a machine move, copy the whole `--root` `state/` directory
yourself — see Moving machines in [`operations.md`](operations.md).
`state/delivered.json` is the difference between a clean move and a
replay of recent history.

If the command is not on your binary yet, this page is still the
contract the implementation must match.

---

## What nobody can promise

| Claim | Truth |
| --- | --- |
| "It's off my phone." | Only the platform can tell you that. We never could. |
| "It's off this disk." | Only if you wiped the mailbox, `--root`, **and** every backup you took. v0.2 has no `forget`. |
| "Export is the complete conversation." | It is the letters we stored plus our receipts. Not drafts you typed and never sent. Not messages the allowlist denied (those were consumed without a letter — silence is the deny path). |
| "A canary letter is mail." | It lives under `state/canary/`. Do not export it as correspondence. |

Denied senders produce no letter. There is nothing to retain, export, or
forget for them. That is the security property, not a gap in the archive.

---

## `alb forget` is not in v0.2

Deletion is 0.3, and only with a written threat model:

- **Do not delete the dedup ledger and call it forgotten.** A rewound
  platform offset will deliver the same updates again. Without ledger
  evidence they become **new letters**. That is resurrection, not
  erasure.
- 0.3 must tombstone, require an explicit confirm flag, and **print
  exactly what will die** before it dies.
- Even then: the chat app keeps its copy; backups keep theirs.

Until that exists, the honest operators' tools are export, then
whatever you do to the files with your own hands — knowing a replay
is possible if you also throw away `delivered.json`.

---

## 3am

1. Is the letter on disk? Then the crash did not lose it.
2. Need a copy? `alb export` (or copy the mailbox files). Leave the
   live store alone unless you mean to.
3. Want it gone tonight? You can delete files. You cannot make
   Telegram forget, and you cannot make last week's backup forget.
   If you delete `delivered.json`, yesterday's messages can come
   back as new mail. Don't.
