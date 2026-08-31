# letter

Transport-neutral durable-letter contract. Atomic publish (temp + hardlink),
two-fence parsing, exact-id resolution.

**Never:** resolve a path-shaped identifier. Never resolve an id that matches zero
or many letters.
