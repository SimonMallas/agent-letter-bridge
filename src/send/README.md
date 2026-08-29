# send

Bounded outbound. Replies only to the originating chat of a stored inbound
letter. Claims before sending (O_EXCL ledger).

**Never:** originate contact. Never send on an allowlist miss. Never auto-retry
an ambiguous outcome.
