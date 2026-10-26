"""The memory guard's deciding inputs, without Chromium or a native container."""

import asyncio
import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import chromium  # noqa: E402

LIMIT = 1 << 30


def cgroup_reads(monkeypatch, files):
    """Each file is consumed once, so a later diagnostic reread cannot pass."""
    reads = []

    def opened(path):
        name = path.removeprefix("/synthetic-cgroup/")
        reads.append(name)
        value = files.pop(name, FileNotFoundError())
        if isinstance(value, OSError):
            raise value
        return io.StringIO(value)

    monkeypatch.setattr(chromium, "open", opened, raising=False)
    return reads


def records(caplog):
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "browserd" and record.getMessage().startswith('{"event":"browser_memory"')
    ]


def browser():
    pages = []

    class Page:
        async def close(self):
            pages.remove(self)

    pages.extend([Page(), Page()])
    return SimpleNamespace(contexts=[SimpleNamespace(pages=pages)])


def test_guard_diagnostic_uses_the_deciding_reads_and_preserves_the_event(monkeypatch, caplog):
    current, inactive, active = 1054867456, 1048576, 700 << 20
    reads = cgroup_reads(
        monkeypatch,
        {
            "memory.current": f"{current}\n",
            "memory.max": f"{LIMIT}\n",
            "memory.stat": (
                f"inactive_file {inactive}\nactive_file {active}\n"
                "anon 280000000\nfile 760000000\nshmem 12000000\nkernel 15000000\n"
                "slab 3000000\nfile_mapped 220000000\nunknown_counter 999999\n"
            ),
        },
    )
    caplog.set_level(logging.DEBUG, logger="browserd")
    events = []
    live = browser()
    oldest = live.contexts[0].pages[0]
    assert asyncio.run(
        chromium.guard_memory(
            live, 90, lambda **e: events.append(e), lambda: chromium.memory("/synthetic-cgroup")
        )
    )
    assert live.contexts[0].pages == [oldest]
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1005}]
    assert reads == ["memory.current", "memory.max", "memory.stat"]
    samples = records(caplog)
    assert samples, "the actual deciding cgroup reads must produce a numeric diagnostic"
    sample = next(s for s in samples if s["phase"] == "guard_close")
    assert sample["current_bytes"] == current
    assert sample["limit_bytes"] == LIMIT
    assert sample["working_set_bytes"] == current - inactive
    assert sample["cgroup_version"] == 2
    assert sample["accounting_status"] == "ok"
    assert sample["stat_status"] == "ok"
    assert sample["pct"] == 90
    assert sample["sample_id"] == sample["read_started_ns"]
    assert sample["read_started_ns"] <= sample["read_finished_ns"]
    assert sample["counters"] == {
        "inactive_file": inactive,
        "active_file": active,
        "anon": 280000000,
        "file": 760000000,
        "shmem": 12000000,
        "kernel": 15000000,
        "slab": 3000000,
        "file_mapped": 220000000,
    }
    assert samples[0]["sample_id"] == sample["sample_id"]


@pytest.mark.parametrize(
    ("stat", "status", "inactive"),
    [
        ("inactive_file 100\nactive_file 900\n", "ok", 100),
        ("active_file 900\n", "missing", 0),
        ("", "missing", 0),
        ("inactive_file x\n", "invalid", 0),
        ("inactive_file -5\n", "invalid", 0),
        ("inactive_file_x 100\n", "missing", 0),
        ("inactive_file x\ninactive_file 100\ninactive_file 200\n", "ok", 100),
        ("inactive_file\t100\n", "missing", 0),
        ("inactive_file \t100\n", "ok", 100),
        ("inactive_file  100\n", "ok", 100),
        ("inactive_file ١٠٠\n", "ok", 100),
        ("inactive_file 2000\n", "ok", 2000),
        (FileNotFoundError(), "unreadable", 0),
        (PermissionError(), "unreadable", 0),
    ],
)
def test_stat_fallback_is_conservative_and_diagnosed(monkeypatch, caplog, stat, status, inactive):
    caplog.set_level(logging.DEBUG, logger="browserd")
    cgroup_reads(monkeypatch, {"memory.current": "1000", "memory.max": "1000", "memory.stat": stat})
    assert chromium.memory("/synthetic-cgroup") == (max(0, 1000 - inactive), 1000)
    sample = records(caplog)[0]
    assert sample["stat_status"] == status
    assert sample["current_bytes"] == 1000
    assert sample["working_set_bytes"] == max(0, 1000 - inactive)
    assert sample["observer_failed"] == 0


def test_v1_uses_total_inactive_file_and_reports_the_original_fallback(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="browserd")
    reads = cgroup_reads(
        monkeypatch,
        {
            "memory/memory.usage_in_bytes": "1000",
            "memory/memory.limit_in_bytes": "2000",
            "memory/memory.stat": (
                "inactive_file 999\ntotal_inactive_file 100\ntotal_active_file 700\n"
                "total_rss 150\ntotal_cache 800\ntotal_shmem 20\ntotal_mapped_file 500\n"
            ),
        },
    )
    assert chromium.memory("/synthetic-cgroup") == (900, 2000)
    assert reads == [
        "memory.current",
        "memory/memory.usage_in_bytes",
        "memory/memory.limit_in_bytes",
        "memory/memory.stat",
    ]
    sample = records(caplog)[0]
    assert sample["cgroup_version"] == 1
    assert sample["read_failures"] == 1
    assert sample["stat_status"] == "ok"
    assert sample["counters"]["total_inactive_file"] == 100
    assert sample["counters"]["total_active_file"] == 700
    assert sample["counters"]["total_rss"] == 150
    assert sample["counters"]["total_cache"] == 800


@pytest.mark.parametrize(
    ("files", "status", "version", "expected"),
    [
        ({"memory.current": "1000", "memory.max": "max"}, "unlimited", 2, None),
        ({"memory.current": "x", "memory.max": "1000"}, "invalid", 2, None),
        ({"memory.current": "", "memory.max": "1000"}, "invalid", 2, None),
        ({}, "unavailable", 0, None),
        ({"memory.current": "1000"}, "unavailable", 0, None),
        # The original numeric v1 unlimited sentinel is still returned as a numeric limit.
        (
            {
                "memory/memory.usage_in_bytes": "1000",
                "memory/memory.limit_in_bytes": str(2**63 - 4096),
            },
            "ok",
            1,
            (1000, 2**63 - 4096),
        ),
    ],
)
def test_accounting_status_does_not_change_the_tuple_api(
    monkeypatch, caplog, files, status, version, expected
):
    caplog.set_level(logging.DEBUG, logger="browserd")
    reads = cgroup_reads(monkeypatch, files)
    assert chromium.memory("/synthetic-cgroup") == expected
    sample = records(caplog)[0]
    assert sample["accounting_status"] == status
    assert sample["cgroup_version"] == version
    if version == 2:
        assert reads == ["memory.current", "memory.max"]  # no v1 fallback after a parsed v2 read


@pytest.mark.parametrize("failure", ["counters", "debug_logger", "all_logging", "observer"])
def test_observer_failures_do_not_change_the_guard(monkeypatch, caplog, failure):
    caplog.set_level(logging.DEBUG, logger="browserd")
    cgroup_reads(
        monkeypatch,
        {
            "memory.current": str(LIMIT),
            "memory.max": str(LIMIT),
            "memory.stat": "inactive_file 0\n",
        },
    )

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic observer fault")

    if failure == "counters":
        monkeypatch.setattr(chromium, "_memory_counters", broken)
    elif failure == "observer":
        monkeypatch.setattr(chromium, "_observe_memory", broken)
    else:
        original = chromium.log.log

        def logging_fault(level, *args, **kwargs):
            if failure == "all_logging" or level == logging.DEBUG:
                broken()
            original(level, *args, **kwargs)

        monkeypatch.setattr(chromium.log, "log", logging_fault)
    events = []
    live = browser()
    assert asyncio.run(
        chromium.guard_memory(
            live, 90, lambda **e: events.append(e), lambda: chromium.memory("/synthetic-cgroup")
        )
    )
    assert len(live.contexts[0].pages) == 1
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1024}]
    if failure in {"counters", "debug_logger"}:
        assert records(caplog)[-1]["observer_failed"] == 1


def test_warning_is_visible_at_default_info_and_never_reuses_a_sample(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="browserd")
    cgroup_reads(
        monkeypatch,
        {
            "memory.current": str(LIMIT),
            "memory.max": str(LIMIT),
            "memory.stat": "inactive_file 0\n",
        },
    )
    live = browser()
    assert asyncio.run(
        chromium.guard_memory(
            live, 90, lambda **e: None, lambda: chromium.memory("/synthetic-cgroup")
        )
    )
    assert len(records(caplog)) == 1
    assert records(caplog)[0]["phase"] == "guard_close"
    assert caplog.records[-1].levelno == logging.WARNING
    # A synthetic reader with no underlying cgroup reads must not inherit that attribution.
    assert asyncio.run(
        chromium.guard_memory(browser(), 90, lambda **e: None, lambda: (LIMIT, LIMIT))
    )
    assert len(records(caplog)) == 1


def test_diagnostics_only_export_bounded_allowlisted_numbers(monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG, logger="browserd")
    marker = "canary_91a2f0d48e654c5c"
    cgroup_reads(
        monkeypatch,
        {
            "memory.current": str(LIMIT),
            "memory.max": str(LIMIT),
            "memory.stat": f"inactive_file 0\nactive_file 9\n{marker} 123\nfile_mapped {marker}\n"
            + "anon "
            + "9" * 20000,
        },
    )
    assert chromium.memory("/synthetic-cgroup") == (LIMIT, LIMIT)
    sample = records(caplog)[0]
    assert sample["counters"] == {"inactive_file": 0, "active_file": 9}
    assert sample["stat_truncated"] == 1
    assert marker not in caplog.text
    assert marker not in str(capsys.readouterr())
    assert "/synthetic-cgroup" not in caplog.text
    assert len(json.dumps(sample)) < 2048


def test_guard_keeps_its_sample_when_another_task_observes_during_close(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="browserd")
    ticks = iter((100, 110, 200, 210))
    monkeypatch.setattr(chromium.time, "monotonic_ns", lambda: next(ticks))
    files = {
        "memory.current": str(LIMIT),
        "memory.max": str(LIMIT),
        "memory.stat": "inactive_file 0\n",
    }
    cgroup_reads(monkeypatch, files)
    live = browser()
    newest = live.contexts[0].pages[-1]
    close = newest.close

    async def observe_other_task():
        files.update(
            {"memory.current": "100", "memory.max": "200", "memory.stat": "inactive_file 50\n"}
        )
        assert chromium.memory("/synthetic-cgroup") == (50, 200)

    async def interleaved_close():
        await asyncio.create_task(observe_other_task())
        await close()

    newest.close = interleaved_close
    events = []
    assert asyncio.run(
        chromium.guard_memory(
            live, 90, lambda **e: events.append(e), lambda: chromium.memory("/synthetic-cgroup")
        )
    )
    warning = records(caplog)[-1]
    assert warning["phase"] == "guard_close"
    assert warning["sample_id"] == 100
    assert warning["read_finished_ns"] == 110
    assert warning["current_bytes"] == warning["working_set_bytes"] == LIMIT
    assert events == [{"event": "tab_closed", "reason": "memory", "used_mib": 1024}]
    assert records(caplog)[1]["sample_id"] == 200


def test_existing_lifecycle_hooks_emit_numeric_boundaries_only_when_debug_enabled(
    monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG, logger="browserd")
    files = {"memory.current": "100", "memory.max": "200", "memory.stat": "inactive_file 10\n"}
    # Lifecycle samples use the ordinary memory() reader, on a synthetic accounting root.
    cgroup_reads(monkeypatch, files)
    memory = chromium.memory

    def read_memory():
        files.update(
            {"memory.current": "100", "memory.max": "200", "memory.stat": "inactive_file 10\n"}
        )
        return memory("/synthetic-cgroup")

    monkeypatch.setattr(chromium, "memory", read_memory)
    callbacks = {}
    page = SimpleNamespace(on=lambda event, callback: callbacks.update({event: callback}))
    context = SimpleNamespace(pages=[page], on=lambda *args: None)

    async def no_op(*args, **kwargs):
        return None

    async def ready(*args):
        assert args[1] == chromium.c.START_S
        return True

    connected = SimpleNamespace(contexts=[context], new_browser_cdp_session=no_op, close=no_op)

    async def connect(*args, **kwargs):
        assert kwargs == {"timeout": chromium.c.START_S * 1000}
        return connected

    runtime = chromium.Chromium(
        SimpleNamespace(uid=20001),
        "synthetic",
        SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect)),
        SimpleNamespace(),
    )
    for name in ("prepare", "spawn", "_watch_exit", "_watch"):
        monkeypatch.setattr(runtime, name, no_op)
    for name in ("_write_state", "_clear_state"):
        monkeypatch.setattr(runtime, name, lambda: None)
    monkeypatch.setattr(chromium, "cdp_ready", ready)
    monkeypatch.setattr(chromium, "free_port", lambda: 9333)
    monkeypatch.setattr(chromium, "reap_strays", no_op)
    monkeypatch.setattr(chromium, "as_uid", no_op)

    async def lifecycle():
        await runtime.start()
        assert "framenavigated" in callbacks
        callbacks["framenavigated"](SimpleNamespace(parent_frame=object()))
        callbacks["framenavigated"](SimpleNamespace(parent_frame=None))
        callbacks["framenavigated"](SimpleNamespace(parent_frame=None))
        await runtime._finish()
        await asyncio.gather(*runtime._tasks, return_exceptions=True)

    asyncio.run(lifecycle())
    samples = records(caplog)
    assert [sample["phase"] for sample in samples] == [
        "startup",
        "cdp_ready",
        "first_navigation",
        "end",
    ]
    assert all(
        sample["current_bytes"] == 100 and sample["working_set_bytes"] == 90 for sample in samples
    )
    caplog.set_level(logging.INFO, logger="browserd")
    reads = []
    monkeypatch.setattr(chromium, "memory", lambda: reads.append(1))
    runtime._memory_boundary("startup")
    assert reads == []


def test_lifecycle_observer_failure_cannot_escape_to_page_or_cleanup(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="browserd")
    runtime = chromium.Chromium(SimpleNamespace(), "synthetic", None, None)

    def broken():
        raise OSError("canary_41a1633c772e487b")

    monkeypatch.setattr(chromium, "memory", broken)
    runtime._memory_boundary("end")
    runtime._on_navigation(SimpleNamespace(parent_frame=None))
    assert "canary_41a1633c772e487b" not in caplog.text
