"""Slack imports the shared client from this path."""
from alb.adapters.websocket import (  # noqa: F401
    GUID,
    MAX_FRAGMENTS,
    MAX_FRAME,
    MAX_MESSAGE,
    WebSocket,
    WebSocketError,
    connect,
)
