"""Issue 546: parser failures are visible without logging third-party or profile values."""

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.logging_config import JsonFormatter
from app.opportunities import sources
from app.opportunities.sources import FeedError, remoteok
from app.profile.schemas import Feed
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import now, set_profile
from tests.jobs.test_sweep_api import FakeQueue
from tests.opportunities.conftest import Person
from tests.opportunities.o7_support import feed, feeds_search


@pytest.mark.parametrize("skipped", [0, 1, 3])
@pytest.mark.parametrize("store_fails", [False, True])
def test_skip_event_is_once_per_feed_and_contains_only_safe_fields(
    skipped: int,
    store_fails: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = uuid.uuid4().hex
    kinds = list(sources.SOURCES)
    real = remoteok._posting

    def posting(item: Any) -> Any:
        if item["id"] != "good":
            raise ValueError(marker)
        return real(item)

    monkeypatch.setattr(remoteok, "_posting", posting)
    items = [
        {
            "id": "good",
            "company": marker,
            "position": marker,
            "url": "https://remoteok.com/remote-jobs/" + marker,
            "description": marker,
        }
    ]
    items += [{"id": str(n)} for n in range(skipped)]

    async def reader(client: Any, feed_: Feed) -> Any:
        return remoteok.parse([{"legal": marker}, *items])

    kept_postings: list[Any] = []
    stats = SimpleNamespace(sources=[], new=0, skipped_by_rule=0, current_board="")

    async def store(source: str, kept: Any) -> None:
        kept_postings.extend(kept)
        if store_fails:
            raise RuntimeError(marker)
        stats.new += len(kept)

    async def progress() -> None:
        return None

    hook = sources.FeedRunner(
        lambda: sources.FeedClient(stub_base_url="http://feeds.invalid"),
        dict.fromkeys(kinds, reader),
    )
    plan = SimpleNamespace(
        feeds=[Feed(**feed(kind, terms=[marker])) for kind in kinds],
        sweep_id=uuid.uuid4(),
        profile={"text": marker},
        query=marker,
    )
    with caplog.at_level(logging.WARNING, logger="app.opportunities.sources"):
        asyncio.run(hook(plan, stats, store, progress, float("inf")))
    assert len(kept_postings) == len(kinds)
    assert [entry["items_skipped"] for entry in stats.sources] == [skipped] * len(kinds)
    assert [entry["found"] for entry in stats.sources] == [1] * len(kinds)
    assert [entry["error"] for entry in stats.sources] == [
        "store_failed" if store_fails else None
    ] * len(kinds)
    assert stats.new == (0 if store_fails else len(kinds))
    records = [r for r in caplog.records if r.getMessage() == "feed_items_skipped"]
    assert len(records) == (len(kinds) if skipped else 0)
    for record, kind in zip(records, kinds, strict=bool(skipped)):
        payload = json.loads(JsonFormatter().format(record))
        assert {key: value for key, value in payload.items() if key not in {"ts", "message"}} == {
            "level": "warning",
            "logger": "app.opportunities.sources",
            "msg": "feed_items_skipped",
            "kind": kind,
            "items_skipped": skipped,
            "sweep_id": str(plan.sweep_id),
        }
        assert record.exc_info is None
    assert marker not in caplog.text
    assert marker not in json.dumps([JsonFormatter().format(r) for r in caplog.records])


@pytest.mark.parametrize("reader_bug", [False, True])
def test_a_failed_reader_does_not_log_parser_skips(
    reader_bug: bool, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.opportunities.test_o7_limits import _run_feed

    marker = uuid.uuid4().hex

    def failed() -> Any:
        if reader_bug:
            raise ValueError(marker)
        raise FeedError("invalid_response")

    with caplog.at_level(logging.WARNING, logger="app.opportunities.sources"):
        entry = _run_feed("remoteok", failed)
    assert entry["items_skipped"] == 0
    assert entry["error"] == "invalid_response"
    assert not any(r.getMessage() == "feed_items_skipped" for r in caplog.records)
    assert marker not in caplog.text
    assert marker not in json.dumps([JsonFormatter().format(r) for r in caplog.records])


def test_feed_only_start_and_stored_counts_are_private_in_every_sweep_response(
    api: Api,
    app: FastAPI,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
) -> None:
    queue = FakeQueue()
    app.state.queue = queue
    # One immediate snapshot is enough to inspect the real running stream without waiting.
    app.state.sse_settings = {"max_s": 0}
    set_profile(factory, person_a.user_id, feeds_search(feed("remoteok")))
    response = api.request("POST", "/api/jobs/sweeps", token=person_a.token)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    started = response.json()
    assert started["sources"] == []
    assert len(queue.calls) == 1
    sweep_id = uuid.UUID(started["id"])
    with factory() as db, db.begin():
        row = db.get(Sweep, sweep_id)
        assert row is not None
        row.stats = {
            "sources": [
                {
                    "kind": "remoteok",
                    "found": 4,
                    "postings": 2,
                    "skipped_by_rule": 1,
                    "items_skipped": 7,
                    "error": "store_failed",
                }
            ]
        }
        db.add(
            Sweep(
                user_id=person_b.user_id,
                trigger=SweepTrigger.manual,
                status=SweepStatus.done,
                started_at=now(),
                finished_at=now(),
                stats={"sources": [{"kind": "remoteok", "items_skipped": 2}]},
            )
        )

    path = f"/api/jobs/sweeps/{sweep_id}"
    for status, event in [(SweepStatus.running, "progress"), (SweepStatus.done, "done")]:
        if status is SweepStatus.done:
            with factory() as db, db.begin():
                row = db.get(Sweep, sweep_id)
                assert row is not None
                row.status = status
                row.finished_at = now()
        for route in ["/api/jobs/sweeps", path, path + "/events"]:
            own = api.request("GET", route, token=person_a.token)
            assert own.status_code == 200
            assert own.headers["cache-control"].startswith("no-store")
            if route.endswith("/events"):
                assert f"event: {event}" in own.text
                body = json.loads(
                    next(line[6:] for line in own.text.splitlines() if line.startswith("data: "))
                )
            elif route == "/api/jobs/sweeps":
                [body] = own.json()["items"]
            else:
                body = own.json()
            assert body["sources"][0]["items_skipped"] == 7
            assert (
                body["sources"][0]["found"],
                body["sources"][0]["new"],
                body["sources"][0]["skipped_by_rule"],
                body["sources"][0]["error"],
            ) == (4, 2, 1, "store_failed")
            other = api.request("GET", route, token=person_b.token)
            if route == "/api/jobs/sweeps":
                assert other.status_code == 200
                [other_row] = other.json()["items"]
                assert other_row["id"] != str(sweep_id)
                assert other_row["sources"][0]["items_skipped"] == 2
            else:
                assert other.status_code == 404
                assert other.json() == {"detail": "Sweep not found."}
