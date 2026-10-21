"""Opportunities O3b (issue #504, follow-up 5 of the re-check of PR #498): the jobs import
document carries pay_min, pay_max, pay_currency, pay_period and work_mode.

The import checks them exactly as PATCH does (the same table through PayIn and through
ImportJob, and the same refusals over HTTP); a row that carries them sets them, nulls remove
them, a row that does not carry them keeps the stored values (a document written before they
existed imports as before); a second run changes nothing; the document's JSON writes them only
when a row carries them. Everything is invented."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import service
from app.jobs.models import Job, JobEvent, JobEventKind, PayPeriod, WorkMode
from app.jobs.schemas import (
    ImportJob,
    JobPatchIn,
    JobsImportDocument,
    describe_validation_error,
    parse_document,
)
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, ok, sign_in

BASE: dict[str, Any] = {"slug": "acme-ai-engineer", "company": "Acme", "title": "AI Engineer"}
PAY = {"pay_min": 45000, "pay_max": 55000, "pay_currency": "EUR", "pay_period": "year"}


def item(**fields: Any) -> ImportJob:
    return ImportJob.model_validate({**BASE, **fields})


def run(db: Session, user: uuid.UUID, items: list[ImportJob], later: int = 0) -> Any:
    summary = service.import_jobs(db, user, items, NOW + timedelta(minutes=later))
    db.commit()
    return summary


def stored(db: Session, user: uuid.UUID) -> tuple[Any, ...]:
    job = db.execute(service.jobs_of(user)).scalar_one()
    db.refresh(job)
    return (job.pay_min, job.pay_max, job.pay_currency, job.pay_period, job.work_mode)


# --- the same checks as PATCH -------------------------------------------------------------------

# (min, max, currency, period): accepted by PATCH's PayIn or not; the import must agree.
PAYS: list[tuple[Any, Any, Any, Any]] = [
    (45000, 55000, "EUR", "year"),
    (600, 600, "EUR", "day"),
    (1, 1, "USD", "hour"),
    (10_000_000, 10_000_000, "GBP", "month"),
    (2000, 2000, "EUR", "month"),
    (55000, 45000, "EUR", "year"),  # reversed
    (0, 100, "EUR", "year"),  # under 1
    (1, 10_000_001, "EUR", "year"),  # over 10 million
    (-5, 100, "EUR", "year"),
    (45000.0, 55000, "EUR", "year"),  # strict: a float is no whole number
    (45000.5, 55000, "EUR", "year"),
    ("45000", 55000, "EUR", "year"),  # strict: a string is no figure
    (True, 55000, "EUR", "year"),
    (45000, 55000, "eur", "year"),  # a code is written in capitals
    (45000, 55000, "XYZ", "year"),
    (45000, 55000, 978, "year"),
    (45000, 55000, "EUR", "week"),
    (45000, 55000, "EUR", "YEAR"),
    (45000, 55000, "EUR", 1),
    (10**400, 10**400, "EUR", "year"),
]


def _patch_accepts(low: Any, high: Any, currency: Any, period: Any) -> bool:
    body = {"pay": {"min": low, "max": high, "currency": currency, "period": period}}
    try:
        JobPatchIn.model_validate(body)
    except ValidationError:
        return False
    return True


def _import_accepts(low: Any, high: Any, currency: Any, period: Any) -> bool:
    fields = {"pay_min": low, "pay_max": high, "pay_currency": currency, "pay_period": period}
    try:
        item(**fields)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(("low", "high", "currency", "period"), PAYS)
def test_the_import_checks_a_pay_as_patch_does(
    low: Any, high: Any, currency: Any, period: Any
) -> None:
    assert _import_accepts(low, high, currency, period) == _patch_accepts(
        low, high, currency, period
    )


def test_the_table_holds_both_outcomes() -> None:
    outcomes = [_patch_accepts(*row) for row in PAYS]
    assert outcomes.count(True) == 5
    assert outcomes.count(False) == len(PAYS) - 5


@pytest.mark.parametrize(
    "fields",
    [
        {"pay_min": 45000},
        {"pay_min": 45000, "pay_max": 55000, "pay_currency": "EUR"},
        {"pay_currency": "EUR", "pay_period": "year"},
        {**PAY, "pay_period": None},
        # Keys, not values (review of #513, follow-up 4): some keys carried, even all null, is
        # refused; only all four keys or none.
        {"pay_min": None},
        {"pay_min": None, "pay_max": None},
        {"pay_min": None, "pay_max": None, "pay_currency": None},
    ],
)
def test_a_partial_pay_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="go together"):
        item(**fields)


@pytest.mark.parametrize(
    ("mode", "accepted"),
    [("remote", True), ("hybrid", True), ("office", True), (None, True), ("Remote", False),
     ("on_site", False), ("", False), (1, False)],
)  # fmt: skip
def test_the_work_mode_is_checked_as_patch_does(mode: Any, accepted: bool) -> None:
    def accepts(build: Any) -> bool:
        try:
            build()
        except ValidationError:
            return False
        return True

    assert accepts(lambda: item(work_mode=mode)) is accepted
    assert accepts(lambda: JobPatchIn.model_validate({"work_mode": mode})) is accepted


def test_a_refusal_names_the_place_never_the_value() -> None:
    raw = json.dumps(
        {"format": "jobapp-jobs", "version": 1, "exported_at": NOW.isoformat(),
         "jobs": [{**BASE, **PAY, "pay_currency": "QQQ"}]}
    ).encode()  # fmt: skip
    with pytest.raises(ValidationError) as caught:
        parse_document(raw, 10_000)
    message = describe_validation_error(caught.value)
    assert message.startswith("jobs.0: pay: currency: ")
    assert "QQQ" not in message


# --- the document -------------------------------------------------------------------------------


def test_the_json_carries_them_only_when_a_row_does() -> None:
    document = JobsImportDocument(
        exported_at=NOW,
        jobs=[
            item(**PAY, work_mode="hybrid"),
            item(slug="beta-ml", company="Beta", title="ML"),
            item(slug="gamma-ops", company="Gamma", title="Ops", work_mode=None),
        ],
    )
    rows = json.loads(document.to_json())["jobs"]
    assert {k: rows[0][k] for k in (*PAY, "work_mode")} == {**PAY, "work_mode": "hybrid"}
    assert not {*PAY, "work_mode"} & set(rows[1])  # an old style row: none written
    assert rows[2]["work_mode"] is None
    assert not set(PAY) & set(rows[2])
    again = parse_document(document.to_json().encode(), 10_000_000)
    assert again.jobs[0].pay_period is PayPeriod.year
    assert again.jobs[0].work_mode is WorkMode.hybrid
    assert (again.jobs[1].carries_pay, again.jobs[1].carries_work_mode) == (False, False)
    assert (again.jobs[2].carries_pay, again.jobs[2].carries_work_mode) == (False, True)


def test_a_document_written_before_the_fields_still_parses() -> None:
    raw = json.dumps(
        {"format": "jobapp-jobs", "version": 1, "exported_at": NOW.isoformat(), "jobs": [BASE]}
    ).encode()
    (job,) = parse_document(raw, 10_000).jobs
    assert (job.pay_min, job.work_mode, job.carries_pay, job.carries_work_mode) == (
        None,
        None,
        False,
        False,
    )


# --- the import ---------------------------------------------------------------------------------


def test_a_new_row_gets_the_pay_and_the_work_mode(db: Session, alice: uuid.UUID) -> None:
    summary = run(db, alice, [item(**PAY, work_mode="remote")])
    assert summary.created == 1
    assert stored(db, alice) == (45000, 55000, "EUR", PayPeriod.year, WorkMode.remote)


def test_a_second_run_changes_nothing(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(**PAY, work_mode="remote")])
    summary = run(db, alice, [item(**PAY, work_mode="remote")], later=5)
    assert (summary.created, summary.updated, summary.unchanged) == (0, 0, 1)


def test_an_update_sets_them_with_an_event(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item()])
    assert stored(db, alice) == (None,) * 5
    new_pay = {**PAY, "pay_period": "month", "pay_min": 3000, "pay_max": 3500}
    summary = run(db, alice, [item(**new_pay, work_mode="office")], later=5)
    assert summary.updated == 1
    assert stored(db, alice) == (3000, 3500, "EUR", PayPeriod.month, WorkMode.office)
    query = sa.select(JobEvent).where(JobEvent.kind == JobEventKind.import_)
    (event,) = db.execute(query).scalars()
    assert event.data["changed"] == [
        "pay_currency",
        "pay_max",
        "pay_min",
        "pay_period",
        "work_mode",
    ]


def test_a_row_without_them_keeps_the_stored_values(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(**PAY, work_mode="hybrid")])
    summary = run(db, alice, [item()], later=5)  # a document written before O3
    assert (summary.updated, summary.unchanged) == (0, 1)
    assert stored(db, alice) == (45000, 55000, "EUR", PayPeriod.year, WorkMode.hybrid)
    # Only the pay carried: the work mode stays, and the other way round.
    run(db, alice, [item(**{**PAY, "pay_currency": "GBP"})], later=10)
    assert stored(db, alice) == (45000, 55000, "GBP", PayPeriod.year, WorkMode.hybrid)
    run(db, alice, [item(work_mode="remote")], later=15)
    assert stored(db, alice) == (45000, 55000, "GBP", PayPeriod.year, WorkMode.remote)


def test_nulls_remove_them(db: Session, alice: uuid.UUID) -> None:
    run(db, alice, [item(**PAY, work_mode="hybrid")])
    nulls = {name: None for name in PAY}
    summary = run(db, alice, [item(**nulls, work_mode=None)], later=5)
    assert summary.updated == 1
    assert stored(db, alice) == (None,) * 5


# --- over HTTP ----------------------------------------------------------------------------------


@pytest.fixture
def person(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-o3b@example.org")


def _document(*jobs: dict[str, Any]) -> bytes:
    return json.dumps(
        {"format": "jobapp-jobs", "version": 1, "exported_at": NOW.isoformat(), "jobs": list(jobs)}
    ).encode()


def _post(api: Api, who: Signed, body: bytes) -> Any:
    return api.post(
        "/api/jobs/import",
        token=who.token,
        content=body,
        headers={"Content-Type": "application/json"},
    )


def test_the_route_imports_them_and_the_list_shows_them(api: Api, person: Signed) -> None:
    response = _post(api, person, _document({**BASE, **PAY, "work_mode": "remote"}))
    assert response.status_code == 200, response.text
    (row,) = ok(api, person)["items"]
    assert {k: row[k] for k in (*PAY, "work_mode")} == {**PAY, "work_mode": "remote"}


@pytest.mark.parametrize(
    ("fields", "where"),
    [
        ({**PAY, "pay_min": 60000}, "jobs.0: pay: min must be at most max"),
        ({**PAY, "pay_min": 45000.0}, "jobs.0: pay: min:"),
        ({**PAY, "pay_max": 20_000_000}, "jobs.0: pay: max:"),
        ({**PAY, "pay_currency": "eur"}, "jobs.0: pay: currency:"),
        ({**PAY, "pay_period": "week"}, "jobs.0: pay: period:"),
        ({"pay_min": 45000}, "jobs.0: pay_min, pay_max, pay_currency and pay_period go together"),
        ({"work_mode": "anywhere"}, "jobs.0.work_mode:"),
    ],
)
def test_the_route_refuses_what_patch_refuses(
    api: Api,
    person: Signed,
    factory: sessionmaker[Session],
    fields: dict[str, Any],
    where: str,
) -> None:
    response = _post(api, person, _document({**BASE, **fields}))
    assert response.status_code == 422
    assert response.json()["detail"].startswith(where), response.json()["detail"]
    assert ok(api, person)["total"] == 0  # nothing written
    # PATCH refuses the same value on a stored row.
    with factory() as db, db.begin():
        job = Job(user_id=person.id, slug="x-y", company="X", title="Y", location="")
        db.add(job)
        db.flush()
        job_id = job.id
    sent = fields if "pay_currency" not in fields and "pay_max" not in fields else {**PAY, **fields}
    pay = {k.removeprefix("pay_"): v for k, v in sent.items() if k.startswith("pay_")}
    body = {"work_mode": fields["work_mode"]} if "work_mode" in fields else {"pay": pay}
    patched = api.request("PATCH", f"/api/jobs/{job_id}", token=person.token, json=body)
    assert patched.status_code == 422
