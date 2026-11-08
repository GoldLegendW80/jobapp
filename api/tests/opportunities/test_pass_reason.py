"""The optional reason of a Pass (SPEC-opportunities.md, "A Pass"; owner, 2026-11-07)."""

import uuid

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.models import JobStatus
from app.opportunities import passes
from tests.identity.api_helpers import Api
from tests.opportunities.conftest import (
    Person,
    add_posting,
    pass_,
    status_events,
    status_of,
    undo,
)


def reason(api: Api, who: Person, posting_id: uuid.UUID | str, value: str) -> httpx.Response:
    return api.request(
        "PUT", f"/api/postings/{posting_id}/pass/reason", token=who.token, json={"reason": value}
    )


def test_the_reason_joins_the_pass_event_and_a_second_one_replaces_it(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    assert pass_(api, person_a, posting).status_code == 200
    first = reason(api, person_a, posting, "pay")
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {"id": str(posting), "reason": "pay"}
    assert reason(api, person_a, posting, "remote").status_code == 200
    assert status_events(factory, posting) == [
        {"from": "found", "to": "skipped", "via": "swipe", "reason": "remote"}
    ]
    assert status_of(factory, posting) is JobStatus.skipped


def test_every_reason_is_taken_and_an_unknown_one_is_a_422(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    pass_(api, person_a, posting)
    for value in passes.REASONS:
        assert reason(api, person_a, posting, value).status_code == 200
    for value in ("", "Pay too low", "other", "PAY"):
        assert reason(api, person_a, posting, value).status_code == 422
    assert status_events(factory, posting)[0]["reason"] == passes.REASONS[-1]


def test_no_reason_without_the_pass_in_force(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    never = add_posting(factory, person_a)
    undone = add_posting(factory, person_a)
    pass_(api, person_a, undone)
    assert undo(api, person_a, undone).status_code == 200
    for posting in (never, undone):
        response = reason(api, person_a, posting, "pay")
        assert response.status_code == 409
        assert response.json()["code"] == "changed"
    assert all("reason" not in data for data in status_events(factory, undone))


def test_the_undo_after_a_reason_still_puts_the_posting_back(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a, status=JobStatus.shortlisted)
    pass_(api, person_a, posting)
    reason(api, person_a, posting, "stack")
    assert undo(api, person_a, posting).status_code == 200
    assert status_of(factory, posting) is JobStatus.shortlisted


@pytest.mark.parametrize("posting_id", ["nope", "5b1f0e2a-3c4d-4e5f-8a9b-0c1d2e3f4a5b"])
def test_an_unknown_posting_is_a_404(api: Api, person_a: Person, posting_id: str) -> None:
    assert reason(api, person_a, posting_id, "pay").status_code == 404


def test_b_cannot_give_a_reason_to_a_s_pass(
    api: Api, person_a: Person, person_b: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    pass_(api, person_a, posting)
    response = reason(api, person_b, posting, "pay")
    assert response.status_code == 404
    assert response.json() == {"detail": "Posting not found."}
    assert "reason" not in status_events(factory, posting)[0]


def test_the_route_needs_a_session_and_the_csrf_header(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    pass_(api, person_a, posting)
    path = f"/api/postings/{posting}/pass/reason"
    assert api.request("PUT", path, json={"reason": "pay"}).status_code == 401
    refused = api.request("PUT", path, token=person_a.token, csrf=False, json={"reason": "pay"})
    assert refused.status_code == 403
    assert "reason" not in status_events(factory, posting)[0]
