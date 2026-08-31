"""One platform double, shared by every layer that talks to a queue.

THIS FILE EXISTS BECAUSE IT WAS TWO FILES. The double was copy-pasted into the
poller tests and the bridge tests, and they drifted: one modelled ack() as
consuming while the real adapter only records. The suite stayed green while a
live bot proved the bridge consumed nothing. A lie in one copy is invisible
while the other copy is honest.

The contract modelled here, which is the platform's and not our wish:

    ack()     RECORDS a high-water mark locally. Consumes nothing.
    confirm() TRANSMITS it. This is what makes the platform forget.
    fetch()   returns everything above what has been CONFIRMED.

If this ever needs to differ per layer, that is a signal the layers disagree
about the platform - which is the bug, not the reason for a second copy.
"""
from alb.poller import loop


class FakePlatform:
    def __init__(self, updates=None, raise_conflict=False):
        self.updates = list(updates or [])
        self.raise_conflict = raise_conflict
        self.staged = None      # ack()ed, not yet transmitted
        self.confirmed = None   # what the platform has actually been told
        self.fetch_count = 0

    def fetch(self, offset=None):
        if self.raise_conflict:
            raise loop.PlatformConflict("another consumer holds this token")
        self.fetch_count += 1
        if self.confirmed is None:
            return list(self.updates)
        return [u for u in self.updates if u["update_id"] > self.confirmed]

    def ack(self, update_id):
        if self.staged is None or update_id > self.staged:
            self.staged = update_id

    def confirm(self):
        self.confirmed = self.staged

    def pending(self):
        return self.fetch(None)


def update(uid, chat, text):
    return {"update_id": uid, "chat_id": chat, "text": text}
