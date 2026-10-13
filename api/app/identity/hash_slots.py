"""A process wide bound on memory heavy work running at once (issue #108).

One argon2 password hash or verification holds 64 MiB for as long as it runs. The handlers that
hash are plain `def`, so they run on the threadpool (40 threads by default): without a bound, as
many hashes as there are threads run together, far past the api container's memory. `HashSlots`
is that bound, by construction:

- at most `slots` holders at any time, whoever asks;
- at most `max_waiting` callers wait for a slot, each for at most `max_wait_s` seconds. A caller
  beyond either bound gets `HashBusyError` at once or at the deadline, never a place in an
  unbounded queue. The api turns it into 503 with Retry-After;
- a slot is always given back: the hold is a `with` block, so an exception inside it, a
  password that does not verify, or a request whose client went away all release it. A handler
  on the threadpool is never interrupted halfway: when its client disconnects the work it began
  runs to its end and then frees the slot, so a slot is held for one hash at the longest.

It is a thread primitive on purpose (`threading`, not `asyncio`): the holders are threadpool
threads, and nothing here may be awaited. `hold()` is re-entrant per thread, so a route can take
its slot once, before it opens a database transaction, and the hashes inside reuse it: the wait
then happens with no database connection checked out, and one request never needs two slots.

Warning: the re-entrance is remembered per thread. Never use `hold()` from a dependency with
`yield` or from async code: enter and exit may run on different threads, so the exit would clear
the flag of a thread that holds nothing while the entering thread keeps a flag for a slot already
given back, and its next hash would run outside the bound. Use it only as a `with` block inside
a plain `def` handler or function, entered and left on one thread.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class HashBusyError(Exception):
    """No slot within the bounds. `retry_after` is in whole seconds, at least 1."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"busy, retry after {retry_after}s")
        self.retry_after = retry_after


class HashSlots:
    def __init__(
        self, slots: int, max_waiting: int, max_wait_s: float, retry_after_s: int = 1
    ) -> None:
        if slots < 1 or max_waiting < 0 or max_wait_s < 0 or retry_after_s < 1:
            raise ValueError("slots >= 1, max_waiting >= 0, max_wait_s >= 0, retry_after_s >= 1")
        self.slots = slots
        self.max_waiting = max_waiting
        self.max_wait_s = max_wait_s
        self.retry_after_s = retry_after_s
        self._free = threading.Semaphore(slots)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._waiting = 0
        self._held = 0
        self._peak = 0
        self._refused = 0

    @property
    def held(self) -> int:
        """Slots held right now."""
        return self._held

    @property
    def waiting(self) -> int:
        """Callers waiting for a slot right now."""
        return self._waiting

    @property
    def peak(self) -> int:
        """The most slots ever held at once since the last `reset_counters`."""
        return self._peak

    @property
    def refused(self) -> int:
        """Callers refused (HashBusyError) since the last `reset_counters`."""
        return self._refused

    def reset_counters(self) -> None:
        with self._lock:
            self._peak = self._held
            self._refused = 0

    def _refuse(self) -> HashBusyError:
        # Called with the lock held.
        self._refused += 1
        return HashBusyError(self.retry_after_s)

    def _acquire(self) -> None:
        if self._free.acquire(blocking=False):
            return
        with self._lock:
            if self._waiting >= self.max_waiting:
                raise self._refuse()
            self._waiting += 1
        try:
            acquired = self._free.acquire(timeout=self.max_wait_s)
        finally:
            with self._lock:
                self._waiting -= 1
        if not acquired:
            with self._lock:
                raise self._refuse()

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Hold one slot for the block. Raises HashBusyError when none comes free in time.
        Inside a block of the same thread it takes nothing more. Only as a `with` block in a
        plain `def`, never from a dependency with `yield` or from async code: enter and exit
        may then run on different threads (see the module docstring)."""
        if getattr(self._local, "holding", False):
            yield
            return
        self._acquire()
        self._local.holding = True
        with self._lock:
            self._held += 1
            self._peak = max(self._peak, self._held)
        try:
            yield
        finally:
            self._local.holding = False
            with self._lock:
                self._held -= 1
            self._free.release()
