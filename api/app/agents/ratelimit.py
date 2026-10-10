"""A per-user sliding window counter, kept in this process (one api process on the Jetson).

Guards the credential routes: saving a credential and verifying one (each verification is an
outbound call to Anthropic). A refused call is not counted, so waiting out the window always
works. Thread safe: the handlers run on the threadpool.
"""

import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._lock = threading.Lock()
        self._calls: dict[uuid.UUID, deque[float]] = {}

    def take(self, user_id: uuid.UUID) -> int | None:
        """Count one call for this user; the whole seconds to wait instead when over the limit."""
        with self._lock:
            now = self.clock()
            calls = self._calls.setdefault(user_id, deque())
            while calls and calls[0] <= now - self.window_s:
                calls.popleft()
            if len(calls) >= self.limit:
                return max(1, math.ceil(calls[0] + self.window_s - now))
            calls.append(now)
            # Forget users idle for a whole window, so the map never grows without bound.
            for key in [k for k, v in self._calls.items() if not v or v[-1] <= now - self.window_s]:
                del self._calls[key]
            return None
