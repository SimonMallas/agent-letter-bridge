# letter

Transport-neutral durable-letter contract. Atomic publish (temp + hardlink),
two-fence parsing, exact-id resolution.

New publications use UTC for both the ID stamp and an explicit `sent:` field,
from one clock snapshot. The publisher owns this field rather than trusting
caller-supplied metadata. It records local publication time, not remote delivery
or the original platform message's authoring time. Outbound reply composition
adds the same UTC field without changing its source-derived claim ID.
Reading or deduplicating older letters never adds or rewrites timestamps.

**Never:** resolve a path-shaped identifier. Never resolve an id that matches zero
or many letters.
