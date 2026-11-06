"""Apply to the selected postings (owner, 2026-11-06: "in the job list and map, i want being able
with the multi select to apply at all selected"), on a real Postgres at head through the routes:
the confirmation's read (`POST /api/apply/bulk/preview`) names what one click would start, with
the documents written first and their estimate, and each posting it would not start with the
sentence of its own Apply; the click (`POST /api/apply/bulk`) starts exactly the postings named,
each through the path of its own Apply click (`likes.on_apply`), one transaction each, and
leaves out a posting that changed or became ineligible since. Every account is
`e2e-*@example.org`; the postings are invented ("Example Robotics" on the fake Ashby). No browser,
no model, no company.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import bulk_router, likes
from app.apply.caps_models import ApplySettings
from app.apply.models import Application, Mode
from app.documents.models import GenerationTrigger, Kind
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import events
from tests.apply.test_a6_gate import _password
from tests.apply.test_a12_likes import (
    OWNER_RULE,
    api_app,
    applications,
    form,
    generations,
    never_apply,
    posting,
    stub,
)
from tests.identity.api_helpers import Api

__all__ = ["api_app", "stub"]

PREVIEW = "/api/apply/bulk/preview"
START = "/api/apply/bulk"
LINKEDIN = "https://www.linkedin.com/jobs/view/4012345/"


def review_box(db: Session, person: Person, ticked: bool) -> None:
    found = db.get(ApplySettings, person.user_id)
    if found is None:
        db.add(ApplySettings(user_id=person.user_id, updated_at=AT, review_before_send=ticked))
    else:
        found.review_before_send = ticked
    db.commit()


class Signed:
    """One signed in person on the api."""

    def __init__(self, client: TestClient, person: Person) -> None:
        self.api = Api(client)
        self.token = self.api.login_token(person.email)

    def preview(self, *ids: uuid.UUID) -> dict[str, Any]:
        response = self.api.post(
            PREVIEW, token=self.token, json={"posting_ids": [str(one) for one in ids]}
        )
        assert response.status_code == 200, response.text
        return response.json()

    def start(self, shown: dict[str, Any], *, drop: tuple[str, ...] = ()) -> Any:
        """The click on "Apply to N": the items as the confirmation showed them, less `drop`."""
        items = [
            {key: one[key] for key in ("posting_id", "seen", "documents")}
            for one in shown["items"]
            if one["posting_id"] not in drop
        ]
        return self.api.post(
            START,
            token=self.token,
            json={"review_before_send": shown["review_before_send"], "items": items},
        )


@pytest.fixture
def spied(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Each posting the click hands to the Apply button's own start, in order."""
    calls: list[uuid.UUID] = []
    real = likes.on_apply

    def on_apply(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, *a: Any, **kw: Any):
        calls.append(posting_id)
        return real(db, user_id, posting_id, *a, **kw)

    monkeypatch.setattr(likes, "on_apply", on_apply)
    return calls


def names(listed: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(one["title"], one["code"]) for one in listed]


def test_the_confirmation_names_each_posting_and_why_some_are_left_out(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a)
    user = person_a.user_id
    never_apply(db, user, OWNER_RULE)
    ready = posting(db, user, form(stub), title="Robotics Engineer")
    bare = posting(db, user, form(stub), title="ML Engineer", kinds=())
    letter = posting(db, user, form(stub), title="Data Engineer", kinds=(Kind.cv,))
    applied = posting(db, user, form(stub), title="Platform Engineer", status="applied")
    cyber = posting(db, user, form(stub), title="Cybersecurity Engineer")
    linked = posting(db, user, LINKEDIN, title="Perception Engineer")
    nowhere = posting(db, user, None, title="Controls Engineer")
    # One Ashby form under two addresses: its key is the job id, wherever it is shown.
    same = form(stub)
    first = posting(db, user, same, title="Fleet Engineer")
    twin = posting(db, user, same.removesuffix("/application"), title="Fleet Engineer II")
    theirs = posting(db, person_b.user_id, form(stub), title="Their Engineer")
    ids = (ready, bare, letter, applied, cyber, linked, nowhere, first, twin, theirs)
    with TestClient(api_app, follow_redirects=False) as client:
        shown = Signed(client, person_a).preview(*ids)

    assert shown["review_before_send"] is False
    assert shown["limit"] == bulk_router.LIMIT == 25
    items = {one["title"]: one for one in shown["items"]}
    assert list(items) == ["Robotics Engineer", "ML Engineer", "Data Engineer", "Fleet Engineer"]
    assert {one["company"] for one in shown["items"]} == {"Example Robotics"}
    # What each click writes first, with the estimate the click stores on the generation.
    assert items["Robotics Engineer"]["documents"] == []
    assert items["Robotics Engineer"]["estimate"] is None
    assert items["ML Engineer"]["documents"] == ["cv", "letter"]
    assert items["Data Engineer"]["documents"] == ["letter"]
    for title in ("ML Engineer", "Data Engineer"):
        tokens = items[title]["estimate"]["tokens"]
        assert 0 < tokens["low"] < tokens["high"]
        # No model key: tokens only, as the documents card says them on a subscription.
        assert items[title]["estimate"]["cost"] is None
    total = shown["estimate"]["tokens"]
    written = ("ML Engineer", "Data Engineer")
    assert total["high"] == sum(items[t]["estimate"]["tokens"]["high"] for t in written)
    assert not any(one["email"] for one in shown["items"])
    # Each one left out says why in the app's words, those of its own Apply.
    assert names(shown["left_out"]) == [
        ("Platform Engineer", "already_applied"),
        ("Cybersecurity Engineer", "never_apply_role"),
        ("Perception Engineer", "linkedin_off"),
        ("Controls Engineer", "no_address"),
        ("Fleet Engineer II", "same_form"),
        (None, "not_found"),
    ]
    detail = {one["code"]: one["detail"] for one in shown["left_out"]}
    assert detail["already_applied"] == "This posting is marked as applied."
    assert detail["linkedin_off"].startswith("LinkedIn is off for you")
    assert detail["not_found"] == "This posting is no longer in your list."
    assert all(" - " not in one and "—" not in one for one in detail.values())
    assert shown["beyond"] == []
    # Nothing was written: no application, no generation.
    assert applications(db, user) == []
    assert generations(db, bare) == []


def test_the_click_starts_exactly_the_postings_named_each_through_its_own_apply(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
    spied: list[uuid.UUID],
) -> None:
    _password(factory, person_a)
    user = person_a.user_id
    ready = posting(db, user, form(stub), title="Robotics Engineer")
    bare = posting(db, user, form(stub), title="ML Engineer", kinds=())
    unnamed = posting(db, user, form(stub), title="Data Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(ready, bare, unnamed)
        # The person's click names two of the three: the third is not part of the approval.
        answer = person.start(shown, drop=(str(unnamed),))

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["started"], body["left_out"]) == (2, 0)
    assert [one["posting_id"] for one in body["results"]] == [str(ready), str(bare)]
    assert all(one["started"] and one["mode"] == "direct" for one in body["results"])
    assert spied == [ready, bare]
    made = {one.posting_id: one for one in applications(db, user)}
    assert set(made) == {ready, bare}
    for found in made.values():
        assert found.mode is Mode.direct
        kinds = [e.kind for e in events(db, found.id)]
        # The events of one Apply click: its start, and the click as the approval.
        assert kinds[:1] == ["created"]
        assert "apply_click" in kinds
    # The posting without documents: written first, by a click, before anything is filled.
    asked = [e.data for e in events(db, made[bare].id) if e.kind == "documents_asked"]
    assert asked == [{"kinds": ["cv", "letter"]}]
    [(kind, generation)] = generations(db, bare)
    assert (kind, generation.trigger) == (Kind.cv, GenerationTrigger.click)
    assert [e.kind for e in events(db, made[ready].id)].count("documents_asked") == 0


def test_review_first_is_unchanged_and_a_changed_review_box_starts_nothing(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a)
    user = person_a.user_id
    review_box(db, person_a, True)
    one = posting(db, user, form(stub), title="Robotics Engineer")
    two = posting(db, user, form(stub), title="ML Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(one)
        assert shown["review_before_send"] is True
        answer = person.start(shown)
        assert answer.status_code == 200, answer.text
        [found] = applications(db, user)
        assert found.mode is Mode.review
        assert "apply_click" not in [e.kind for e in events(db, found.id)]
        # A20: as from its own Apply with the box ticked, the apply agent fills it and the
        # person confirms its list on the job page.
        from app.apply import applier, confirm

        assert confirm.review_agent(db, found)
        assert applier.runs_agent(db, found)

        # The box unticked after the confirmation opened: what it said is no longer what the
        # click does, so nothing starts.
        later = person.preview(two)
        review_box(db, person_a, False)
        refused = person.start(later)
    assert refused.status_code == 409
    assert refused.json() == {
        "detail": bulk_router.SETTINGS_CHANGED,
        "code": "settings_changed",
    }
    assert refused.headers["cache-control"] == "no-store"
    assert [a.posting_id for a in applications(db, user)] == [one]


def test_a_posting_that_changed_or_became_ineligible_since_is_left_out_alone(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
    spied: list[uuid.UUID],
) -> None:
    _password(factory, person_a)
    user = person_a.user_id
    renamed = posting(db, user, form(stub), title="Robotics Engineer")
    moved = posting(db, user, form(stub), title="Perception Engineer")
    applied = posting(db, user, form(stub), title="Platform Engineer")
    lost = posting(db, user, form(stub), title="Data Engineer")
    fine = posting(db, user, form(stub), title="ML Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(renamed, moved, applied, lost, fine)
        assert all(one["documents"] == [] for one in shown["items"])
        # After the confirmation opened: a new title, a new form address, marked as applied, and
        # the CV gone, so the click would write what the confirmation never priced.
        db.execute(sa.text("UPDATE jobs SET title = 'Robotics Lead' WHERE id = :p"), {"p": renamed})
        db.execute(sa.text("UPDATE jobs SET url = :u WHERE id = :p"), {"u": form(stub), "p": moved})
        db.execute(sa.text("UPDATE jobs SET status = 'applied' WHERE id = :p"), {"p": applied})
        db.execute(
            sa.text("DELETE FROM documents WHERE posting_id = :p AND kind = 'cv'"), {"p": lost}
        )
        db.commit()
        answer = person.start(shown)

    assert answer.status_code == 200, answer.text
    results = {one["posting_id"]: one for one in answer.json()["results"]}
    assert [results[str(p)]["code"] for p in (renamed, moved, applied, lost, fine)] == [
        "changed",
        "changed",
        "already_applied",
        "changed",
        None,
    ]
    assert results[str(applied)]["detail"] == "This posting is marked as applied."
    assert results[str(renamed)]["detail"] == bulk_router.SENTENCES["changed"]
    assert results[str(fine)]["started"] is True
    assert [a.posting_id for a in applications(db, user)] == [fine]
    # Only the one still as shown reached the Apply button's start; nothing was written for the
    # posting whose CV is gone.
    assert spied == [fine]
    assert generations(db, lost) == []


def test_documents_that_came_meanwhile_are_no_reason_to_leave_one_out(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    """The confirmation priced the CV and the letter; written by themselves since, the click
    spends less than it said, and goes."""
    from tests.apply.test_apply_writes_documents import write

    _password(factory, person_a)
    user = person_a.user_id
    bare = posting(db, user, form(stub), title="ML Engineer", kinds=())
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(bare)
        assert shown["items"][0]["documents"] == ["cv", "letter"]
        write(db, user, bare, Kind.cv)
        write(db, user, bare, Kind.letter)
        answer = person.start(shown)
    assert answer.json()["started"] == 1
    [found] = applications(db, user)
    assert "documents_asked" not in [e.kind for e in events(db, found.id)]


def test_at_most_25_a_click_the_others_said_and_the_click_bounded(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a)
    user = person_a.user_id
    ids = [posting(db, user, form(stub), title=f"Robotics Engineer {n}") for n in range(27)]
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(*ids)
        assert [one["posting_id"] for one in shown["items"]] == [str(p) for p in ids[:25]]
        assert [one["posting_id"] for one in shown["beyond"]] == [str(p) for p in ids[25:]]
        assert shown["beyond"][0]["title"] == "Robotics Engineer 25"
        assert shown["left_out"] == []
        # 26 named postings are refused whole: the bound is the server's, not the page's.
        items = [
            {"posting_id": one["posting_id"], "seen": one["seen"], "documents": []}
            for one in shown["items"]
        ]
        extra = {"posting_id": str(ids[25]), "seen": "a" * 64, "documents": []}
        too_many = person.api.post(
            START,
            token=person.token,
            json={"review_before_send": False, "items": [*items, extra]},
        )
        twice = person.api.post(
            START,
            token=person.token,
            json={"review_before_send": False, "items": [items[0], items[0]]},
        )
    assert too_many.status_code == 422
    assert twice.status_code == 422
    assert applications(db, user) == []


def test_another_persons_posting_reads_as_gone_and_starts_nothing(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a, person_b)
    theirs = posting(db, person_a.user_id, form(stub), title="Robotics Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        owner = Signed(client, person_a)
        shown = owner.preview(theirs)
        other = Signed(client, person_b)
        peek = other.preview(theirs)
        answer = other.start(shown)
    assert peek["items"] == []
    assert peek["left_out"][0]["company"] is None
    assert "Robotics Engineer" not in str(peek)
    assert answer.json()["results"] == [
        {
            "posting_id": str(theirs),
            "started": False,
            "application_id": None,
            "mode": None,
            "state": None,
            "code": "not_found",
            "detail": "This posting is no longer in your list.",
        }
    ]
    assert applications(db, person_a.user_id) == []
    assert applications(db, person_b.user_id) == []


def test_the_same_role_twice_in_one_click_applies_once(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    """The same job found on two sites, both selected: the first starts, the second is left out
    with the Like's sentence, at the confirmation and again at the click."""
    _password(factory, person_a)
    user = person_a.user_id
    one = posting(db, user, form(stub), title="Applied AI Engineer (H/F)")
    two = posting(db, user, form(stub), title="Applied AI Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        person = Signed(client, person_a)
        shown = person.preview(one, two)
        assert names(shown["left_out"]) == [("Applied AI Engineer", "same_role_live")]
        # A click that names both anyway (a confirmation read before the first started).
        both = person.preview(two)
        answer = person.api.post(
            START,
            token=person.token,
            json={
                "review_before_send": False,
                "items": [
                    {key: x[key] for key in ("posting_id", "seen", "documents")}
                    for x in (shown["items"][0], both["items"][0])
                ],
            },
        )
    codes = [x["code"] for x in answer.json()["results"]]
    assert codes == [None, "same_role_live"]
    assert [a.posting_id for a in applications(db, user)] == [one]
    assert isinstance(applications(db, user)[0], Application)
