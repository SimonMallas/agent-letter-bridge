# doctor

Local diagnostics: single-consumer conflict probe and daemon-context checks.

**Never:** hold a token. Never make a platform call. Never call getUpdates — a
conflict probe is forbidden and uninterpretable.

Prints the read-only getWebhookInfo command for the operator to run themselves.
