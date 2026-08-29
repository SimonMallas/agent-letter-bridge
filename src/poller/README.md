# poller

Untrusted. Fetches from the platform, writes the letter, then acks.

**Never:** ring, notify, or touch a terminal. Exposes no supported send code path.

**Stated limit:** the token is not credential-separated. This is code discipline,
not a capability boundary.
