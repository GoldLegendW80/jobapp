"""The newest sweep of each search in GET /api/jobs/searches (jobs J33b, #832): however old, for
the main search, each saved search and none for a deleted one's; read for the whole list in one
query whatever the number of searches."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import searches
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.identity.api_helpers import Api
from tests.jobs.test_api import Signed, sign_in

BASE = "/api/jobs/searches"
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j33b-alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-j33b-bob@example.org")


def saved(factory: sessionmaker[Session], user_id: uuid.UUID, *names: str) -> list[uuid.UUID]:
    with factory() as db:
        rows = [searches.create(db, user_id, name, {}, searches.SavedSchedule()) for name in names]
        db.commit()
        return [row.id for row in rows]


def ran(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    at: datetime,
    *,
    search: tuple[uuid.UUID | None, str] | None = None,
    new: Any = 0,
    running: bool = False,
) -> uuid.UUID:
    """A sweep as the line leaves it: a saved search's froze its name and keeps its id until the
    search is deleted (then None, the name kept)."""
    stats: dict[str, Any] = {} if new is None else {"new": new}
    if search is not None:
        stats["search_scope"] = {"saved_search": {"id": str(search[0]), "name": search[1]}}
    row = Sweep(
        user_id=user_id,
        trigger=SweepTrigger.manual,
        status=SweepStatus.running if running else SweepStatus.done,
        started_at=at,
        finished_at=None if running else at + timedelta(minutes=10),
        stats=stats,
        saved_search_id=search[0] if search else None,
    )
    with factory() as db:
        db.add(row)
        db.commit()
        return row.id


def listed(api: Api, who: Signed) -> dict[str, Any]:
    response = api.request("GET", BASE, token=who.token)
    assert response.status_code == 200, response.text
    return {item["name"]: item["last_run"] for item in response.json()["items"]}


def test_each_search_says_its_newest_run_older_than_the_last_twenty_sweeps_too(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    lyon, remote, _never = saved(factory, alice.id, "Data in Lyon", "Remote roles", "Never")
    first = ran(factory, alice.id, T0, search=(lyon, "Data in Lyon"), new=2)
    newest = ran(factory, alice.id, T0 + timedelta(hours=1), search=(lyon, "Data in Lyon"), new=4)
    # 25 sweeps of the main search after it: Data in Lyon's run is past the last 20.
    main = [ran(factory, alice.id, T0 + timedelta(hours=2 + i), new=i) for i in range(25)]
    # A deleted search's sweep keeps its frozen name and loses its id: it is no search's.
    ran(factory, alice.id, T0 + timedelta(days=3), search=(None, "Gone"), new=9)
    running = ran(
        factory, alice.id, T0 + timedelta(days=4), search=(remote, "Remote roles"), running=True
    )
    # Another person's newest sweep is never hers.
    ran(factory, bob.id, T0 + timedelta(days=5), new=99)

    runs = listed(api, alice)
    assert runs["Data in Lyon"] == {
        "sweep_id": str(newest),
        "status": "done",
        "started_at": "2026-11-01T09:00:00Z",
        "finished_at": "2026-11-01T09:10:00Z",
        "new": 4,
    }
    assert first != newest
    assert runs["Main search"]["sweep_id"] == str(main[-1])
    assert runs["Main search"]["new"] == 24
    assert runs["Remote roles"]["sweep_id"] == str(running)
    assert (runs["Remote roles"]["status"], runs["Remote roles"]["finished_at"]) == (
        "running",
        None,
    )
    assert runs["Remote roles"]["new"] == 0
    assert runs["Never"] is None
    assert listed(api, bob)["Main search"]["new"] == 99


@pytest.mark.parametrize("stored", [None, True, "4", -3, 2.5])
def test_a_count_that_is_no_whole_number_reads_as_none_new(
    api: Api, factory: sessionmaker[Session], alice: Signed, stored: Any
) -> None:
    ran(factory, alice.id, T0, new=stored)
    assert listed(api, alice)["Main search"]["new"] == 0


@contextmanager
def statements(app: FastAPI) -> Iterator[list[str]]:
    """The SQL the api's own sessions send inside the block."""
    sent: list[str] = []

    def note(*args: Any) -> None:
        sent.append(args[2])

    engine = app.state.sessionmaker.kw["bind"]
    sa.event.listen(engine, "before_cursor_execute", note)
    try:
        yield sent
    finally:
        sa.event.remove(engine, "before_cursor_execute", note)


def test_the_list_reads_every_last_run_in_one_query_whatever_the_number_of_searches(
    api: Api, app: FastAPI, factory: sessionmaker[Session], alice: Signed
) -> None:
    def reads_of_sweeps() -> list[str]:
        with statements(app) as sent:
            listed(api, alice)
        return [one for one in sent if "FROM sweeps" in one]

    [first] = saved(factory, alice.id, "First")
    ran(factory, alice.id, T0, search=(first, "First"), new=1)
    one = reads_of_sweeps()
    assert sum("row_number()" in statement for statement in one) == 1
    for index, name in enumerate(("Second", "Third", "Fourth")):
        [other] = saved(factory, alice.id, name)
        ran(factory, alice.id, T0 + timedelta(hours=index + 1), search=(other, name), new=2)
    assert len(reads_of_sweeps()) == len(one)
    assert set(listed(api, alice)) == {"Main search", "First", "Second", "Third", "Fourth"}
