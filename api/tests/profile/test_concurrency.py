"""Concurrent saves on a real Postgres: with the same base revision exactly one wins, through the
service (separate sessions released together by a barrier) and through the HTTP API."""

import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import COOKIE_NAME
from app.main import create_app
from app.profile import service
from app.profile.models import Profile, ProfileRevision
from tests.conftest import make_settings
from tests.identity.api_helpers import CSRF, Api, Clock, make_user
from tests.profile.conftest import ALICE, signed_in

WRITERS = 20


def history(factory: sessionmaker[Session]) -> tuple[int, list[ProfileRevision]]:
    with factory() as db:
        revision = db.execute(sa.select(Profile.revision)).scalar_one()
        rows = list(db.execute(sa.select(ProfileRevision)).scalars())
    return revision, rows


def test_service_concurrent_saves_one_wins(factory: sessionmaker[Session]) -> None:
    user = make_user(factory, email=ALICE)
    now = datetime.now(UTC)
    barrier = threading.Barrier(WRITERS)

    def attempt(n: int) -> str:
        with factory() as db:
            barrier.wait(timeout=30)
            try:
                service.save_section(db, user.id, "identity", {"full_name": f"W{n}"}, 0, now)
            except service.StaleRevisionError as exc:
                current = exc.profile.revision
                db.rollback()
                return f"stale at {current}"
            db.commit()
            return "saved"

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        outcomes = Counter(pool.map(attempt, range(WRITERS)))
    assert outcomes == {"saved": 1, "stale at 1": WRITERS - 1}
    revision, rows = history(factory)
    assert revision == 1
    assert [r.revision for r in rows] == [1]


def test_service_concurrent_first_reads_create_one_row(factory: sessionmaker[Session]) -> None:
    user = make_user(factory, email=ALICE)
    barrier = threading.Barrier(WRITERS)

    def first_read(_: int) -> int:
        with factory() as db:
            barrier.wait(timeout=30)
            profile = service.get_profile(db, user.id, datetime.now(UTC))
            db.commit()
            return profile.revision

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        assert set(pool.map(first_read, range(WRITERS))) == {0}
    with factory() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(Profile)).scalar_one() == 1


def test_service_sequential_saves_from_fresh_bases_all_win(factory: sessionmaker[Session]) -> None:
    user = make_user(factory, email=ALICE)
    for base in range(5):
        with factory() as db:
            service.save_section(db, user.id, "facts", {"blocks": []}, base, datetime.now(UTC))
            db.commit()
    assert history(factory)[0] == 5


def test_service_refuses_an_unknown_section(factory: sessionmaker[Session]) -> None:
    user = make_user(factory, email=ALICE)
    with factory() as db, pytest.raises(ValueError, match="unknown section"):
        service.save_section(db, user.id, "photo", {}, 0, datetime.now(UTC))


def test_service_reads_only_the_callers_revision(factory: sessionmaker[Session]) -> None:
    alice = make_user(factory, email=ALICE)
    with factory() as db:
        service.save_section(db, alice.id, "facts", {"blocks": []}, 0, datetime.now(UTC))
        db.commit()
        assert service.get_revision(db, alice.id, 1) is not None
        assert service.get_revision(db, uuid.uuid4(), 1) is None
        assert service.list_revisions(db, uuid.uuid4()).items == []
        # Limits are clamped, not trusted.
        assert len(service.list_revisions(db, alice.id, limit=0).items) == 1
        assert len(service.list_revisions(db, alice.id, limit=10_000).items) == 1


@pytest.fixture
def roomy_client(migrated_url: str, clock: Clock) -> Iterator[TestClient]:
    """An app whose pool waits long enough for twenty requests queued on one row lock."""
    settings = make_settings(migrated_url, db_connect_timeout_s=15)
    with TestClient(create_app(settings, clock=clock)) as client:
        yield client


def test_api_twenty_concurrent_saves_with_the_same_base(
    roomy_client: TestClient, api: Api, factory: sessionmaker[Session]
) -> None:
    token = signed_in(factory, api, ALICE)
    assert api.get("/api/profile", token=token).json()["revision"] == 0
    barrier = threading.Barrier(WRITERS)
    headers = {**CSRF, "Cookie": f"{COOKIE_NAME}={token}"}

    def attempt(n: int) -> tuple[int, int]:
        barrier.wait(timeout=30)
        response = roomy_client.put(
            "/api/profile/identity",
            headers=headers,
            json={"base_revision": 0, "data": {"full_name": f"Writer {n}"}},
        )
        return response.status_code, response.json()["revision"]

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        results = list(pool.map(attempt, range(WRITERS)))
    statuses = Counter(status for status, _ in results)
    assert statuses == {200: 1, 409: WRITERS - 1}
    assert {revision for _, revision in results} == {1}  # every loser sees the winner's revision
    revision, rows = history(factory)
    assert revision == 1
    assert len(rows) == 1
    winner = next(n for n, (status, _) in enumerate(results) if status == 200)
    profile = api.get("/api/profile", token=token).json()
    assert profile["sections"]["identity"]["full_name"] == f"Writer {winner}"
