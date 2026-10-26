"""Dedicated permission intent survives edits, reacceptance and generic profile writes."""

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import product
from typing import Any

import pytest
import sqlalchemy as sa

from app.browser import linkedin_guard
from app.browser.models import LinkedInCheckpoint
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.opportunities.platforms import guard
from app.profile import service
from app.profile.models import Profile, ProfileRevision
from app.profile.schemas import validate_section
from tests.identity.api_helpers import Api
from tests.opportunities.conftest import Person

KINDS = ("linkedin", "freework")
MISSING = "omitted"


def path(kind: str) -> str:
    return "/api/browser/linkedin" if kind == "linkedin" else f"/api/opportunities/platforms/{kind}"


def profile(api: Api, who: Person) -> dict[str, Any]:
    response = api.get("/api/profile", token=who.token)
    assert response.status_code == 200, response.text
    return response.json()


def source(api: Api, who: Person, kind: str) -> dict[str, Any]:
    return next(
        row
        for row in profile(api, who)["sections"]["search"]["browser_sources"]
        if row["kind"] == kind
    )


def seed(factory: Any, who: Person, kind: str, enabled: bool, flag: Any, now: Any) -> None:
    row = {
        "kind": kind,
        "query": "Robotics Engineer",
        "location": "Paris",
        "enabled": enabled,
        "accepted_at": now.isoformat(),
        "text_version": 1,
    }
    if flag != MISSING:
        row["interactive_only"] = flag
    with factory() as db, db.begin():
        stored = db.get(Profile, who.user_id)
        search = {**stored.sections["search"], "browser_sources": [row]}
        stored.sections = {**stored.sections, "search": search}


def snapshot(factory: Any, who: Person) -> Any:
    with factory() as db:
        stored = db.get(Profile, who.user_id)
        revisions = list(
            db.execute(
                sa.select(ProfileRevision.revision, ProfileRevision.section, ProfileRevision.data)
                .where(ProfileRevision.user_id == who.user_id)
                .order_by(ProfileRevision.revision)
            )
        )
        checkpoints = list(
            db.execute(
                sa.select(LinkedInCheckpoint.id, LinkedInCheckpoint.at, LinkedInCheckpoint.origin)
                .where(LinkedInCheckpoint.user_id == who.user_id)
                .order_by(LinkedInCheckpoint.id)
            )
        )
        history = list(
            db.execute(
                sa.select(Sweep.id, Sweep.status, Sweep.stats)
                .where(Sweep.user_id == who.user_id)
                .order_by(Sweep.id)
            )
        )
        return deepcopy(
            (stored.sections, stored.revision, stored.updated_at, revisions, checkpoints, history)
        )


def write(api: Api, who: Person, method: str, search: dict[str, Any], revision: int) -> Any:
    if method == "save":
        return api.request(
            "PUT",
            "/api/profile/search",
            token=who.token,
            json={"data": search, "base_revision": revision},
        )
    exported = api.get("/api/profile/export", token=who.token)
    assert exported.status_code == 200, exported.text
    document = exported.json()
    document["sections"]["search"] = search
    document["base_revision"] = revision
    return api.post("/api/profile/import", token=who.token, json=document)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("stored_enabled", "stored_flag", "request_flag", "accept"),
    list(
        product(
            [False, True],
            [MISSING, False, True],
            [MISSING, False, True],
            [MISSING, 1, 2],
        )
    ),
)
def test_off_preserves_intent_and_acceptance_never_turns_it_back_on(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    stored_enabled: bool,
    stored_flag: Any,
    request_flag: Any,
    accept: Any,
) -> None:
    seed(factory, person_a, kind, stored_enabled, stored_flag, clock())
    before = snapshot(factory, person_a)
    body: dict[str, Any] = {"enabled": False}
    if request_flag != MISSING:
        body["interactive_only"] = request_flag
    if accept != MISSING:
        body["accept_version"] = accept
    response = api.request("PUT", path(kind), token=person_a.token, json=body)
    assert response.status_code == (409 if accept == 2 else 200), response.text
    if accept == 2:
        assert snapshot(factory, person_a) == before
        return
    after = source(api, person_a, kind)
    assert after["enabled"] is False
    assert after["interactive_only"] is (stored_flag is True)
    assert after["text_version"] == 1
    assert response.json()["allowed"] is False
    assert response.json()["enabled"] is False


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("stored_enabled", "stored_flag", "request_flag", "expected"),
    [
        (False, MISSING, MISSING, False),
        (False, False, MISSING, False),
        (False, True, MISSING, False),
        (True, MISSING, MISSING, False),
        (True, False, MISSING, False),
        (True, True, MISSING, True),
        (False, MISSING, False, False),
        (False, False, False, False),
        (False, True, False, False),
        (True, MISSING, False, False),
        (True, False, False, False),
        (True, True, False, False),
        (False, MISSING, True, True),
        (False, False, True, True),
        (False, True, True, True),
        (True, MISSING, True, True),
        (True, False, True, True),
        (True, True, True, True),
    ],
)
@pytest.mark.parametrize("accept", [MISSING, 1, 2])
def test_enable_uses_explicit_intent_or_an_actual_activation_transition(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    stored_enabled: bool,
    stored_flag: Any,
    request_flag: Any,
    expected: bool,
    accept: Any,
) -> None:
    seed(factory, person_a, kind, stored_enabled, stored_flag, clock())
    before = snapshot(factory, person_a)
    body: dict[str, Any] = {"enabled": True, "query": "Robotics in France"}
    if request_flag != MISSING:
        body["interactive_only"] = request_flag
    if accept != MISSING:
        body["accept_version"] = accept
    response = api.request("PUT", path(kind), token=person_a.token, json=body)
    assert response.status_code == (409 if accept == 2 else 200), response.text
    if accept == 2:
        assert snapshot(factory, person_a) == before
        return
    after = source(api, person_a, kind)
    assert after["interactive_only"] is expected
    assert after["enabled"] is True
    assert after["query"] == "Robotics in France"
    assert response.json()["allowed"] is True


@pytest.mark.parametrize("kind", KINDS)
def test_setup_enable_then_old_account_off_and_enable_selects_legacy_intent(
    api: Api,
    person_a: Person,
    kind: str,
) -> None:
    enabled = api.request(
        "PUT",
        path(kind),
        token=person_a.token,
        json={
            "enabled": True,
            "query": "Robotics",
            "accept_version": 1,
            "interactive_only": True,
        },
    )
    assert enabled.status_code == 200, enabled.text
    assert source(api, person_a, kind)["interactive_only"] is True
    assert api.request("PUT", path(kind), token=person_a.token, json={"enabled": False}).is_success
    assert source(api, person_a, kind)["interactive_only"] is True
    legacy = api.request("PUT", path(kind), token=person_a.token, json={"enabled": True})
    assert legacy.status_code == 200, legacy.text
    assert source(api, person_a, kind)["interactive_only"] is False


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("flag", [False, True])
def test_unaccepted_or_stale_acceptance_cannot_grant_authority(
    api: Api,
    person_a: Person,
    factory: Any,
    kind: str,
    flag: bool,
) -> None:
    before = snapshot(factory, person_a)
    for acceptance in ({}, {"accept_version": 2}):
        response = api.request(
            "PUT",
            path(kind),
            token=person_a.token,
            json={
                "enabled": True,
                "query": "Robotics",
                "interactive_only": flag,
                **acceptance,
            },
        )
        assert response.status_code == 409, response.text
        assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", ["indeed", "welcome_to_the_jungle"])
@pytest.mark.parametrize("flag", [False, True])
def test_additional_platform_map_does_not_accept_legacy_intent(
    api: Api,
    person_a: Person,
    factory: Any,
    kind: str,
    flag: bool,
) -> None:
    before = snapshot(factory, person_a)
    response = api.request(
        "PUT",
        path(kind),
        token=person_a.token,
        json={
            "enabled": True,
            "accept_version": 1,
            "interactive_only": flag,
        },
    )
    assert response.status_code == 422, response.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("stored_flag", [False, True])
@pytest.mark.parametrize("incoming_flag", [MISSING, False, True])
def test_generic_writes_preserve_the_stored_intent_and_acceptance(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    method: str,
    stored_flag: bool,
    incoming_flag: Any,
) -> None:
    seed(factory, person_a, kind, True, stored_flag, clock())
    current = profile(api, person_a)
    before = source(api, person_a, kind)
    incoming = {**before, "enabled": False, "query": "Updated robotics", "location": "Lyon"}
    incoming.pop("interactive_only", None)
    if incoming_flag != MISSING:
        incoming["interactive_only"] = incoming_flag
    search = {**current["sections"]["search"], "browser_sources": [incoming], "min_fit": 42}
    response = write(api, person_a, method, search, current["revision"])
    assert response.status_code == 200, response.text
    after = source(api, person_a, kind)
    # LinkedIn words are saved as sent; a stored Free-Work entry keeps its words (#653, #656).
    words = {} if kind == "freework" else {"query": "Updated robotics", "location": "Lyon"}
    assert after == {**before, **words}
    assert profile(api, person_a)["sections"]["search"]["min_fit"] == 42


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("replacement", ["omitted", "site", "vie", "google_careers", "other_kind"])
def test_generic_replacement_retains_the_original_permission_only_row(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    method: str,
    replacement: str,
) -> None:
    seed(factory, person_a, kind, True, True, clock())
    current = profile(api, person_a)
    original = source(api, person_a, kind)
    if replacement == "omitted":
        incoming = []
    else:
        new_kind = (
            ("freework" if kind == "linkedin" else "linkedin")
            if replacement == "other_kind"
            else replacement
        )
        incoming = [
            {
                "kind": new_kind,
                "query": "https://linkedin.com/jobs"
                if kind == "linkedin"
                else "https://free-work.com/jobs",
                "enabled": True,
            }
        ]
    search = {**current["sections"]["search"], "browser_sources": incoming}
    response = write(api, person_a, method, search, current["revision"])
    assert response.status_code == 200, response.text
    assert source(api, person_a, kind) == original
    if replacement == "other_kind":
        other = source(api, person_a, new_kind)
        assert (other["enabled"], other["accepted_at"], other["interactive_only"]) == (
            False,
            None,
            False,
        )
    # A separately configured site is distinct. Its protected-host execution is #631's guard.


@pytest.mark.parametrize("method", ["save", "import"])
def test_old_linkedin_omission_still_removes_a_legacy_row(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    method: str,
) -> None:
    seed(factory, person_a, "linkedin", True, MISSING, clock())
    current = profile(api, person_a)
    search = {**current["sections"]["search"], "browser_sources": []}
    response = write(api, person_a, method, search, current["revision"])
    assert response.status_code == 200, response.text
    assert profile(api, person_a)["sections"]["search"]["browser_sources"] == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
def test_restoration_checks_source_cap_and_stale_revision_before_any_write(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    method: str,
) -> None:
    seed(factory, person_a, kind, True, True, clock())
    current = profile(api, person_a)
    before = snapshot(factory, person_a)
    search = {
        **current["sections"]["search"],
        "browser_sources": [
            {"kind": "vie", "query": f"Robotics {n}", "enabled": True} for n in range(10)
        ],
        "min_fit": 40,
    }
    stale = write(api, person_a, method, search, current["revision"] - 1)
    assert stale.status_code == 409, stale.text
    assert snapshot(factory, person_a) == before
    full = write(api, person_a, method, search, current["revision"])
    assert full.status_code == 422, full.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", KINDS)
def test_another_dedicated_writer_cannot_change_intent_or_acceptance(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
) -> None:
    seed(factory, person_a, kind, False, True, clock())
    before = source(api, person_a, kind)
    with factory() as db, db.begin():
        stored = service.get_profile(db, person_a.user_id, clock())
        forged = {**before, "enabled": True, "interactive_only": False}
        data = validate_section(
            "search", {**stored.sections["search"], "browser_sources": [forged]}
        )
        trusted = (
            {"platform_consent": "freework"} if kind == "linkedin" else {"linkedin_consent": True}
        )
        service.save_section(
            db, person_a.user_id, "search", data, stored.revision, clock(), **trusted
        )
    assert source(api, person_a, kind) == before


@pytest.mark.parametrize("kind", KINDS)
def test_intent_and_acceptance_cannot_be_imported_from_another_person(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: Any,
    clock: Any,
    kind: str,
) -> None:
    seed(factory, person_a, kind, True, True, clock())
    before = snapshot(factory, person_a)
    incoming = api.get("/api/profile/export", token=person_a.token).json()
    incoming["base_revision"] = profile(api, person_b)["revision"]
    response = api.post("/api/profile/import", token=person_b.token, json=incoming)
    assert response.status_code == 200, response.text
    own = source(api, person_b, kind)
    assert (own["enabled"], own["accepted_at"], own["interactive_only"]) == (False, None, False)
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", KINDS)
def test_off_reacceptance_and_intent_changes_do_not_clear_an_existing_checkpoint(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
) -> None:
    seed(factory, person_a, kind, True, True, clock())
    with factory() as db, db.begin():
        if kind == "linkedin":
            linkedin_guard.record_checkpoint(db, person_a.user_id, clock())
        else:
            db.add(
                Sweep(
                    user_id=person_a.user_id,
                    trigger=SweepTrigger.manual,
                    status=SweepStatus.done,
                    started_at=clock(),
                    finished_at=clock(),
                    stats={"sources": [{"kind": kind, "checkpoint_at": clock().isoformat()}]},
                )
            )
    before = api.get(path(kind), token=person_a.token).json()
    assert before["paused_until"] is not None
    for enabled, flag in [(False, False), (False, True), (True, False), (True, True)]:
        response = api.request(
            "PUT",
            path(kind),
            token=person_a.token,
            json={
                "enabled": enabled,
                "accept_version": 1,
                "interactive_only": flag,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["allowed"] is False
        assert response.json()["paused_until"] == before["paused_until"]
    with factory() as db:
        state = (
            linkedin_guard.state(db, person_a.user_id, clock())
            if kind == "linkedin"
            else guard.state(db, person_a.user_id, kind, clock())
        )
        assert state.paused_until is not None


ALL_KINDS = ("linkedin", "freework", "indeed", "welcome_to_the_jungle")


def reviewed_body(kind: str, enabled: bool, revision: int) -> dict[str, Any]:
    body = {"enabled": enabled, "accept_version": 1, "base_revision": revision}
    if kind in KINDS:
        body["query"] = "Reviewed robotics"
    return body


def add_checkpoint(factory: Any, who: Person, kind: str, now: Any) -> None:
    with factory() as db, db.begin():
        if kind == "linkedin":
            linkedin_guard.record_checkpoint(db, who.user_id, now)
        else:
            db.add(
                Sweep(
                    user_id=who.user_id,
                    trigger=SweepTrigger.manual,
                    status=SweepStatus.done,
                    started_at=now,
                    finished_at=now,
                    stats={"sources": [{"kind": kind, "checkpoint_at": now.isoformat()}]},
                )
            )


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("enabled", [False, True])
def test_a_reviewed_revision_allows_the_current_consent_write(
    api: Api,
    person_a: Person,
    kind: str,
    enabled: bool,
) -> None:
    revision = profile(api, person_a)["revision"]
    response = api.request(
        "PUT", path(kind), token=person_a.token, json=reviewed_body(kind, enabled, revision)
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is enabled


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("enabled", [False, True])
def test_edit_committed_before_consent_request_is_not_overwritten(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    enabled: bool,
) -> None:
    add_checkpoint(factory, person_a, kind, clock())
    reviewed = profile(api, person_a)
    ordinary = {**reviewed["sections"]["search"], "min_fit": 43}
    if kind in KINDS:
        ordinary["browser_sources"] = [{"kind": kind, "query": "Changed in another tab"}]
    saved = write(api, person_a, "save", ordinary, reviewed["revision"])
    assert saved.status_code == 200, saved.text
    before = snapshot(factory, person_a)
    response = api.request(
        "PUT",
        path(kind),
        token=person_a.token,
        json=reviewed_body(kind, enabled, reviewed["revision"]),
    )
    assert response.status_code == 409, response.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_stale_off_noop_still_requires_the_reviewed_revision(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
) -> None:
    add_checkpoint(factory, person_a, kind, clock())
    revision = profile(api, person_a)["revision"]
    before = snapshot(factory, person_a)
    response = api.request(
        "PUT",
        path(kind),
        token=person_a.token,
        json={"enabled": False, "base_revision": revision - 1},
    )
    assert response.status_code == 409, response.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("value", [-1, True, "1", 1.5])
def test_client_revision_is_a_strict_nonnegative_integer(
    api: Api,
    person_a: Person,
    factory: Any,
    kind: str,
    value: Any,
) -> None:
    before = snapshot(factory, person_a)
    response = api.request(
        "PUT", path(kind), token=person_a.token, json={"enabled": False, "base_revision": value}
    )
    assert response.status_code == 422, response.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("enabled", [False, True])
def test_profile_edit_holding_the_lock_after_request_read_wins_the_conflict(
    api: Api,
    person_a: Person,
    factory: Any,
    app: Any,
    clock: Any,
    monkeypatch: Any,
    kind: str,
    enabled: bool,
) -> None:
    add_checkpoint(factory, person_a, kind, clock())
    reviewed = profile(api, person_a)
    before = snapshot(factory, person_a)
    looked, release, locking = threading.Event(), threading.Event(), threading.Event()
    request_thread = []
    original = service.get_profile
    request_engine = app.state.sessionmaker.kw["bind"]

    def paused_read(*args: Any, **kwargs: Any) -> Any:
        assert args[0].get_bind() is request_engine
        result = original(*args, **kwargs)
        request_thread.append(threading.get_ident())
        looked.set()
        assert release.wait(5), "request profile read was not released"
        return result

    def before_execute(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if request_thread and threading.get_ident() == request_thread[0]:
            sql = statement.lstrip().upper()
            if "FOR UPDATE" in sql or sql.startswith("INSERT"):
                locking.set()

    monkeypatch.setattr(service, "get_profile", paused_read)
    sa.event.listen(request_engine, "before_cursor_execute", before_execute)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                api.request,
                "PUT",
                path(kind),
                token=person_a.token,
                json=reviewed_body(kind, enabled, reviewed["revision"]),
            )
            future.add_done_callback(lambda _: looked.set())
            try:
                assert looked.wait(5), "request never read its profile"
                if future.done():
                    response = future.result()
                    assert response.status_code == 409, response.text
                    pytest.fail("request finished without exercising the row-lock race")
                with factory() as db, db.begin():
                    held = db.get(Profile, person_a.user_id, with_for_update=True)
                    changed = validate_section("search", {**held.sections["search"], "min_fit": 44})
                    service.save_section(
                        db, person_a.user_id, "search", changed, held.revision, clock()
                    )
                    expected_sections = deepcopy(held.sections)
                    release.set()
                    assert locking.wait(5), "request did not attempt the held profile lock"
                response = future.result(timeout=5)
                assert response.status_code == 409, response.text
                after = snapshot(factory, person_a)
                assert after[0] == expected_sections
                assert after[1] == reviewed["revision"] + 1
                assert len(after[3]) == len(before[3]) + 1
                assert after[4:] == before[4:]
                assert not any(
                    row["query"] == "Reviewed robotics"
                    for row in after[0]["search"].get("browser_sources", [])
                )
            finally:
                release.set()
    finally:
        sa.event.remove(request_engine, "before_cursor_execute", before_execute)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_noop_off_rechecks_a_profile_edit_committed_during_the_request(
    api: Api,
    person_a: Person,
    factory: Any,
    app: Any,
    clock: Any,
    monkeypatch: Any,
    kind: str,
) -> None:
    add_checkpoint(factory, person_a, kind, clock())
    reviewed = profile(api, person_a)
    before = snapshot(factory, person_a)
    looked, release, progressed = threading.Event(), threading.Event(), threading.Event()
    guarded_read = threading.Event()
    request_thread = []
    original = service.get_profile
    request_engine = app.state.sessionmaker.kw["bind"]

    def paused_read(*args: Any, **kwargs: Any) -> Any:
        assert args[0].get_bind() is request_engine
        result = original(*args, **kwargs)
        if not kwargs.get("lock", False):
            request_thread.append(threading.get_ident())
            looked.set()
            assert release.wait(5), "initial profile read was not released"
        return result

    def before_execute(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if request_thread and threading.get_ident() == request_thread[0]:
            sql = statement.lstrip().upper()
            if "FOR UPDATE" in sql or sql.startswith("INSERT"):
                guarded_read.set()
                progressed.set()

    monkeypatch.setattr(service, "get_profile", paused_read)
    sa.event.listen(request_engine, "before_cursor_execute", before_execute)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                api.request,
                "PUT",
                path(kind),
                token=person_a.token,
                json={"enabled": False, "base_revision": reviewed["revision"]},
            )
            future.add_done_callback(lambda _: looked.set())
            future.add_done_callback(lambda _: progressed.set())
            try:
                assert looked.wait(5), "request never read its profile"
                if future.done():
                    response = future.result()
                    assert response.status_code == 409, response.text
                    pytest.fail("request did not exercise the no-op race")
                with factory() as db, db.begin():
                    held = db.get(Profile, person_a.user_id, with_for_update=True)
                    changed = validate_section("search", {**held.sections["search"], "min_fit": 45})
                    service.save_section(
                        db, person_a.user_id, "search", changed, held.revision, clock()
                    )
                    expected_sections = deepcopy(held.sections)
                    release.set()
                    assert progressed.wait(5), "no-op made no progress after its initial read"
                    if future.done():
                        response = future.result()
                        assert response.status_code == 409, response.text
                        pytest.fail("no-op returned before checking the held profile lock")
                    assert guarded_read.is_set()
                response = future.result(timeout=5)
                assert response.status_code == 409, response.text
                after = snapshot(factory, person_a)
                assert after[0] == expected_sections
                assert after[1] == reviewed["revision"] + 1
                assert len(after[3]) == len(before[3]) + 1
                assert after[4:] == before[4:]
            finally:
                release.set()
    finally:
        sa.event.remove(request_engine, "before_cursor_execute", before_execute)


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("with_revision", [False, True])
def test_noop_off_retains_profile_revisions_and_checkpoints(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
    kind: str,
    with_revision: bool,
) -> None:
    add_checkpoint(factory, person_a, kind, clock())
    reviewed = profile(api, person_a)
    before = snapshot(factory, person_a)
    body = {"enabled": False}
    if with_revision:
        body["base_revision"] = reviewed["revision"]
    response = api.request("PUT", path(kind), token=person_a.token, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False
    assert snapshot(factory, person_a) == before
