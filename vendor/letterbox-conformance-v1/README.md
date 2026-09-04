# Letterbox envelope conformance fixtures

Version: 1

This directory is the canonical, transport-neutral fixture set. Consumers vendor
the bytes together with `SHA256SUMS`; ordinary CI must not fetch them.

`accepted/` contains envelopes that must parse. `rejected/` contains malformed
envelopes that must not resolve as letters. Fence recognition is byte-exact: the
opening and closing lines are exactly `---`; trailing whitespace is not allowed.
Keys without a value are valid empty values. UTF-8 body and metadata bytes are
preserved. Body keys after the second fence are never metadata.

Dynamic emission is compared semantically: required fields, body, and linkage are
checked after ignoring the generated timestamp/random suffix. This shell helper
does not expose injectable clock/randomness, so this version intentionally makes
no byte-equality claim for emitted letters. A byte-exact emission fixture requires
an explicitly reviewed injection seam in a later change.

Path-shaped command arguments are identifiers, not filesystem authority, and must
be refused. These fixtures do not define new envelope fields or runtime semantics.

