"""The shared manual and wizard sweep start, without a live database: a feed alone, even
enabled, is no longer a search (owner, 2026-10-28: every search runs the websites alone)."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.jobs import sweep
from app.profile.schemas import SearchSection


@pytest.mark.parametrize("enabled", [True, False])
def test_a_feed_only_search_queues_nothing(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
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
    assert result.outcome == "no_websites"
    assert result.sweep_id is None
    assert queued == []
    assert start.call_count == 0
