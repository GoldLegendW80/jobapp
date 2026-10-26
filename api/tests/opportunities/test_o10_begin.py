"""O8 integration with main's shared manual and wizard sweep start, without a live database."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.jobs import sweep
from app.profile.schemas import SearchSection


@pytest.mark.parametrize("enabled", [True, False])
def test_feed_only_begin_queues_only_an_enabled_feed(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    search = SearchSection.model_validate({"feeds": [{"kind": "remoteok", "enabled": enabled}]})
    monkeypatch.setattr(sweep, "load_search", lambda *_: search)
    row = MagicMock(id=uuid.uuid4())
    start = MagicMock(return_value=row)
    monkeypatch.setattr(sweep, "start", start)
    sessions = MagicMock()
    queued: list[str] = []

    class Queue:
        async def enqueue(self, function: str, *args: str, **kwargs: str) -> bool:
            queued.append(function)
            assert args == (str(row.id),)
            return True

    result = asyncio.run(
        sweep.begin(
            sessions, Queue(), uuid.uuid4(), datetime(2026, 10, 21, tzinfo=UTC), lambda r: r.id
        )
    )
    assert result.outcome == ("started" if enabled else "no_boards")
    assert result.sweep_id == (row.id if enabled else None)
    assert queued == (["run_sweep"] if enabled else [])
    assert start.call_count == int(enabled)
