"""The J4 routes through the app: PATCH /api/jobs/{id} (every field, events with before and
after, applied_on once, manual fit set and cleared, stale versions, duplicates, isolation),
POST /api/jobs/bulk and /bulk/undo (500 ids, atomic 404, notes on many, undo once, expiry,
tampering), POST /api/jobs (scored with the profile's skills, duplicates by URL variants and by
company, title and location), POST /api/jobs/rescore (counts, manual fits, time budget), CSRF."""

import uuid
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import router as jobs_router
from app.jobs import scoring
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.main import create_app
from app.profile.models import Profile
from tests.conftest import make_settings
from tests.identity.api_helpers import Api, Clock
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, seed, sign_in
from tests.jobs.test_scoring import skills as make_skills

ALICE = "alice@example.org"
BOB = "bob@example.org"
SKILLS = make_skills()
POSTING = "About the role\n\nBuild an agent with tool use in Python. Some RAG.\n"


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, BOB)


def give_skills(factory: sessionmaker[Session], user: uuid.UUID, **overrides: Any) -> None:
    data = make_skills(**overrides).model_dump(mode="json")
    with factory() as db, db.begin():
        db.add(Profile(user_id=user, sections={"skills": data}, revision=1, updated_at=NOW))


def patch(api: Api, who: Signed, job_id: Any, **body: Any) -> httpx.Response:
    return api.request("PATCH", f"/api/jobs/{job_id}", token=who.token, json=body)


def ok_patch(api: Api, who: Signed, job_id: Any, **body: Any) -> dict[str, Any]:
    response = patch(api, who, job_id, **body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[tuple[str, dict]]:
    with factory() as db:
        rows = db.execute(
            sa.select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        ).scalars()
        return [(e.kind.value, e.data) for e in rows]


def stored(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


# --- PATCH --------------------------------------------------------------------------------------


def test_patch_status_writes_an_event_and_fills_applied_on_once(
    api: Api, alice: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    body = ok_patch(api, alice, job.id, status="shortlisted")
    assert body["status"] == "shortlisted"
    assert body["applied_on"] is None
    assert body["events"][0]["kind"] == "status"
    assert body["events"][0]["data"] == {"from": "found", "to": "shortlisted"}
    body = ok_patch(api, alice, job.id, status="applied")
    assert body["applied_on"] == clock.now.date().isoformat()
    first_applied = body["applied_on"]
    clock.advance(days=3)
    ok_patch(api, alice, job.id, status="interview")
    body = ok_patch(api, alice, job.id, status="applied")
    assert body["applied_on"] == first_applied  # filled once, never moved
    # The same status again is no change: no event.
    count = len(events(factory, job.id))
    ok_patch(api, alice, job.id, status="applied")
    assert len(events(factory, job.id)) == count


def test_patch_notes_records_before_and_after_and_coalesces_autosaves(
    api: Api, alice: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    (job,) = seed(factory, alice.id, [{"notes": "old"}])
    ok_patch(api, alice, job.id, notes="old and")
    clock.advance(seconds=5)
    body = ok_patch(api, alice, job.id, notes="old and new ✓ émoji 🚀")
    assert body["notes"] == "old and new ✓ émoji 🚀"
    notes = [d for k, d in events(factory, job.id) if k == "note"]
    assert notes == [{"from": "old", "to": "old and new ✓ émoji 🚀"}]  # one event while typing
    clock.advance(minutes=5)
    ok_patch(api, alice, job.id, notes="")
    notes = [d for k, d in events(factory, job.id) if k == "note"]
    assert notes[-1] == {"from": "old and new ✓ émoji 🚀", "to": ""}
    assert len(notes) == 2
    # Typing back to where the edit started removes the event: nothing changed in the end.
    clock.advance(seconds=10)
    ok_patch(api, alice, job.id, notes="old and new ✓ émoji 🚀")
    assert len([d for k, d in events(factory, job.id) if k == "note"]) == 1


def test_patch_notes_limits(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job,) = seed(factory, alice.id, [{}])
    assert patch(api, alice, job.id, notes="x" * 100_000).status_code == 200
    assert patch(api, alice, job.id, notes="x" * 100_001).status_code == 422
    assert patch(api, alice, job.id, notes=None).status_code == 422


def test_patch_fit_override_set_and_cleared(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    give_skills(factory, alice.id)
    computed = scoring.score(POSTING, SKILLS, title="Role 0").fit
    (job,) = seed(factory, alice.id, [{"fit": computed, "posting_text": POSTING}])
    body = ok_patch(api, alice, job.id, fit=42)
    assert (body["fit"], body["fit_overridden"]) == (42, True)
    body = ok_patch(api, alice, job.id, fit=42)  # the same again: nothing new
    assert [k for k, _ in events(factory, job.id)].count("fit") == 1
    body = ok_patch(api, alice, job.id, fit=None)
    assert (body["fit"], body["fit_overridden"]) == (computed, False)
    fits = [d for k, d in events(factory, job.id) if k == "fit"]
    assert fits == [
        {"from": computed, "to": 42, "manual": True},
        {"from": 42, "to": computed, "manual": False, "was_manual": True},
    ]
    # Clearing a fit that is not manual is no change.
    ok_patch(api, alice, job.id, fit=None)
    assert [k for k, _ in events(factory, job.id)].count("fit") == 2
    for bad in (-1, 101, 50.5, "80", True):
        assert patch(api, alice, job.id, fit=bad).status_code == 422, bad


def test_clearing_a_manual_fit_needs_something_to_compute_from(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    (no_text,) = seed(factory, alice.id, [{"fit": 70, "fit_overridden": True}])
    refused = patch(api, alice, no_text.id, fit=None)
    assert refused.status_code == 422
    assert "skills" in refused.json()["detail"]
    give_skills(factory, alice.id)
    refused = patch(api, alice, no_text.id, fit=None)
    assert refused.status_code == 422
    assert "posting text" in refused.json()["detail"]
    assert stored(factory, no_text.id).fit_overridden is True  # nothing written


def test_patch_details_with_one_edit_event(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    body = ok_patch(
        api,
        alice,
        job.id,
        company="  Mistral   AI ",
        title="Applied AI Engineer",
        location="Paris, France",
        contract_type="VIE",
        url="https://jobs.ashbyhq.com/mistral/42",
    )
    assert (body["company"], body["title"], body["location"], body["contract_type"]) == (
        "Mistral AI",
        "Applied AI Engineer",
        "Paris, France",
        "VIE",
    )
    assert body["slug"] == "job-0"  # the slug is the id of the row: an edit keeps it
    (edit,) = [d for k, d in events(factory, job.id) if k == "edit"]
    assert edit["changes"]["company"] == {"from": "Acme", "to": "Mistral AI"}
    assert edit["changes"]["contract_type"] == {"from": "CDI", "to": "VIE"}
    assert set(edit["changes"]) == {"company", "title", "location", "contract_type", "url"}
    body = ok_patch(api, alice, job.id, url=None)
    assert body["url"] is None
    body = ok_patch(api, alice, job.id, url="")
    assert body["url"] is None
    assert len([k for k, _ in events(factory, job.id) if k == "edit"]) == 2  # "" after None: none


@pytest.mark.parametrize(
    "body",
    [
        {"status": "ghosted"},
        {"status": None},
        {"contract_type": "Permanent"},
        {"company": "   "},
        {"title": ""},
        {"company": None},
        {"url": "javascript:alert(1)"},
        {"url": "ftp://example.org/x"},
        {"url": "/relative/path"},
        {"url": "https://exa mple.org"},
        {"title": "x" * 1001},
        {"slug": "new-slug"},
        {"unknown": 1},
        {},
        {"updated_at": "2026-10-08T09:30:00Z"},
    ],
)
def test_patch_invalid_bodies_are_422(
    api: Api, alice: Signed, factory: sessionmaker[Session], body: dict[str, Any]
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    response = patch(api, alice, job.id, **body)
    assert response.status_code == 422, response.text
    assert "detail" in response.json()
    assert events(factory, job.id) == []


def test_stale_version_is_409(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job,) = seed(factory, alice.id, [{}])
    first = ok_patch(api, alice, job.id, notes="mine")
    version = first["updated_at"]
    second = ok_patch(api, alice, job.id, notes="mine too", updated_at=version)
    assert second["updated_at"] != version  # every write moves the version, same clock or not
    stale = patch(api, alice, job.id, status="skipped", updated_at=version)
    assert stale.status_code == 409
    assert stale.json()["updated_at"] == second["updated_at"]
    assert "changed since" in stale.json()["detail"]
    assert stored(factory, job.id).status is JobStatus.found  # nothing written
    fresh = ok_patch(api, alice, job.id, status="skipped", updated_at=second["updated_at"])
    assert fresh["status"] == "skipped"


def test_duplicate_edit_is_409_with_the_existing_job(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    a, b = seed(
        factory,
        alice.id,
        [
            {"company": "Acme", "title": "AI Engineer", "location": "Paris"},
            {"company": "Beta", "title": "ML Engineer", "location": "Lyon"},
        ],
    )
    same_identity = patch(api, alice, b.id, company="ACME", title="ai engineer", location="paris")
    assert same_identity.status_code == 409
    assert same_identity.json()["existing_id"] == str(a.id)
    assert same_identity.json()["existing_slug"] == a.slug
    variant = a.url.replace("https://", "http://www.") + "/?utm_source=x"
    same_url = patch(api, alice, b.id, url=variant)
    assert same_url.status_code == 409
    assert same_url.json()["existing_id"] == str(a.id)
    assert stored(factory, b.id).company == "Beta"
    # Editing a job to its own values, or to another city, is not a duplicate.
    assert patch(api, alice, a.id, company="Acme", url=a.url).status_code == 200
    assert patch(api, alice, b.id, company="Acme", title="AI Engineer").status_code == 200


def test_patch_isolation_and_unknown_ids(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]
) -> None:
    (theirs,) = seed(factory, bob.id, [{}])
    assert patch(api, alice, theirs.id, status="skipped").status_code == 404
    assert patch(api, alice, uuid.uuid4(), status="skipped").status_code == 404
    assert patch(api, alice, "not-a-uuid", status="skipped").status_code == 404
    assert stored(factory, theirs.id).status is JobStatus.found
    assert events(factory, theirs.id) == []


def test_session_and_csrf_on_every_write(api: Api, alice: Signed) -> None:
    some = uuid.uuid4()
    writes = [
        ("PATCH", f"/api/jobs/{some}", {"status": "found"}),
        ("POST", "/api/jobs", {"company": "A", "title": "B"}),
        ("POST", "/api/jobs/bulk", {"ids": [str(some)], "set": {"status": "found"}}),
        ("POST", "/api/jobs/bulk/undo", {"token": "x.y"}),
        ("POST", "/api/jobs/rescore", {}),
    ]
    for method, path, body in writes:
        assert api.request(method, path, json=body).status_code == 401, path
        refused = api.request(method, path, token=alice.token, csrf=False, json=body)
        assert refused.status_code == 403, path


# --- bulk and undo ------------------------------------------------------------------------------


def bulk(api: Api, who: Signed, ids: list[Any], **set_: Any) -> httpx.Response:
    return api.post(
        "/api/jobs/bulk", token=who.token, json={"ids": [str(i) for i in ids], "set": set_}
    )


def undo(api: Api, who: Signed, token: str) -> httpx.Response:
    return api.post("/api/jobs/bulk/undo", token=who.token, json={"token": token})


def test_bulk_500_ids_in_one_call_then_undo_restores_exactly_once(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    rows: list[dict[str, Any]] = [{} for _ in range(500)]
    rows[0] = {"status": JobStatus.shortlisted}
    rows[1] = {"status": JobStatus.interview, "applied_on": date(2026, 9, 1)}
    rows[2] = {"status": JobStatus.applied, "applied_on": date(2026, 9, 2)}
    jobs = seed(factory, alice.id, rows)
    response = bulk(api, alice, [j.id for j in jobs], status="applied")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["changed"], body["unchanged"]) == (499, 1)
    assert body["results"][2] == {
        "id": str(jobs[2].id),
        "changed": False,
        "status": "applied",
        "previous_status": "applied",
    }
    assert body["undo_token"]
    assert body["undo_expires_at"]
    with factory() as db:
        applied = db.execute(
            sa.select(sa.func.count()).where(Job.user_id == alice.id, Job.applied_on.is_not(None))
        ).scalar_one()
        assert applied == 500
        via = db.execute(
            sa.select(sa.func.count()).where(
                JobEvent.job_id.in_([j.id for j in jobs]),
                JobEvent.kind == JobEventKind.status,
                JobEvent.data["via"].astext == "bulk",
            )
        ).scalar_one()
        assert via == 499

    restored = undo(api, alice, body["undo_token"])
    assert restored.status_code == 200, restored.text
    assert len(restored.json()["restored"]) == 499
    assert restored.json()["skipped"] == []
    assert stored(factory, jobs[0].id).status is JobStatus.shortlisted
    assert stored(factory, jobs[0].id).applied_on is None  # bulk filled it, undo cleared it
    assert stored(factory, jobs[1].id).status is JobStatus.interview
    assert stored(factory, jobs[1].id).applied_on == date(2026, 9, 1)
    assert stored(factory, jobs[2].id).applied_on == date(2026, 9, 2)
    assert stored(factory, jobs[42].id).status is JobStatus.found

    again = undo(api, alice, body["undo_token"])
    assert again.status_code == 409
    assert stored(factory, jobs[0].id).status is JobStatus.shortlisted


def test_bulk_501_ids_is_422_and_empty_is_422(api: Api, alice: Signed) -> None:
    assert bulk(api, alice, [uuid.uuid4() for _ in range(501)], status="found").status_code == 422
    assert bulk(api, alice, [], status="found").status_code == 422
    some = [uuid.uuid4()]
    assert bulk(api, alice, some).status_code == 422  # nothing to set
    assert bulk(api, alice, some, notes_append="   ").status_code == 422
    assert bulk(api, alice, some, status="ghosted").status_code == 422
    assert bulk(api, alice, ["not-a-uuid"], status="found").status_code == 422


def test_bulk_with_a_foreign_id_changes_nothing_and_lists_it(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]
) -> None:
    mine = seed(factory, alice.id, [{}, {}])
    (theirs,) = seed(factory, bob.id, [{}])
    ghost = uuid.uuid4()
    response = bulk(api, alice, [mine[0].id, theirs.id, mine[1].id, ghost], status="skipped")
    assert response.status_code == 404
    assert response.json()["missing"] == [str(theirs.id), str(ghost)]
    assert "2 jobs were not found" in response.json()["detail"]
    for job in (*mine, theirs):
        assert stored(factory, job.id).status is JobStatus.found
        assert events(factory, job.id) == []


def test_bulk_notes_append_on_many(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    jobs = seed(factory, alice.id, [{"notes": "first line\n\n"}, {"notes": ""}, {"notes": "x"}])
    ids = [j.id for j in jobs] + [jobs[0].id]  # a repeated id counts once
    body = bulk(api, alice, ids, notes_append="  Called back 9 Oct  ").json()
    assert body["changed"] == 3
    assert body["undo_token"] is None  # no status changed: nothing to undo
    assert stored(factory, jobs[0].id).notes == "first line\nCalled back 9 Oct"
    assert stored(factory, jobs[1].id).notes == "Called back 9 Oct"
    assert events(factory, jobs[1].id) == [
        ("note", {"from": "", "to": "Called back 9 Oct", "via": "bulk"})
    ]


def test_bulk_status_and_note_together_undo_restores_the_status(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    jobs = seed(factory, alice.id, [{}, {"status": JobStatus.skipped}])
    body = bulk(api, alice, [j.id for j in jobs], status="skipped", notes_append="n").json()
    assert body["changed"] == 2
    assert undo(api, alice, body["undo_token"]).status_code == 200
    assert stored(factory, jobs[0].id).status is JobStatus.found
    assert stored(factory, jobs[0].id).notes == "n"  # the undo is of statuses only


def test_undo_skips_jobs_changed_since(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    a, b = seed(factory, alice.id, [{}, {}])
    token = bulk(api, alice, [a.id, b.id], status="shortlisted").json()["undo_token"]
    ok_patch(api, alice, b.id, status="cv_ready")
    body = undo(api, alice, token).json()
    assert body == {"restored": [str(a.id)], "skipped": [str(b.id)]}
    assert stored(factory, b.id).status is JobStatus.cv_ready


def test_undo_expires_after_ten_minutes(
    api: Api, alice: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    token = bulk(api, alice, [job.id], status="skipped").json()["undo_token"]
    clock.advance(minutes=10, seconds=1)
    expired = undo(api, alice, token)
    assert expired.status_code == 410
    assert stored(factory, job.id).status is JobStatus.skipped


def test_undo_within_the_window_works(
    api: Api, alice: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    token = bulk(api, alice, [job.id], status="skipped").json()["undo_token"]
    clock.advance(minutes=9, seconds=59)
    assert undo(api, alice, token).status_code == 200


def test_tampered_and_foreign_tokens_are_rejected(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]
) -> None:
    (job,) = seed(factory, alice.id, [{}])
    token = bulk(api, alice, [job.id], status="skipped").json()["undo_token"]
    payload, signature = token.split(".")
    flipped = ("A" if payload[5] != "A" else "B").join((payload[:5], payload[6:]))
    for bad in (
        f"{flipped}.{signature}",
        f"{payload}.{signature[:-2]}",
        f"{payload}.",
        "x.y",
        "no-dot",
        "a.b.c",
        token + "x",
        "é.ü",
    ):
        response = undo(api, alice, bad)
        assert response.status_code == 400, bad
        assert response.json()["detail"] == "This undo token is not valid."
    assert undo(api, bob, token).status_code == 400  # another user's token
    assert api.post("/api/jobs/bulk/undo", token=alice.token, json={"token": ""}).status_code == 422
    assert stored(factory, job.id).status is JobStatus.skipped
    assert undo(api, alice, token).status_code == 200


def test_undo_token_signed_with_secret_key(
    migrated_url: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    """With SECRET_KEY set, a token survives a restart (another app with the same key) and a
    server with another key refuses it."""

    def app_with(secret: str) -> TestClient:
        return TestClient(create_app(make_settings(migrated_url, secret_key=secret), clock=clock))

    with app_with("k" * 40) as first:
        one = Api(first)
        alice = sign_in(factory, one, ALICE)
        (job,) = seed(factory, alice.id, [{}])
        token = bulk(one, alice, [job.id], status="skipped").json()["undo_token"]
    with app_with("z" * 40) as other:
        assert undo(Api(other), alice, token).status_code == 400
    with app_with("k" * 40) as restarted:
        assert undo(Api(restarted), alice, token).status_code == 200


# --- add by hand --------------------------------------------------------------------------------


def add(api: Api, who: Signed, **body: Any) -> httpx.Response:
    return api.post("/api/jobs", token=who.token, json=body)


def test_add_with_text_is_scored_with_the_profile_skills(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    give_skills(factory, alice.id)
    response = add(
        api,
        alice,
        company=" Mistral AI ",
        title="Applied AI Engineer",
        location="Paris",
        url="https://jobs.ashbyhq.com/mistral/1",
        posting_text=POSTING,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    expected = scoring.score(POSTING.strip(), SKILLS, title="Applied AI Engineer")
    assert (body["fit"], body["competencies"], body["gaps"]) == (
        expected.fit,
        expected.competencies,
        expected.gaps,
    )
    assert body["fit"] is not None
    assert body["fit_overridden"] is False
    assert (body["status"], body["source"], body["slug"]) == (
        "found",
        "manual",
        "mistral-ai-applied-ai-engineer",
    )
    assert body["contract_type"] == "CDI"
    assert body["posting_text"] == POSTING.strip()
    assert [e["kind"] for e in body["events"]] == ["fit", "created"]


def test_add_without_text_or_without_skills_is_unscored(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    body = add(api, alice, company="Acme", title="Stage IA", posting_text=POSTING).json()
    assert body["fit"] is None  # no skills in the profile yet
    assert body["contract_type"] == "Stage"  # read from the title, as the engine did
    give_skills(factory, alice.id)
    body = add(api, alice, company="Acme", title="Other", posting_text="  \n ").json()
    assert (body["fit"], body["posting_text"]) == (None, None)
    body = add(api, alice, company="Acme", title="Third", contract_type="Freelance").json()
    assert (body["fit"], body["contract_type"], body["url"]) == (None, "Freelance", None)


@pytest.mark.parametrize(
    "variant",
    [
        "http://jobs.ashbyhq.com/acme/1",
        "https://www.jobs.ashbyhq.com/acme/1/",
        "https://JOBS.ashbyhq.com/acme/1?utm_source=linkedin&gh_src=x",
        "https://jobs.ashbyhq.com:443/acme/1#apply",
    ],
)
def test_add_duplicate_by_url_variants_is_409(
    api: Api, alice: Signed, factory: sessionmaker[Session], variant: str
) -> None:
    (job,) = seed(factory, alice.id, [{"url": "https://jobs.ashbyhq.com/acme/1"}])
    response = add(api, alice, company="Other", title="Other", url=variant)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This posting is already in your list.",
        "existing_id": str(job.id),
        "existing_slug": job.slug,
    }


def test_add_duplicate_by_company_title_location_is_409(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    (job,) = seed(factory, alice.id, [{"company": "Hugging Face", "title": "ML Engineer"}])
    response = add(api, alice, company="hugging-face", title="ML  engineer", location="PARIS")
    assert response.status_code == 409
    assert response.json()["existing_id"] == str(job.id)
    # Another city is another posting; the same posting in another account is no duplicate.
    assert add(api, alice, company="Hugging Face", title="ML Engineer").status_code == 201
    with factory() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 2


def test_add_free_slug_and_invalid_bodies(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    seed(factory, alice.id, [{"slug": "acme-ai-engineer", "company": "X", "title": "Y"}])
    body = add(api, alice, company="Acme", title="AI Engineer").json()
    assert body["slug"] == "acme-ai-engineer-2"
    for bad in (
        {"company": "", "title": "x"},
        {"company": "x"},
        {"company": "x", "title": "y", "url": "javascript:alert(1)"},
        {"company": "x", "title": "y", "contract_type": "Permanent"},
        {"company": "x", "title": "y", "posting_text": "a" * 1_000_001},
        {"company": "x", "title": "y", "status": "applied"},
    ):
        assert api.post("/api/jobs", token=alice.token, json=bad).status_code == 422, bad


# --- rescore ------------------------------------------------------------------------------------


def rescore(api: Api, who: Signed, **body: Any) -> httpx.Response:
    return api.post("/api/jobs/rescore", token=who.token, json=body)


def test_rescore_counts_and_keeps_manual_fits(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]
) -> None:
    give_skills(factory, alice.id)
    computed = scoring.score(POSTING, SKILLS, title="Role 1").fit
    jobs = seed(
        factory,
        alice.id,
        [
            {"posting_text": POSTING, "fit": None},  # changed: unscored to computed
            {"posting_text": POSTING, "fit": computed},  # unchanged... its competencies refresh
            {"posting_text": POSTING, "fit": 99, "fit_overridden": True},  # manual: kept
            {"posting_text": None, "fit": 50},  # no text: skipped
            {"posting_text": "   ", "fit": 50},  # blank text: skipped
        ],
    )
    (theirs,) = seed(factory, bob.id, [{"posting_text": POSTING, "fit": None}])
    body = rescore(api, alice).json()
    assert body == {
        "scored": 3,
        "changed": 2,
        "unchanged": 0,
        "skipped_manual": 1,
        "skipped_no_text": 2,
        "skipped_busy": 0,
        "remaining": 0,
        "next_after": None,
    }
    assert stored(factory, jobs[2].id).fit == 99
    assert stored(factory, jobs[2].id).fit_overridden is True
    assert stored(factory, theirs.id).fit is None  # another user's rows are never touched
    again = rescore(api, alice).json()
    assert (again["changed"], again["unchanged"], again["skipped_manual"]) == (0, 2, 1)
    overwrite = rescore(api, alice, overwrite_manual=True).json()
    assert (overwrite["changed"], overwrite["skipped_manual"]) == (1, 0)
    assert stored(factory, jobs[2].id).fit_overridden is False
    assert stored(factory, jobs[2].id).fit == scoring.score(POSTING, SKILLS, title="Role 2").fit
    fit_events = [d for k, d in events(factory, jobs[2].id) if k == "fit"]
    assert fit_events[-1]["via"] == "rescore"
    assert fit_events[-1]["was_manual"] is True


def test_rescore_selected_ids_and_missing_ones(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]
) -> None:
    give_skills(factory, alice.id)
    a, b = seed(factory, alice.id, [{"posting_text": POSTING}, {"posting_text": POSTING}])
    (theirs,) = seed(factory, bob.id, [{"posting_text": POSTING}])
    body = rescore(api, alice, ids=[str(a.id)]).json()
    assert (body["scored"], body["changed"]) == (1, 1)
    assert stored(factory, b.id).fit is None
    missing = rescore(api, alice, ids=[str(a.id), str(theirs.id)])
    assert missing.status_code == 404
    assert missing.json()["missing"] == [str(theirs.id)]
    assert rescore(api, alice, ids=[]).status_code == 422


def test_rescore_without_skills_is_409(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    seed(factory, alice.id, [{"posting_text": POSTING}])
    response = rescore(api, alice)
    assert response.status_code == 409
    assert "skills" in response.json()["detail"]


def test_rescore_stops_at_the_time_budget_and_resumes(
    api: Api, alice: Signed, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    give_skills(factory, alice.id)
    seed(factory, alice.id, [{"posting_text": POSTING} for _ in range(25)])
    monkeypatch.setattr(jobs_router, "RESCORE_BATCH", 10)
    monkeypatch.setattr(jobs_router, "RESCORE_BUDGET_S", -1.0)  # spent after the first batch
    first = rescore(api, alice).json()
    assert (first["scored"], first["remaining"]) == (10, 15)
    assert first["next_after"] is not None
    second = rescore(api, alice, after=first["next_after"]).json()
    assert (second["scored"], second["remaining"]) == (10, 5)
    third = rescore(api, alice, after=second["next_after"]).json()
    assert (third["scored"], third["remaining"], third["next_after"]) == (5, 0, None)
    with factory() as db:
        unscored = db.execute(
            sa.select(sa.func.count()).where(Job.user_id == alice.id, Job.fit.is_(None))
        ).scalar_one()
        assert unscored == 0


def test_rescore_row_cap(
    api: Api, alice: Signed, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    give_skills(factory, alice.id)
    seed(factory, alice.id, [{"posting_text": POSTING} for _ in range(7)])
    monkeypatch.setattr(jobs_router, "RESCORE_BATCH", 2)
    monkeypatch.setattr(jobs_router, "RESCORE_MAX_ROWS", 5)
    first = rescore(api, alice).json()
    assert (first["scored"], first["remaining"]) == (5, 2)
    second = rescore(api, alice, after=first["next_after"]).json()
    assert (second["scored"], second["remaining"], second["next_after"]) == (2, 0, None)


def test_rescore_of_an_empty_list(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    give_skills(factory, alice.id)
    body = rescore(api, alice).json()
    assert body["scored"] == 0
    assert (body["remaining"], body["next_after"]) == (0, None)


def test_a_rescore_fit_event_and_the_edit_history(
    api: Api, alice: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    """A manual fit kept by a rescore, then every edit kind shows in the detail, newest first."""
    give_skills(factory, alice.id)
    (job,) = seed(factory, alice.id, [{"posting_text": POSTING}])
    ok_patch(api, alice, job.id, fit=12)
    clock.advance(seconds=1)
    rescore(api, alice)
    clock.advance(seconds=1)
    ok_patch(api, alice, job.id, title="New title")
    clock.advance(seconds=1)
    body = ok_patch(api, alice, job.id, status="applied", notes="sent")
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["note", "status", "edit", "fit"]
    assert body["fit"] == 12
    assert body["events_total"] == 4
    assert body["updated_at"] > (NOW - timedelta(days=1)).isoformat()
