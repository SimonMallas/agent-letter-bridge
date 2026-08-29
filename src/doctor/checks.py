"""Local diagnostics. No token, no platform calls, no getUpdates - ever.

The doctor is a diagnostic, not a second consumer: a doctor that polls is the
very thing it exists to detect. That boundary is not a policy note here, it is
asserted by test, including that this package cannot even reach the network.

A getUpdates conflict probe is FORBIDDEN and uninterpretable: an "ok" may mean
it just terminated another consumer's in-flight request, and telling which side
of the conflict you were on requires repeating it - the loop the boundary
forbids.
"""

_TOKEN_HINTS = ("token", "secret", "api_key", "apikey")


def env_is_token_free(environ):
    """True if no credential-shaped variable is present in this process."""
    return not any(hint in key.lower() for key in environ for hint in _TOKEN_HINTS)


def webhook_check_command():
    """Return the command for the OPERATOR to run in their own shell.

    A webhook set on the bot conflicts with polling forever and is invisible
    locally, so it must be checked - but getWebhookInfo is read-only, consumes
    nothing and conflicts with nothing. The doctor prints it; it never runs it,
    because running it would require holding the token.

    If a webhook is set, the remedy is deleteWebhook or a token re-issue:
    polling cannot coexist with one.
    """
    return (
        "curl -s 'https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo'"
    )
