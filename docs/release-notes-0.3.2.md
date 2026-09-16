# Agent Letter Bridge 0.3.2

A focused fix for the order of inbound notification and platform confirmation.

Once a new batch of letters is durable, the bridge attempts its bounded,
coalesced doorbell notification before confirming consumption to the platform.
Even if confirmation then fails, the saved mail has had its notification
attempt; subsequent deduplicated cycles do not attempt it again.

- Replaying the same updates does not publish duplicate letters or ring again.
- Notification failures remain recorded and do not prevent platform confirmation.
- The completed-cycle heartbeat is written only after confirmation succeeds.
- Standalone and Letterbox-integrated notification paths retain their existing
  routing and submission contracts. Core runtime dependencies remain empty.

The change does not implement a durable notification queue, repair historical
missed notifications, or guarantee recovery from a process crash between letter
publication and notification. A successful transport call is not proof that an
agent read or handled a letter.

The source fix was reviewed with isolated tests and mutation checks. Live
transport and seat rollout are separate operator steps, not evidence supplied
by those fake-transport tests.
