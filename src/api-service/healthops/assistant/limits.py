"""Per-user request limiter for the assistant route.

A sliding one-minute window per authenticated user, held in process memory. One API process
serves the single-tester deployment, so an in-memory limiter is sufficient; a multi-process
deployment would need a shared store. The limiter runs after authentication, so anonymous
callers are rejected before they reach it, and before the model, so a burst never queues GPU
work.
"""

import threading
import time
from collections import deque
from uuid import UUID


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[UUID, deque[float]] = {}

    def allow(self, user_id: UUID) -> bool:
        """Records the request and returns False when the user exceeded the minute window."""
        if self.per_minute <= 0:
            return True
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(user_id, deque())
            while events and now - events[0] >= 60.0:
                events.popleft()
            if len(events) >= self.per_minute:
                return False
            events.append(now)
            return True

    def retry_after_seconds(self, user_id: UUID) -> int:
        with self._lock:
            events = self._events.get(user_id)
            if not events:
                return 1
            return max(1, int(60.0 - (self._clock() - events[0])) + 1)
