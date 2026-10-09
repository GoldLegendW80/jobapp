"""The /api/jobs routes through the app: list (every filter alone and combined, q, sorts, cursor
pagination across ties, counts and histogram of the filtered set, bad parameters), detail,
file download (mime, disposition, ETag and 304), import over HTTP (limits, refusals,
idempotence), session and CSRF, and isolation between users."""

import hashlib
import json
import uuid
from collections import Counter
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.jobs import service
from app.jobs.models import ContractType, Job, JobEventKind, JobFile, JobStatus
from app.jobs.schemas import IMPORT_HTTP_MAX_BYTES, ImportFile, JobsImportDocument
from tests.identity.api_helpers import Api, make_user
from tests.jobs.conftest import NOW
from tests.jobs.sources import PDF

ALICE = "alice@example.org"
BOB = "bob@example.org"


class Signed:
    def __init__(self, token: str, user_id: uuid.UUID) -> None:
        self.token = token
        self.id = user_id


def sign_in(factory: sessionmaker[Session], api: Api, email: str) -> Signed:
    make_user(factory, email=email, name=email.split("@")[0])
    token = api.login_token(email)
    with factory() as db:
        user_id = db.execute(sa.select(User.id).where(User.email == email)).scalar_one()
    return Signed(token, user_id)


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, BOB)


def seed(factory: sessionmaker[Session], user: uuid.UUID, rows: list[dict[str, Any]]) -> list[Job]:
    """Insert jobs as given (defaults filled), straight in the database."""
    made = []
    with factory() as db, db.begin():
        for index, values in enumerate(rows):
            fields: dict[str, Any] = {
                "slug": f"job-{index}",
                "company": "Acme",
                "title": f"Role {index}",
                "location": "Paris",
                "url": f"https://jobs.example/{user}/{index}",
                "found_on": date(2026, 10, 7),
                "created_at": NOW,
                "updated_at": NOW,
            }
            fields.update(values)
            job = Job(user_id=user, **fields)
            db.add(job)
            made.append(job)
    return made


def listing(api: Api, who: Signed, **params: Any) -> httpx.Response:
    return api.get("/api/jobs", token=who.token, params=params)


def ok(api: Api, who: Signed, **params: Any) -> dict[str, Any]:
    response = listing(api, who, **params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def slugs_of(page: dict[str, Any]) -> list[str]:
    return [item["slug"] for item in page["items"]]


def walk(api: Api, who: Signed, limit: int, **params: Any) -> list[str]:
    """Every slug of the list, page after page."""
    seen: list[str] = []
    cursor = None
    while True:
        extra = {"cursor": cursor} if cursor else {}
        page = ok(api, who, limit=limit, **params, **extra)
        seen.extend(slugs_of(page))
        cursor = page["next_cursor"]
        if cursor is None:
            return seen


# --- list ---------------------------------------------------------------------------------------


def test_empty_list(api: Api, alice: Signed) -> None:
    page = ok(api, alice)
    assert page["items"] == []
    assert (page["next_cursor"], page["total"]) == (None, 0)
    assert page["counts_by_status"] == {s.value: 0 for s in JobStatus}
    assert list(page["counts_by_status"]) == [s.value for s in JobStatus]  # pipeline order
    assert [(b["low"], b["high"], b["count"]) for b in page["fit_histogram"]["buckets"]] == [
        (0, 9, 0),
        (10, 19, 0),
        (20, 29, 0),
        (30, 39, 0),
        (40, 49, 0),
        (50, 59, 0),
        (60, 69, 0),
        (70, 79, 0),
        (80, 89, 0),
        (90, 100, 0),
    ]
    assert page["fit_histogram"]["unscored"] == 0


def test_list_item_fields(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job,) = seed(
        factory,
        alice.id,
        [
            {
                "fit": 77,
                "competencies": ["agents"],
                "gaps": ["rag"],
                "description": "d",
                "posting_text": "SECRET POSTING",
                "legacy": {"jd": "x"},
                "notes": "n",
                "source": "ashby:acme",
                "contract_type": ContractType.VIE,
            }
        ],
    )
    with factory() as db, db.begin():
        db.add(
            JobFile(
                job_id=job.id,
                name="cv.pdf",
                mime="application/pdf",
                content=PDF,
                size=len(PDF),
                sha256=hashlib.sha256(PDF).digest(),
                origin="legacy",
            )
        )
    (item,) = ok(api, alice)["items"]
    assert set(item) == {
        "id", "slug", "company", "title", "location", "contract_type", "url", "source", "status",
        "fit", "fit_overridden", "competencies", "gaps", "description", "published_on",
        "found_on", "applied_on", "notes", "file_count", "created_at", "updated_at",
    }  # fmt: skip
    assert (item["id"], item["contract_type"], item["fit"], item["file_count"]) == (
        str(job.id),
        "VIE",
        77,
        1,
    )
    assert "SECRET POSTING" not in json.dumps(item)


@pytest.fixture
def mixed(factory: sessionmaker[Session], alice: Signed) -> list[Job]:
    """Twelve jobs spread over statuses, types, companies, fits and dates."""
    s, t = JobStatus, ContractType
    table = [
        # company, title, status, fit, found_on, extra
        ("Mistral AI", "Applied AI Engineer", s.found, 95, (10, 7), {"location": "Paris"}),
        ("Mistral AI", "Forward Deployed Engineer", s.shortlisted, 88, (10, 5),
         {"location": "London"}),
        ("Anthropic", "Solutions Architect", s.applied, 100, (8, 31),
         {"location": "Remote", "applied_on": date(2026, 9, 1)}),
        ("Anthropic", "Data Engineer", s.skipped, 40, (9, 29), {"notes": "call back on monday"}),
        ("Société Générale", "Ingénieur IA", s.found, None, (10, 7), {"contract_type": t.VIE}),
        ("Betclic", "ML Engineer", s.found, 9, (10, 1), {"contract_type": t.CDD}),
        ("betclic", "AI Intern", s.closed, 10, (10, 2), {"contract_type": t.Stage}),
        ("OpenAI", "Engineer, Agents", s.interview, 90, (8, 12),
         {"applied_on": date(2026, 8, 31)}),
        ("Cohere", "FDE", s.staged, 89, (10, 7), {}),
        ("Cohere", "Agentic FDE", s.cv_ready, 0, (10, 7), {"contract_type": t.Freelance}),
        ("100% Remote_Co", "Engineer", s.rejected, 55, (9, 30), {}),
        ("Zeta", "Engineer", s.found, None, (10, 6), {}),
    ]  # fmt: skip
    rows = [
        {
            "company": c,
            "title": ti,
            "status": st,
            "fit": f,
            "found_on": date(2026, *d),
            "location": "Lyon",
            **x,
        }
        for c, ti, st, f, d, x in table
    ]
    return seed(factory, alice.id, rows)


def companies(page: dict[str, Any]) -> list[str]:
    return [item["company"] for item in page["items"]]


def test_each_filter_alone(api: Api, alice: Signed, mixed: list[Job]) -> None:
    def total(**params: Any) -> int:
        return ok(api, alice, **params)["total"]

    assert total() == 12
    assert total(status="found") == 4
    assert total(status=["found", "applied"]) == 5
    assert total(status=["found", "found"]) == 4  # repeated values count once
    assert total(type="VIE") == 1
    assert total(type=["CDD", "Stage", "Freelance"]) == 3
    assert total(company="mistral") == 2
    assert total(company="BETCLIC") == 2  # case-insensitive
    assert total(company="100%") == 1  # % and _ are literal
    assert total(company="_") == 1
    assert total(company="  ") == 12  # blank is no filter
    assert total(fit_min=90) == 3
    assert total(fit_max=9) == 2  # 0 and 9
    assert total(fit_min=0) == 10  # unscored left out by any fit bound
    assert total(fit_min=10, fit_max=10) == 1
    assert total(found_from="2026-10-05") == 6
    assert total(found_to="2026-08-31") == 2
    assert total(found_from="2026-10-07", found_to="2026-10-07") == 4


def test_filters_combined(api: Api, alice: Signed, mixed: list[Job]) -> None:
    page = ok(api, alice, status=["found", "shortlisted"], company="mistral", fit_min=90)
    assert companies(page) == ["Mistral AI"]
    assert page["items"][0]["title"] == "Applied AI Engineer"
    page = ok(api, alice, type="CDI", found_from="2026-10-01", fit_max=89, sort="fit")
    assert [i["fit"] for i in page["items"]] == [89, 88]  # Betclic is a CDD, Zeta unscored
    assert ok(api, alice, q="engineer", status="found", type="CDD")["total"] == 1


def test_q_full_text(api: Api, alice: Signed, mixed: list[Job]) -> None:
    def found(q: str) -> set[str]:
        return {f"{i['company']}/{i['title']}" for i in ok(api, alice, q=q)["items"]}

    assert found("anthropic") == {"Anthropic/Solutions Architect", "Anthropic/Data Engineer"}
    assert found("Engineer Paris") == {"Mistral AI/Applied AI Engineer"}
    assert found("monday") == {"Anthropic/Data Engineer"}  # notes are searched
    assert found("remote") == {"Anthropic/Solutions Architect", "100% Remote_Co/Engineer"}
    assert found('"solutions architect"') == {"Anthropic/Solutions Architect"}
    assert found("london or remote") == {
        "Mistral AI/Forward Deployed Engineer",
        "Anthropic/Solutions Architect",
        "100% Remote_Co/Engineer",
    }
    assert found("cohere -agentic") == {"Cohere/FDE"}
    # Accents are kept by the 'simple' configuration: the accented spelling only.
    assert found("ingénieur") == {"Société Générale/Ingénieur IA"}
    assert found("ingenieur") == set()
    assert found("INGÉNIEUR") == {"Société Générale/Ingénieur IA"}  # case is ignored
    assert found("engineering") == set()  # whole words: no stemming
    assert found("!!! & | :*") == set()  # odd input is not an error


def test_counts_ignore_the_status_filter_and_histogram_follows_filters(
    api: Api, alice: Signed, mixed: list[Job]
) -> None:
    page = ok(api, alice)
    assert page["counts_by_status"] == {
        "found": 4, "shortlisted": 1, "cv_ready": 1, "staged": 1, "applied": 1,
        "interview": 1, "rejected": 1, "closed": 1, "skipped": 1,
    }  # fmt: skip
    hist = page["fit_histogram"]
    assert [b["count"] for b in hist["buckets"]] == [2, 1, 0, 0, 1, 1, 0, 0, 2, 3]
    assert hist["unscored"] == 2
    filtered = ok(api, alice, status="found", company="betclic")
    assert filtered["total"] == 1
    # Status counts: the company filter applies, the status filter does not.
    assert filtered["counts_by_status"]["found"] == 1
    assert filtered["counts_by_status"]["closed"] == 1
    assert sum(filtered["counts_by_status"].values()) == 2
    # Histogram: every filter, the status one included.
    assert [b["count"] for b in filtered["fit_histogram"]["buckets"]] == [1] + [0] * 9
    assert filtered["fit_histogram"]["unscored"] == 0
    assert ok(api, alice, status="found")["fit_histogram"]["unscored"] == 2


def test_sorts(api: Api, alice: Signed, mixed: list[Job], factory: sessionmaker[Session]) -> None:
    page = ok(api, alice)  # found_on desc, then fit desc, unscored last within a day
    days = [i["found_on"] for i in page["items"]]
    assert days == sorted(days, reverse=True)
    assert [i["fit"] for i in page["items"][:4]] == [95, 89, 0, None]
    fits = [i["fit"] for i in ok(api, alice, sort="fit")["items"]]
    assert fits == [100, 95, 90, 89, 88, 55, 40, 10, 9, 0, None, None]
    names = companies(ok(api, alice, sort="company"))
    assert names == [
        "100% Remote_Co", "Anthropic", "Anthropic", "betclic", "Betclic", "Cohere", "Cohere",
        "Mistral AI", "Mistral AI", "OpenAI", "Société Générale", "Zeta",
    ]  # fmt: skip
    titles = [i["title"] for i in ok(api, alice, sort="company")["items"][1:3]]
    assert titles == ["Data Engineer", "Solutions Architect"]
    statuses = [i["status"] for i in ok(api, alice, sort="status")["items"]]
    order = [s.value for s in JobStatus]
    assert statuses == sorted(statuses, key=order.index)
    with factory() as db, db.begin():
        job = db.get(Job, mixed[5].id)
        assert job is not None
        job.updated_at = NOW + timedelta(hours=1)
    assert ok(api, alice, sort="updated")["items"][0]["slug"] == mixed[5].slug


@pytest.mark.parametrize("sort", ["found_on", "fit", "company", "status", "updated"])
def test_cursor_pagination_across_ties(
    api: Api, alice: Signed, factory: sessionmaker[Session], sort: str
) -> None:
    # 433 rows with heavy ties on every sort key: 3 dates, 5 fits and unscored, 4 companies,
    # 3 statuses, one updated_at.
    statuses = [JobStatus.found, JobStatus.applied, JobStatus.skipped]
    rows = [
        {
            "company": ["Acme", "acme", "Beta", "Zeta"][i % 4],
            "title": "Engineer" if i % 3 else "engineer",
            "status": statuses[i % 3],
            "fit": None if i % 7 == 0 else (i % 5) * 20,
            "found_on": date(2026, 10, 7) - timedelta(days=i % 3),
        }
        for i in range(433)
    ]
    seed(factory, alice.id, rows)
    full = ok(api, alice, sort=sort, limit=200)
    assert full["total"] == 433
    for limit in (7, 50, 200):
        seen = walk(api, alice, limit, sort=sort)
        assert len(seen) == 433
        assert len(set(seen)) == 433  # no duplicates, no gaps
    # One row per page over a filtered part: every tie boundary is crossed.
    one_by_one = walk(api, alice, 1, sort=sort, company="acme", found_from="2026-10-07")
    assert one_by_one == walk(api, alice, 200, sort=sort, company="acme", found_from="2026-10-07")
    assert len(set(one_by_one)) == len(one_by_one) > 0
    # The pages concatenated are the single ordered listing.
    assert walk(api, alice, 50, sort=sort)[:200] == slugs_of(full)
    # Filters and cursors combine.
    assert len(walk(api, alice, 25, sort=sort, status="applied")) == 144


def test_bad_parameters(api: Api, alice: Signed, mixed: list[Job]) -> None:
    cursor = ok(api, alice, limit=2)["next_cursor"]
    assert cursor
    for params, field in [
        ({"status": "ghosted"}, "status"),
        ({"type": "Permanent"}, "type"),
        ({"fit_min": -1}, "fit_min"),
        ({"fit_max": 101}, "fit_max"),
        ({"fit_min": "high"}, "fit_min"),
        ({"fit_min": 80, "fit_max": 20}, "fit_min"),
        ({"found_from": "07/10/2026"}, "found_from"),
        ({"found_from": "2026-10-07", "found_to": "2026-10-01"}, "found_from"),
        ({"sort": "salary"}, "sort"),
        ({"limit": 0}, "limit"),
        ({"limit": 201}, "limit"),
        ({"cursor": "not-a-cursor"}, "cursor"),
        ({"cursor": "e30"}, "cursor"),  # {} in base64url
        ({"cursor": cursor, "sort": "fit"}, "cursor"),  # made for another sort
        ({"q": "x" * 201}, "q"),
    ]:
        response = listing(api, alice, **params)
        assert response.status_code == 422, (params, response.text)
        detail = response.json()["detail"]
        assert detail.startswith(f"query.{field}"), (params, detail)


def test_forged_cursors_are_refused(api: Api, alice: Signed, mixed: list[Job]) -> None:
    import base64

    def forged(body: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")

    for body in [
        {"s": "found_on", "k": ["2026-10-07", 90]},  # too few values
        {"s": "found_on", "k": ["yesterday", 90, str(uuid.uuid4())]},
        {"s": "found_on", "k": ["2026-10-07", "x", str(uuid.uuid4())]},
        {"s": "found_on", "k": ["2026-10-07", 90, "not-a-uuid"]},
        {"s": "found_on", "k": ["2026-10-07", None, str(uuid.uuid4())]},
        {"s": "found_on", "k": [["2026-10-07"], 90, str(uuid.uuid4())]},
        {"s": "status", "k": ["ghosted", 90, str(uuid.uuid4())]},
        {"s": "found_on", "k": "abc"},
        ["found_on"],
    ]:
        sort = body["s"] if isinstance(body, dict) else "found_on"
        response = listing(api, alice, cursor=forged(body), sort=sort)
        assert response.status_code == 422, body
    # A well-formed cursor of a row that does not exist simply continues after its values.
    after = forged({"s": "fit", "k": [90, "2026-10-07", str(uuid.UUID(int=0))]})
    fits = [i["fit"] for i in ok(api, alice, sort="fit", cursor=after)["items"]]
    assert fits == [90, 89, 88, 55, 40, 10, 9, 0, None, None]


# --- detail and files ---------------------------------------------------------------------------


def _add_file(
    factory: sessionmaker[Session], job: Job, name: str, mime: str, data: bytes
) -> uuid.UUID:
    with factory() as db, db.begin():
        file = JobFile(
            job_id=job.id,
            name=name,
            mime=mime,
            content=data,
            size=len(data),
            sha256=hashlib.sha256(data).digest(),
            origin="legacy",
        )
        db.add(file)
    return file.id


def test_detail(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job,) = seed(factory, alice.id, [{"posting_text": "About the role", "legacy": {"jd": "p"}}])
    file_id = _add_file(factory, job, "cv.pdf", "application/pdf", PDF)
    with factory() as db, db.begin():
        attached = db.get(Job, job.id)
        assert attached is not None
        for minute in range(55):
            service.add_event(db, attached, JobEventKind.note, {"n": minute},
                              NOW + timedelta(minutes=minute))  # fmt: skip
    response = api.get(f"/api/jobs/{job.id}", token=alice.token)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["posting_text"], body["legacy"], body["file_count"]) == (
        "About the role",
        {"jd": "p"},
        1,
    )
    (file,) = body["files"]
    assert file == {
        "id": str(file_id),
        "name": "cv.pdf",
        "mime": "application/pdf",
        "size": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
        "origin": "legacy",
        "created_at": file["created_at"],
    }
    assert "base64" not in json.dumps(body)
    assert "content" not in file
    assert body["events_total"] == 55
    assert [e["data"]["n"] for e in body["events"]] == list(range(54, 4, -1))  # newest 50


def test_file_download(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job,) = seed(factory, alice.id, [{"slug": "acme-ai-engineer"}])
    pdf_id = _add_file(factory, job, "cv.pdf", "application/pdf", PDF)
    json_id = _add_file(factory, job, "answers.json", "application/json", '{"why": "é"}'.encode())
    py_id = _add_file(factory, job, "apply.py", "text/x-python", b"print(1)\n")
    response = api.get(f"/api/jobs/{job.id}/files/{pdf_id}", token=alice.token)
    assert response.status_code == 200
    assert response.content == PDF
    etag = f'"{hashlib.sha256(PDF).hexdigest()}"'
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="acme-ai-engineer-cv.pdf"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["etag"] == etag
    response = api.get(f"/api/jobs/{job.id}/files/{json_id}", token=alice.token)
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["content-disposition"].startswith("attachment;")
    response = api.get(f"/api/jobs/{job.id}/files/{py_id}", token=alice.token)
    assert response.headers["content-type"] == "text/x-python; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="acme-ai-engineer-apply.py"'
    )
    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        again = api.get(
            f"/api/jobs/{job.id}/files/{pdf_id}",
            token=alice.token,
            headers={"If-None-Match": header},
        )
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == etag
    stale = api.get(
        f"/api/jobs/{job.id}/files/{pdf_id}", token=alice.token, headers={"If-None-Match": '"x"'}
    )
    assert stale.status_code == 200


def test_missing_ids_are_404(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    (job, other) = seed(factory, alice.id, [{}, {}])
    file_id = _add_file(factory, other, "cv.pdf", "application/pdf", PDF)
    for path in [
        f"/api/jobs/{uuid.uuid4()}",
        "/api/jobs/not-a-uuid",
        f"/api/jobs/{uuid.uuid4()}/files/{file_id}",
        f"/api/jobs/{job.id}/files/{uuid.uuid4()}",
        f"/api/jobs/{job.id}/files/{file_id}",  # a file of another job
        f"/api/jobs/{job.id}/files/nope",
    ]:
        response = api.get(path, token=alice.token)
        assert response.status_code == 404, path
        assert response.json()["detail"] in ("Job not found.", "File not found.")


def test_isolation(api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]) -> None:
    (mine,) = seed(factory, alice.id, [{"company": "Mine"}])
    (theirs,) = seed(factory, bob.id, [{"company": "Theirs", "status": JobStatus.applied}])
    file_id = _add_file(factory, theirs, "cv.pdf", "application/pdf", PDF)
    page = ok(api, alice)
    assert companies(page) == ["Mine"]
    assert page["total"] == 1
    assert page["counts_by_status"]["applied"] == 0
    assert ok(api, alice, q="theirs")["total"] == 0
    assert api.get(f"/api/jobs/{theirs.id}", token=alice.token).status_code == 404
    assert api.get(f"/api/jobs/{theirs.id}/files/{file_id}", token=alice.token).status_code == 404
    assert api.get(f"/api/jobs/{mine.id}/files/{file_id}", token=alice.token).status_code == 404
    assert api.get(f"/api/jobs/{theirs.id}/files/{file_id}", token=bob.token).status_code == 200


def test_session_and_csrf(api: Api, alice: Signed) -> None:
    for path in ("/api/jobs", f"/api/jobs/{uuid.uuid4()}", f"/api/jobs/{uuid.uuid4()}/files/x"):
        assert api.get(path).status_code == 401
    assert api.post("/api/jobs/import", content=b"{}").status_code == 401
    refused = api.post("/api/jobs/import", token=alice.token, csrf=False, content=b"{}")
    assert refused.status_code == 403


# --- import over HTTP ---------------------------------------------------------------------------


def _document(*jobs: dict[str, Any]) -> bytes:
    return (
        JobsImportDocument.model_validate(
            {"exported_at": NOW.isoformat(), "jobs": list(jobs), "warnings": ["from the converter"]}
        )
        .to_json()
        .encode()
    )


def _post(api: Api, who: Signed, body: bytes, **headers: str) -> httpx.Response:
    return api.post(
        "/api/jobs/import",
        token=who.token,
        content=body,
        headers={"Content-Type": "application/json", **headers},
    )


def test_import_over_http_is_idempotent(api: Api, alice: Signed) -> None:
    cv = ImportFile.of("cv.pdf", PDF).model_dump()
    body = _document(
        {"slug": "acme-x", "company": "Acme", "title": "X", "status": "applied", "files": [cv]},
        {"slug": "beta-y", "company": "Beta", "title": "Y", "fit": 70},
    )
    first = _post(api, alice, body)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "created": 2, "updated": 0, "unchanged": 0, "skipped": 0, "files_added": 1,
        "files_replaced": 0, "files_unchanged": 0, "files_kept": 0,
        "warnings": ["from the converter"],
    }  # fmt: skip
    second = _post(api, alice, body).json()
    assert (second["created"], second["updated"], second["unchanged"]) == (0, 0, 2)
    page = ok(api, alice)
    assert page["total"] == 2
    assert page["counts_by_status"]["applied"] == 1


def test_import_refusals(api: Api, alice: Signed) -> None:
    too_large = b" " * (IMPORT_HTTP_MAX_BYTES + 1)
    response = _post(api, alice, too_large)
    assert response.status_code == 413
    assert "CLI" in response.json()["detail"]
    # Declared too large: refused from the header, before the body is read.
    response = _post(api, alice, b"{}", **{"Content-Length": str(IMPORT_HTTP_MAX_BYTES + 1)})
    assert response.status_code == 413

    def chunks() -> Any:
        for _ in range(11):
            yield b" " * (1024 * 1024)

    streamed = api.post("/api/jobs/import", token=alice.token, content=chunks())
    assert streamed.status_code == 413  # chunked, no Content-Length
    for body, fragment in [
        (b"nope", "not valid JSON"),
        (b'{"format": "jobapp-profile", "version": 1}', "Unknown file format"),
        (b'{"format": "jobapp-jobs", "version": 9}', "Unsupported version"),
    ]:
        response = _post(api, alice, body)
        assert response.status_code == 422
        assert fragment in response.json()["detail"]
    bad = json.loads(_document({"slug": "a-b", "company": "A", "title": "B"}))
    bad["jobs"][0]["status"] = "ghosted"
    response = _post(api, alice, json.dumps(bad).encode())
    assert response.status_code == 422
    assert response.json()["detail"].startswith("jobs.0.status:")
    assert "ghosted" not in response.json()["detail"]
    assert ok(api, alice)["total"] == 0  # nothing written


def test_import_counts_match_status_counts(api: Api, alice: Signed) -> None:
    statuses = [s.value for s in JobStatus]
    jobs = [
        {"slug": f"co-{i}", "company": f"Co {i}", "title": "T", "status": statuses[i % 9]}
        for i in range(40)
    ]
    assert _post(api, alice, _document(*jobs)).status_code == 200
    page = ok(api, alice)
    expected = Counter(j["status"] for j in jobs)
    assert page["counts_by_status"] == {s: expected[s] for s in statuses}
    assert page["total"] == 40
