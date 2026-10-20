"""O1 on a real Postgres at head: the deck (what is in it, its order, its pages, its filters, its
cards) and the Pass with its undo, through the routes, for two people A and B."""

import base64
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import guardrails
from app.apply.models import Application, State
from app.jobs import service as jobs_service
from app.jobs.models import ContractType, Job, JobStatus
from app.jobs.sweep import NeverApplyRule
from app.opportunities import deck as decks
from app.opportunities import passes
from app.profile.models import Profile
from tests.apply.test_a2_guardrails import KEPT_BY_OWNER, REFUSED_BY_OWNER
from tests.identity.api_helpers import Api
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import set_profile
from tests.jobs.test_status_lock import join_all, run_in_thread, wait_until_blocked
from tests.opportunities.conftest import (
    AT,
    FOUND_ON,
    OWNER_RULE,
    Person,
    add_application,
    add_posting,
    deck_ids,
    deck_search,
    get_deck,
    pass_,
    status_events,
    status_of,
    swept,
    undo,
    walk,
)

# --- what is in the deck -------------------------------------------------------------------------


def test_an_empty_deck(api: Api, person_a: Person) -> None:
    response = get_deck(api, person_a)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "cards": [],
        "next_cursor": None,
        "left": 0,
        "mode": "review",
        "min_fit": 70,
        "rules_unreadable": False,
    }


def test_a_deck_of_one_card_with_its_fields(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(
        factory,
        person_a,
        title="Applied AI Engineer",
        location="Remote, Europe",
        contract_type=ContractType.Freelance,
        competencies=["python", "llm evaluation", "fastapi", "agents", "kubernetes"],
        gaps=["kubernetes", "6+ years"],
    )
    body = get_deck(api, person_a).json()
    assert (body["left"], body["next_cursor"]) == (1, None)
    assert body["cards"] == [
        {
            "id": str(posting),
            "title": "Applied AI Engineer",
            "company": "Example Robotics",
            "location": "Remote, Europe",
            "contract_type": "Freelance",
            "source": "ashby:fake-ok",
            "url": body["cards"][0]["url"],
            "fit": 80,
            "found_on": FOUND_ON.isoformat(),
            "reason": {
                "matches": ["python", "llm evaluation", "fastapi"],
                "gap": "kubernetes",
                "text": "Matches: python, llm evaluation, fastapi. Gap: kubernetes.",
            },
            "pay": None,
            "work_mode": None,
        }
    ]


@pytest.mark.parametrize(
    ("competencies", "gaps", "expected"),
    [
        ([], [], ((), None, None)),
        ([], ["kubernetes"], ((), "kubernetes", "Gap: kubernetes.")),
        (["python"], [], (("python",), None, "Matches: python.")),
        (["python", "go"], ["go"], (("python",), "go", "Matches: python. Gap: go.")),
        (["go"], ["go"], ((), "go", "Gap: go.")),
        (["a", "a", "b", "c", "d"], [], (("a", "b", "c"), None, "Matches: a, b, c.")),
        (["", "a"], ["", "4+ years"], (("a",), "4+ years", "Matches: a. Gap: 4+ years.")),
    ],
)
def test_the_match_reason(competencies: list[str], gaps: list[str], expected: tuple) -> None:
    reason = decks.match_reason(competencies, gaps)
    assert (reason.matches, reason.gap, reason.text) == expected


def test_the_mode_in_force_is_apply_s_mode_after_like(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    with factory() as session, session.begin():
        session.execute(
            sa.text(
                "INSERT INTO apply_settings (user_id, mode_after_like, updated_at) "
                "VALUES (:u, 'full_auto', now())"
            ),
            {"u": person_a.user_id},
        )
    assert get_deck(api, person_a).json()["mode"] == "full_auto"


def test_statuses_and_applications_that_keep_a_posting_out(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    kept = {
        add_posting(factory, person_a, status=JobStatus.found),
        add_posting(factory, person_a, status=JobStatus.shortlisted),
    }
    for status in set(JobStatus) - {JobStatus.found, JobStatus.shortlisted}:
        add_posting(factory, person_a, status=status)
    for state in State:
        posting = add_posting(factory, person_a)
        add_application(factory, person_a, posting, state)
        if state is State.cancelled:
            kept.add(posting)  # a cancelled Like gives the posting back
    assert set(deck_ids(api, person_a)) == {str(p) for p in kept}


def test_a_cancelled_application_beside_a_live_one_keeps_the_posting_out(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    add_application(factory, person_a, posting, State.cancelled)
    add_application(factory, person_a, posting, State.queued)
    assert deck_ids(api, person_a) == []


def test_the_threshold_and_a_posting_without_a_fit(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    below = add_posting(factory, person_a, fit=69)
    at = add_posting(factory, person_a, fit=70)
    no_fit_no_text = add_posting(factory, person_a, fit=None, posting_text=None)
    no_fit_blank_text = add_posting(factory, person_a, fit=None, posting_text="  \n ")
    no_fit_with_text = add_posting(factory, person_a, fit=None, posting_text="Agents and Python.")
    ids = deck_ids(api, person_a)
    assert ids == [str(at), *sorted([str(no_fit_no_text), str(no_fit_blank_text)])]
    assert str(below) not in ids
    assert str(no_fit_with_text) not in ids

    set_profile(factory, person_a.user_id, deck_search(min_fit=69))
    body = get_deck(api, person_a).json()
    assert body["min_fit"] == 69
    assert str(below) in [card["id"] for card in body["cards"]]


@pytest.mark.parametrize("title", [*REFUSED_BY_OWNER, "Secu­rity Engineer", "Сybersecurity"])
def test_a_never_apply_title_never_shows(
    api: Api, person_a: Person, factory: sessionmaker[Session], title: str
) -> None:
    add_posting(factory, person_a, title=title)
    assert deck_ids(api, person_a) == []


def test_the_titles_the_rule_keeps_all_show(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    kept = {str(add_posting(factory, person_a, title=title)) for title in KEPT_BY_OWNER}
    assert {i for page in walk(api, person_a) for i in page} == kept


def test_a_never_apply_company_never_shows(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    add_posting(factory, person_a, company="Example CyberDefense", title="AI Engineer")
    assert deck_ids(api, person_a) == []


def test_rules_that_cannot_be_read_empty_the_deck(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    add_posting(factory, person_a)
    with factory() as session, session.begin():
        profile = session.get(Profile, person_a.user_id)
        assert profile is not None
        profile.sections = {**profile.sections, "search": {"min_fit": "high"}}
    body = get_deck(api, person_a).json()
    assert (body["cards"], body["left"], body["rules_unreadable"]) == ([], 0, True)


def test_the_sweep_s_skipped_rows_are_not_in_the_deck(
    api: Api, person_a: Person, factory: sessionmaker[Session], fake_ats: FakeAts
) -> None:
    set_profile(factory, person_a.user_id, deck_search(min_fit=0))
    postings = swept(factory, person_a, fake_ats)
    assert postings, "the fake sweep found nothing"
    with factory() as session, session.begin():
        first = jobs_service.lock_job(session, person_a.user_id, postings[0].id)
        assert first is not None
        jobs_service.set_status(session, first, JobStatus.skipped, AT, via="never_apply")
    ids = deck_ids(api, person_a)
    assert str(postings[0].id) not in ids
    assert set(ids) == {str(p.id) for p in postings[1:]}


# --- order and pages -----------------------------------------------------------------------------


def _forty_five(factory: sessionmaker[Session], owner: Person) -> list[str]:
    """45 postings, 10 of them tied at fit 90 on one day; the deck's order computed apart."""
    rows: list[tuple[int | None, date, uuid.UUID]] = []
    for index in range(45):
        if index < 10:
            fit, day = 90, FOUND_ON
        elif index < 40:
            fit, day = 70 + index % 25, FOUND_ON - timedelta(days=index % 4)
        else:
            fit, day = None, FOUND_ON - timedelta(days=index % 3)
        text = None if fit is None else "Agents."
        posting = add_posting(factory, owner, fit=fit, found_on=day, posting_text=text)
        rows.append((fit, day, posting))
    rows.sort(key=lambda r: (r[0] is None, -(r[0] or 0), -r[1].toordinal(), str(r[2])))
    return [str(r[2]) for r in rows]


def test_a_deck_of_45_in_three_pages_in_order(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    expected = _forty_five(factory, person_a)
    pages = walk(api, person_a)
    assert [len(page) for page in pages] == [20, 20, 5]
    assert [i for page in pages for i in page] == expected
    assert get_deck(api, person_a).json()["left"] == 45
    assert walk(api, person_a) == pages  # a reload shows the same cards


def test_left_counts_from_the_page_on_and_limit_is_capped(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    _forty_five(factory, person_a)
    first = get_deck(api, person_a, limit=7).json()
    second = get_deck(api, person_a, limit=7, cursor=first["next_cursor"]).json()
    assert (len(first["cards"]), first["left"], second["left"]) == (7, 45, 38)
    assert get_deck(api, person_a, limit=21).status_code == 422
    assert get_deck(api, person_a, limit=0).status_code == 422


def test_the_threshold_changed_between_two_pages(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    expected = _forty_five(factory, person_a)
    first = get_deck(api, person_a).json()
    set_profile(factory, person_a.user_id, deck_search(min_fit=85))
    seen = [card["id"] for card in first["cards"]]
    cursor = first["next_cursor"]
    while cursor is not None:
        body = get_deck(api, person_a, cursor=cursor).json()
        seen += [card["id"] for card in body["cards"]]
        cursor = body["next_cursor"]
    assert len(seen) == len(set(seen))
    assert seen == [i for i in expected if i in seen]  # still the deck's order


def test_a_pass_between_two_pages_skips_no_card(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    expected = _forty_five(factory, person_a)
    first = get_deck(api, person_a).json()
    assert pass_(api, person_a, expected[3]).status_code == 200
    second = get_deck(api, person_a, cursor=first["next_cursor"]).json()
    assert [card["id"] for card in second["cards"]] == expected[20:40]


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "x",
        "not base64 !",
        "W10",
        "WzEsMiwzXQ",
        "a" * 301,
        decks.encode_cursor(70, FOUND_ON, uuid.UUID(int=0))[:-2],
    ],
)
def test_a_damaged_cursor_is_a_422(api: Api, person_a: Person, cursor: str) -> None:
    response = get_deck(api, person_a, cursor=cursor)
    if cursor == "":
        assert response.status_code == 200  # no cursor: the first page
    else:
        assert response.status_code == 422, cursor


def test_a_cursor_with_a_fit_out_of_range_is_refused() -> None:
    raw = base64.urlsafe_b64encode(b'[101,"2026-10-07","00000000-0000-0000-0000-000000000000"]')
    with pytest.raises(decks.InvalidCursorError):
        decks.decode_cursor(raw.decode())
    with pytest.raises(decks.InvalidCursorError):
        decks.decode_cursor(base64.urlsafe_b64encode(b'[true,"2026-10-07","x"]').decode())


# --- filters -------------------------------------------------------------------------------------


def test_filters_alone_and_combined(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    cdi_paris = add_posting(factory, person_a, contract_type=ContractType.CDI, location="Paris")
    free_remote = add_posting(
        factory,
        person_a,
        contract_type=ContractType.Freelance,
        location="Remote, Europe",
        source="greenhouse:example",
    )
    vie_remote = add_posting(
        factory, person_a, contract_type=ContractType.VIE, location="REMOTE", source="manual"
    )

    def ids(**params: Any) -> set[str]:
        return set(deck_ids(api, person_a, **params))

    assert ids(**{"type[]": ["Freelance", "VIE"]}) == {str(free_remote), str(vie_remote)}
    assert ids(remote="remote") == {str(free_remote), str(vie_remote)}
    assert ids(remote="on_site") == {str(cdi_paris)}
    assert ids(source="ashby") == {str(cdi_paris)}
    assert ids(source="ashby:fake-ok") == {str(cdi_paris)}
    assert ids(source="ashby:fake") == set()
    assert ids(source="greenhouse") == {str(free_remote)}
    assert ids(source="manual") == {str(vie_remote)}
    assert ids(**{"type[]": ["VIE"]}, remote="remote", source="manual") == {str(vie_remote)}
    assert ids(**{"type[]": ["CDI"]}, remote="remote") == set()
    assert get_deck(api, person_a, **{"type[]": "Contract"}).status_code == 422
    assert get_deck(api, person_a, remote="hybrid").status_code == 422
    assert get_deck(api, person_a, source="x" * 201).status_code == 422


# --- the Pass ------------------------------------------------------------------------------------


def test_a_pass_skips_the_posting_with_a_swipe_event(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a, status=JobStatus.shortlisted)
    response = pass_(api, person_a, posting)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"id": str(posting), "status": "skipped", "changed": True}
    assert status_events(factory, posting) == [
        {"from": "shortlisted", "to": "skipped", "via": "swipe"}
    ]
    assert deck_ids(api, person_a) == []


def test_a_second_pass_answers_the_same_state_with_one_event(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    assert pass_(api, person_a, posting).json()["changed"] is True
    second = pass_(api, person_a, posting)
    assert second.status_code == 200
    assert second.json() == {"id": str(posting), "status": "skipped", "changed": False}
    assert len(status_events(factory, posting)) == 1


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (State.queued, "in_progress"),
        (State.ready, "in_progress"),
        (State.held, "in_progress"),
        (State.submitting, "in_progress"),
        (State.submitted, "submitted"),
        (State.submit_unknown, "submitted"),
        (State.failed, "changed"),
        (State.posting_closed, "changed"),
    ],
)
def test_a_pass_on_a_liked_posting_is_refused(
    api: Api, person_a: Person, factory: sessionmaker[Session], state: State, code: str
) -> None:
    posting = add_posting(factory, person_a)
    application = add_application(factory, person_a, posting, state)
    response = pass_(api, person_a, posting)
    assert response.status_code == 409
    body = response.json()
    assert (body["code"], body["application_id"]) == (code, str(application))
    assert body["detail"] == passes.SENTENCES[code]
    assert status_of(factory, posting) is JobStatus.found
    assert status_events(factory, posting) == []


def test_a_pass_after_a_cancelled_like_is_taken(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    add_application(factory, person_a, posting, State.cancelled)
    assert pass_(api, person_a, posting).json()["changed"] is True


@pytest.mark.parametrize("status", [JobStatus.cv_ready, JobStatus.applied, JobStatus.closed])
def test_a_pass_on_a_posting_moved_out_of_the_deck_is_refused(
    api: Api, person_a: Person, factory: sessionmaker[Session], status: JobStatus
) -> None:
    posting = add_posting(factory, person_a, status=status)
    response = pass_(api, person_a, posting)
    assert (response.status_code, response.json()["code"]) == (409, "changed")
    assert response.json()["application_id"] is None
    assert status_of(factory, posting) is status


def test_a_passed_posting_found_again_by_the_sweep_stays_skipped(
    api: Api, person_a: Person, factory: sessionmaker[Session], fake_ats: FakeAts
) -> None:
    set_profile(factory, person_a.user_id, deck_search(min_fit=0))
    first = swept(factory, person_a, fake_ats)
    by_url, by_name = first[0], first[1]
    assert pass_(api, person_a, by_url.id).status_code == 200
    assert pass_(api, person_a, by_name.id).status_code == 200
    with factory() as session, session.begin():  # only company, title and location still match
        session.execute(
            sa.update(Job).where(Job.id == by_name.id).values(url="https://jobs.example.org/moved")
        )
    again = swept(factory, person_a, fake_ats)
    assert sorted(p.id for p in again) == sorted(p.id for p in first)
    assert status_of(factory, by_url.id) is JobStatus.skipped
    assert status_of(factory, by_name.id) is JobStatus.skipped
    ids = deck_ids(api, person_a)
    assert str(by_url.id) not in ids
    assert str(by_name.id) not in ids


# --- the undo ------------------------------------------------------------------------------------


@pytest.mark.parametrize("before", [JobStatus.found, JobStatus.shortlisted])
def test_the_undo_puts_back_the_status_before_the_pass(
    api: Api, person_a: Person, factory: sessionmaker[Session], before: JobStatus
) -> None:
    posting = add_posting(factory, person_a, status=before)
    pass_(api, person_a, posting)
    response = undo(api, person_a, posting)
    assert response.status_code == 200
    assert response.json() == {"id": str(posting), "status": before.value, "changed": True}
    assert status_events(factory, posting)[-1] == {
        "from": "skipped",
        "to": before.value,
        "via": "swipe_undo",
    }
    assert deck_ids(api, person_a) == [str(posting)]
    again = undo(api, person_a, posting)  # the Pass is undone already
    assert (again.status_code, again.json()["code"]) == (409, "changed")


def test_the_undo_of_a_posting_never_passed(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    found = add_posting(factory, person_a)
    skipped_by_hand = add_posting(factory, person_a)
    with factory() as session, session.begin():
        row = jobs_service.lock_job(session, person_a.user_id, skipped_by_hand)
        assert row is not None
        jobs_service.set_status(session, row, JobStatus.skipped, AT)
    for posting, status in ((found, JobStatus.found), (skipped_by_hand, JobStatus.skipped)):
        response = undo(api, person_a, posting)
        assert (response.status_code, response.json()["code"]) == (409, "changed")
        assert status_of(factory, posting) is status


@pytest.mark.parametrize(
    ("moves", "via"),
    [
        ([JobStatus.found], None),  # the person, by hand
        ([JobStatus.rejected], "mail"),  # a mail
        ([JobStatus.cv_ready, JobStatus.skipped], None),  # skipped again, by another way
    ],
)
def test_the_undo_after_a_later_status_change_changes_nothing(
    api: Api,
    person_a: Person,
    factory: sessionmaker[Session],
    moves: list[JobStatus],
    via: str | None,
) -> None:
    posting = add_posting(factory, person_a)
    pass_(api, person_a, posting)
    with factory() as session, session.begin():
        row = jobs_service.lock_job(session, person_a.user_id, posting)
        assert row is not None
        for move in moves:
            jobs_service.set_status(session, row, move, AT, via=via)
    events = status_events(factory, posting)
    response = undo(api, person_a, posting)
    assert (response.status_code, response.json()["code"]) == (409, "changed")
    assert status_of(factory, posting) is moves[-1]
    assert status_events(factory, posting) == events


def test_the_undo_after_an_edit_by_the_jobs_api(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    pass_(api, person_a, posting)
    edited = api.request(
        "PATCH", f"/api/jobs/{posting}", token=person_a.token, json={"status": "closed"}
    )
    assert edited.status_code == 200, edited.text
    assert undo(api, person_a, posting).status_code == 409
    assert status_of(factory, posting) is JobStatus.closed


# --- isolation -----------------------------------------------------------------------------------


def test_b_can_neither_see_pass_nor_undo_a_s_postings(
    api: Api, person_a: Person, person_b: Person, factory: sessionmaker[Session]
) -> None:
    open_a = add_posting(factory, person_a)
    passed_a = add_posting(factory, person_a)
    mine_b = add_posting(factory, person_b)
    assert pass_(api, person_a, passed_a).status_code == 200
    assert deck_ids(api, person_b) == [str(mine_b)]
    for posting in (open_a, passed_a):
        for call in (pass_, undo):
            response = call(api, person_b, posting)
            assert response.status_code == 404
            assert response.json() == {"detail": "Posting not found."}
    assert status_of(factory, open_a) is JobStatus.found
    assert status_of(factory, passed_a) is JobStatus.skipped
    assert len(status_events(factory, passed_a)) == 1
    # A's Pass and a B posting at the same company and title: B's deck still shows it.
    assert str(mine_b) in deck_ids(api, person_b)
    assert set(deck_ids(api, person_a)) == {str(open_a)}


@pytest.mark.parametrize("posting_id", ["nope", "5b1f0e2a-3c4d-4e5f-8a9b-0c1d2e3f4a5b", "1" * 40])
def test_an_unknown_posting_is_a_404(api: Api, person_a: Person, posting_id: str) -> None:
    assert pass_(api, person_a, posting_id).status_code == 404
    assert undo(api, person_a, posting_id).status_code == 404


def test_every_route_needs_a_session(api: Api, person_a: Person) -> None:
    posting = uuid.uuid4()
    assert api.get("/api/postings/deck").status_code == 401
    assert api.post(f"/api/postings/{posting}/pass").status_code == 401
    assert api.request("DELETE", f"/api/postings/{posting}/pass").status_code == 401


def test_a_write_without_the_csrf_header_is_refused(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    response = api.post(f"/api/postings/{posting}/pass", token=person_a.token, csrf=False)
    assert response.status_code == 403
    assert status_of(factory, posting) is JobStatus.found


# --- the lock: a Pass racing a Like, and two Passes ---------------------------------------------


def test_a_pass_waiting_behind_a_like_answers_in_progress(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    answers: list[Any] = []
    errors: list[BaseException] = []
    with factory() as like:  # the Like's transaction: the posting's lock, then the application
        assert jobs_service.lock_job(like, person_a.user_id, posting) is not None
        application = add_application_in(like, person_a, posting)
        thread = run_in_thread(errors, lambda: answers.append(pass_(api, person_a, posting)))
        wait_until_blocked(factory)
        like.commit()
    join_all(thread)
    assert errors == []
    assert answers[0].status_code == 409
    assert answers[0].json()["code"] == "in_progress"
    assert answers[0].json()["application_id"] == str(application)
    assert status_of(factory, posting) is JobStatus.found


def test_a_pass_waiting_behind_a_pass_answers_the_same_state(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    posting = add_posting(factory, person_a)
    answers: list[Any] = []
    errors: list[BaseException] = []
    with factory() as first:
        outcome = passes.pass_posting(first, person_a.user_id, posting, AT)
        assert outcome.changed
        thread = run_in_thread(errors, lambda: answers.append(pass_(api, person_a, posting)))
        wait_until_blocked(factory)
        first.commit()
    join_all(thread)
    assert errors == []
    assert answers[0].json() == {"id": str(posting), "status": "skipped", "changed": False}
    assert len(status_events(factory, posting)) == 1


def add_application_in(session: Session, owner: Person, posting_id: uuid.UUID) -> uuid.UUID:
    application = Application(
        id=uuid.uuid4(),
        user_id=owner.user_id,
        posting_id=posting_id,
        mode="review",
        dry_run=False,
        state=State.queued,
        ats="ashby",
        form_url="https://jobs.ashbyhq.com/example/raced",
        canonical_key="ashby:example:raced",
        created_at=AT,
        updated_at=AT,
    )
    session.add(application)
    session.flush()
    return application.id


@pytest.mark.parametrize("company", ["Example Robotics", "Example CyberDefense", ""])
def test_the_deck_s_built_patterns_match_as_apply_s_matcher(company: str) -> None:
    rules = [NeverApplyRule.parse(OWNER_RULE), NeverApplyRule.parse("Nothing here")]
    patterns = decks._patterns(rules)
    for title in [*REFUSED_BY_OWNER, *KEPT_BY_OWNER, "", "Secu­rity Engineer"]:
        expected = guardrails.matching_rule(rules, company, title) is not None
        assert decks._excluded(patterns, company, title) is expected, (company, title)
