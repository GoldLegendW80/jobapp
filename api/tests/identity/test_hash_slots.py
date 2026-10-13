"""The bound on password hashes running at once (issue #108): `HashSlots` on its own, then the
three hashing functions of `security` through it."""

import asyncio
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.identity import security
from app.identity.hash_slots import HashBusyError, HashSlots


class Gauge:
    """Counts the threads inside a section at once, from inside the section."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inside = 0
        self.peak = 0
        self.entered = 0

    def __enter__(self) -> None:
        with self._lock:
            self.inside += 1
            self.entered += 1
            self.peak = max(self.peak, self.inside)

    def __exit__(self, *_: object) -> None:
        with self._lock:
            self.inside -= 1


def settle(slots: HashSlots, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while slots.held or slots.waiting:
        assert time.monotonic() < deadline, f"held {slots.held}, waiting {slots.waiting}"
        time.sleep(0.005)


def test_200_concurrent_calls_never_pass_the_slot_count() -> None:
    slots = HashSlots(slots=3, max_waiting=200, max_wait_s=30)
    gauge = Gauge()
    barrier = threading.Barrier(200)

    def work(_: int) -> None:
        barrier.wait(timeout=30)
        with slots.hold(), gauge:
            time.sleep(0.002)

    with ThreadPoolExecutor(max_workers=200) as pool:
        list(pool.map(work, range(200)))

    assert gauge.entered == 200
    assert gauge.peak == 3  # reached, never passed
    assert slots.peak == 3
    assert (slots.held, slots.waiting, slots.refused) == (0, 0, 0)


def test_the_queue_is_bounded_in_length() -> None:
    # 1 slot, 2 places to wait: of 10 callers at once, 3 are served and 7 are refused at once.
    slots = HashSlots(slots=1, max_waiting=2, max_wait_s=30, retry_after_s=4)
    release = threading.Event()
    outcomes: list[str] = []
    refusal_times: list[float] = []
    lock = threading.Lock()

    def work() -> None:
        started = time.monotonic()
        try:
            with slots.hold():
                release.wait(timeout=30)
            outcome = "served"
        except HashBusyError as exc:
            outcome = f"refused, retry after {exc.retry_after}"
            with lock:
                refusal_times.append(time.monotonic() - started)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=work) for _ in range(10)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    while len(refusal_times) < 7:
        assert time.monotonic() < deadline, outcomes
        time.sleep(0.005)
    assert (slots.held, slots.waiting) == (1, 2)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["refused, retry after 4"] * 7 + ["served"] * 3
    assert max(refusal_times) < 1.0  # no wait at all, far below max_wait_s
    assert slots.refused == 7
    assert (slots.held, slots.waiting) == (0, 0)


def test_the_wait_is_bounded_in_time() -> None:
    slots = HashSlots(slots=1, max_waiting=5, max_wait_s=0.2)
    release = threading.Event()
    holder = threading.Thread(target=lambda: _hold_until(slots, release))
    holder.start()
    while not slots.held:
        time.sleep(0.001)

    started = time.monotonic()
    with pytest.raises(HashBusyError), slots.hold():
        pytest.fail("the slot was taken twice")
    waited = time.monotonic() - started

    assert 0.19 <= waited < 1.5
    assert slots.waiting == 0
    release.set()
    holder.join(timeout=10)
    settle(slots)
    with slots.hold():  # and it is free again
        assert slots.held == 1


def _hold_until(slots: HashSlots, release: threading.Event) -> None:
    with slots.hold():
        release.wait(timeout=30)


def test_a_waiter_is_served_when_a_slot_comes_free_in_time() -> None:
    slots = HashSlots(slots=1, max_waiting=1, max_wait_s=10)
    release = threading.Event()
    holder = threading.Thread(target=lambda: _hold_until(slots, release))
    holder.start()
    while not slots.held:
        time.sleep(0.001)
    threading.Timer(0.1, release.set).start()

    started = time.monotonic()
    with slots.hold():
        waited = time.monotonic() - started
    holder.join(timeout=10)
    assert 0.05 <= waited < 5


def test_an_exception_inside_releases_the_slot() -> None:
    slots = HashSlots(slots=1, max_waiting=0, max_wait_s=0)

    for error in (RuntimeError("boom"), KeyboardInterrupt()):
        with pytest.raises(type(error)), slots.hold():
            raise error
        assert slots.held == 0

    with slots.hold():  # max_waiting 0: this would be refused if a slot had leaked
        assert slots.held == 1


def test_a_cancelled_request_releases_its_slot() -> None:
    # What a client that goes away looks like: the task awaiting the threadpool is cancelled.
    # The thread is not interrupted; it ends its work and gives the slot back.
    slots = HashSlots(slots=1, max_waiting=1, max_wait_s=0.3)
    gauge = Gauge()

    def work() -> None:
        with slots.hold(), gauge:
            time.sleep(0.15)

    async def scenario() -> None:
        running = asyncio.create_task(asyncio.to_thread(work))
        waiting = asyncio.create_task(asyncio.to_thread(work))
        await asyncio.sleep(0.05)
        assert (slots.held, slots.waiting) == (1, 1)
        running.cancel()
        waiting.cancel()
        results = await asyncio.gather(running, waiting, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)

    asyncio.run(scenario())
    settle(slots)
    assert gauge.peak == 1
    with slots.hold():
        assert slots.held == 1


def test_hold_is_reentrant_in_one_thread_only() -> None:
    slots = HashSlots(slots=1, max_waiting=0, max_wait_s=0)
    refused: list[bool] = []

    def other_thread() -> None:
        try:
            with slots.hold():
                refused.append(False)
        except HashBusyError:
            refused.append(True)

    with slots.hold():
        with slots.hold(), slots.hold():  # one request, several hashes: still one slot
            assert slots.held == 1
        assert slots.held == 1  # leaving an inner block gives nothing back
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=10)
    assert refused == [True]
    assert slots.held == 0
    with slots.hold():
        assert slots.peak == 1


def test_reset_counters() -> None:
    slots = HashSlots(slots=2, max_waiting=0, max_wait_s=0)
    with slots.hold():
        pass
    assert slots.peak == 1
    slots.reset_counters()
    assert (slots.peak, slots.refused) == (0, 0)


@pytest.mark.parametrize("args", [(0, 1, 1.0), (1, -1, 1.0), (1, 1, -0.1)])
def test_bounds_are_validated(args: tuple[int, int, float]) -> None:
    with pytest.raises(ValueError, match="slots"):
        HashSlots(*args)


# --- the hashing functions of `security` -------------------------------------------------------


class SlowHasher:
    """Stands for argon2: counts the computations running at once, from inside each one."""

    def __init__(self, seconds: float = 0.003) -> None:
        self.gauge = Gauge()
        self.seconds = seconds

    def hash(self, password: str | bytes) -> str:
        with self.gauge:
            time.sleep(self.seconds)
        return "$slow$hash"

    def verify(self, password_hash: str, password: str | bytes) -> bool:
        with self.gauge:
            time.sleep(self.seconds)
        return True

    def check_needs_rehash(self, password_hash: str) -> bool:
        return False


def test_every_hashing_function_runs_inside_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = SlowHasher()
    slots = HashSlots(slots=2, max_waiting=200, max_wait_s=30)
    monkeypatch.setattr(security, "_hasher", hasher)
    monkeypatch.setattr(security, "password_slots", slots)
    security._dummy_hash.cache_clear()
    security.hash_dummy("the dummy hash is made on first use: here, before the count")
    assert hasher.gauge.entered == 2
    barrier = threading.Barrier(200)

    def work(i: int) -> None:
        barrier.wait(timeout=30)
        if i % 3 == 0:
            security.hash_password("correct horse battery 42")
        elif i % 3 == 1:
            assert security.verify_password("$slow$hash", "correct horse battery 42")
        else:
            security.hash_dummy("anything")

    with ThreadPoolExecutor(max_workers=200) as pool:
        list(pool.map(work, range(200)))

    assert hasher.gauge.entered == 2 + 200
    assert hasher.gauge.peak == 2
    assert slots.peak == 2
    assert (slots.held, slots.waiting, slots.refused) == (0, 0, 0)


@pytest.mark.parametrize("call", ["hash", "verify", "dummy", "unencodable"])
def test_a_hashing_function_without_a_slot_raises_busy(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    hasher = SlowHasher()
    slots = HashSlots(slots=1, max_waiting=0, max_wait_s=0, retry_after_s=2)
    monkeypatch.setattr(security, "_hasher", hasher)
    monkeypatch.setattr(security, "password_slots", slots)
    release = threading.Event()
    holder = threading.Thread(target=lambda: _hold_until(slots, release))
    holder.start()
    while not slots.held:
        time.sleep(0.001)

    calls = {
        "hash": lambda: security.hash_password("correct horse battery 42"),
        "verify": lambda: security.verify_password("$slow$hash", "correct horse battery 42"),
        "dummy": lambda: security.hash_dummy("anything"),
        "unencodable": lambda: security.verify_password("$slow$hash", "lone surrogate \ud800"),
    }
    with pytest.raises(HashBusyError) as caught:
        calls[call]()
    release.set()
    holder.join(timeout=10)

    assert caught.value.retry_after == 2
    assert hasher.gauge.entered == 0  # refused before any computation


def test_the_production_bounds_fit_the_container() -> None:
    assert security.PASSWORD_HASH_MEMORY_MIB == 64
    slots = security.password_slots
    assert (slots.slots, slots.max_waiting) == (2, 16)
    assert (slots.max_wait_s, slots.retry_after_s) == (3.0, 2)
    # Holders and waiters together stay well inside the 40 threads of the threadpool.
    assert slots.slots + slots.max_waiting <= 20

    compose = Path(__file__).resolve().parents[3] / "compose.yml"
    if not compose.exists():  # the api image carries no compose.yml
        pytest.skip("compose.yml is not next to the api folder")
    block = re.search(r"^  api:\n(?:    .*\n|\n)+", compose.read_text(), re.MULTILINE)
    assert block is not None
    limit = re.search(r"^    mem_limit: (\d+)m$", block.group(0), re.MULTILINE)
    assert limit is not None
    # 173 MiB is the api's measured peak before any sign in (issue #108); what is left after
    # the hashes must still be at least 128 MiB for request bodies in flight.
    assert int(limit.group(1)) - 173 - slots.slots * security.PASSWORD_HASH_MEMORY_MIB >= 128


def test_the_api_stays_one_uvicorn_process() -> None:
    # The slots are per process (security.py): a second uvicorn worker would bring two more, and
    # four hashes of 64 MiB no longer fit the container. uvicorn starts workers on --workers, or
    # on WEB_CONCURRENCY in its environment, so neither may appear where the api is started.
    api_dir = Path(__file__).resolve().parents[2]
    dockerfile = api_dir / "Dockerfile"
    if not dockerfile.exists():  # the api image carries no Dockerfile
        pytest.skip("api/Dockerfile is not next to the tests")
    commands = [
        json.loads(line.removeprefix("CMD "))
        for line in dockerfile.read_text().splitlines()
        if line.startswith('CMD ["uvicorn"')
    ]
    assert commands, "no uvicorn CMD found in api/Dockerfile"
    for command in commands:
        assert not any(arg.startswith("--workers") for arg in command), command

    # compose.yml and its overrides may replace the command or set the environment.
    for path in [dockerfile, *sorted(api_dir.parent.glob("compose*.yml"))]:
        settings = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        assert "--workers" not in settings, path.name
        assert "WEB_CONCURRENCY" not in settings, path.name
