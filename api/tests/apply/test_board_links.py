"""Job board listings and no dead ends (owner, 2026-11-01: "When I'm here, how can I continue
applying ?? I fucking don't understand").

The posting's address is often a board's listing (Arbeitnow, Apec, Cadremploi, ...), not the
employer's form. Its Apply link is read without a click, validated, and followed as LinkedIn's
company-site link is (docs/apply-linkedin.md): the lease ends, the application is queued again
with the employer's address, the next fill checks everything again; at most two follows. A
script-only Apply button stops with a reason. Every stop the app cannot get past offers "I
applied" (`manual.applied`: `submitted`, the posting `applied`, no click by the app) and "Try
again" (a new fill). Invented pages and the fake forms only; no real job site."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import boards, caps, linkedin, states
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.jobs.models import Job
from tests.apply.a5_support import FakeAgent, FakeLeases, RunForm, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import WHY, application, events, fill, services, started
from tests.apply.test_a6_gate import stub
from tests.apply.test_a13_full_auto import HOUR, WHY_TEXT, answered, api, job
from tests.identity.api_helpers import Api

__all__ = ["api", "stub"]

LISTING = "https://www.arbeitnow.com/jobs/companies/example-robotics/applied-ai-engineer-123"
SECOND = "https://www.cadremploi.fr/emploi/detail_offre?offreId=4242"
THIRD = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/1W"


def listing(*apply: tuple[str, str | None], extra: str = "") -> str:
    """A board's listing page as a snapshot: a heading, a search box, a sign in link, and the
    Apply controls given (role and name, address or None for a script-only button)."""
    lines = [
        '- heading "Applied AI Engineer at Example Robotics" [level=1] [ref=e1]',
        '- textbox "Search jobs" [ref=e2]',
        '- link "Sign in" [ref=e3]:',
        "  - /url: /login",
    ]
    for index, (name, url) in enumerate(apply, start=4):
        if url is None:
            lines.append(f'- button "{name}" [ref=e{index}]')
        else:
            lines += [f'- link "{name}" [ref=e{index}]:', f"  - /url: {url}"]
    return "\n".join(lines) + extra


class Board(RunForm):
    """The fake browser on board listings: each address of `pages` shows its listing."""

    def __init__(self, pages: dict[str, str]) -> None:
        form = run_ashby()
        super().__init__(form.fields, submit=form.submit, title=form.title)
        self.listings = pages

    def render(self) -> str:
        if self.url in self.listings:
            self._refs = {}
            return self.listings[self.url]
        return super().render()


def form_of(db: Session, person: Person) -> str:
    return db.scalar(sa.select(Job.url).where(Job.id == person.posting_id))


def on_board(db: Session, person: Person, url: str = LISTING) -> str:
    """The posting moved to a board's listing; returns its original fake Ashby form address."""
    form = form_of(db, person)
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(url=url))
    db.commit()
    return form


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [event.kind for event in events(db, app_id)]


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, stub: str) -> str:
    """The fake forms server read as the ATS it names, as the stack's APPLY_STUB_BASE_URL does."""
    real = caps._setting
    monkeypatch.setattr(
        caps, "_setting", lambda name: stub if name == "apply_stub_base_url" else real(name)
    )
    return stub


# --- reading the listing -------------------------------------------------------------------------


EMPLOYER = "https://jobs.ashbyhq.com/example-robotics/3f1c/application"


@pytest.mark.parametrize(
    "name",
    ["Apply", "Apply now", "Apply on company site", "Postuler", "Je postule", "Candidater"],
)
def test_the_apply_link_of_a_listing_is_read(name: str) -> None:
    hop = boards.listing_hop(listing((name, EMPLOYER)), LISTING, None)
    assert hop == boards.Hop(EMPLOYER)


def test_a_relative_link_is_read_against_the_listing() -> None:
    hop = boards.listing_hop(listing(("Apply now", "/apply/out/123")), LISTING, None)
    assert hop == boards.Hop("https://www.arbeitnow.com/apply/out/123")


def test_a_script_only_button_is_a_clear_stop() -> None:
    assert boards.listing_hop(listing(("Apply now", None)), LISTING, None) == boards.Hop(None)


@pytest.mark.parametrize(
    "href",
    [
        "javascript:void(0)",
        "mailto:jobs@example.org",
        LISTING,
        LISTING + "#apply",
        LISTING + "/",
        "https://www.arbeitnow.com/login?next=/apply",
        "https://www.arbeitnow.com/signup",
        "https://accounts.example.org/oauth/authorize",
        "https://www.arbeitnow.com/pricing",
        "https://www.arbeitnow.com/employers/post-a-job",
        "https://user:secret@jobs.ashbyhq.com/example-robotics/1/application",
        "https://jobs.ashbyhq.com:8443/example-robotics/1/application",
        "http://127.0.0.1/apply",
        "https://localhost/apply",
        "ftp://example.org/apply",
    ],
)
def test_an_unusable_address_is_refused_and_nothing_is_guessed(href: str) -> None:
    assert boards.listing_hop(listing(("Apply now", href)), LISTING, None) == boards.Hop(None)


def test_two_different_apply_links_are_never_guessed() -> None:
    page = listing(("Apply", EMPLOYER), ("Apply now", "https://other.example.org/jobs/2"))
    assert boards.listing_hop(page, LISTING, None) == boards.Hop(None)
    same = listing(("Apply", EMPLOYER), ("Apply now", EMPLOYER))
    assert boards.listing_hop(same, LISTING, None) == boards.Hop(EMPLOYER)


def test_a_page_without_an_apply_control_is_left_as_it_was() -> None:
    assert boards.listing_hop(listing(), LISTING, None) is None


def test_known_boards() -> None:
    for url in [LISTING, SECOND, THIRD, "https://app.joinhandshake.com/jobs/1", PAGE_HIMALAYAS]:
        assert boards.is_board(url), url
    assert not boards.is_board("https://careers.example-robotics.test/jobs/1")


PAGE_HIMALAYAS = "https://himalayas.app/companies/example-cloud/jobs/genai-engineer"


# --- the follow ----------------------------------------------------------------------------------


def test_a_listing_is_followed_to_the_employer_form_then_filled(
    db: Session, factory: sessionmaker[Session], person_a: Person, stubbed: str
) -> None:
    form = on_board(db, person_a)
    app_id = started(db, person_a)
    board, leases = Board({LISTING: listing(("Apply now", form))}), FakeLeases()
    assert fill(services(factory, board, leases, stub_base=stubbed), app_id) == "queued"
    found = application(db, app_id)
    assert (found.state, found.form_url, found.ats, found.needs) == (
        State.queued,
        form,
        "ashby",
        None,
    )
    assert "board_link" in kinds(db, app_id)
    assert leases.released  # the listing's lease ends before the employer's form is opened
    assert [c for c in board.calls if c[0] == "click"] == []  # read, never clicked
    # The posting link the person sees is unchanged.
    assert db.scalar(sa.select(Job.url).where(Job.id == person_a.posting_id)) == LISTING
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stubbed), app_id) == "ready"


def test_at_most_two_follows_then_a_clear_stop(
    db: Session, factory: sessionmaker[Session], person_a: Person, stubbed: str
) -> None:
    on_board(db, person_a)
    app_id = started(db, person_a)
    board = Board(
        {
            LISTING: listing(("Apply", SECOND)),
            SECOND: listing(("Postuler", THIRD)),
            THIRD: listing(("Je postule", "https://www.example-robotics.example/apply")),
        }
    )
    assert fill(services(factory, board, stub_base=stubbed), app_id) == "queued"
    assert application(db, app_id).form_url == SECOND
    assert fill(services(factory, board, stub_base=stubbed), app_id) == "queued"
    assert application(db, app_id).form_url == THIRD
    assert fill(services(factory, board, stub_base=stubbed), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.form_url) == (State.needs_person, boards.HOPS, THIRD)
    assert kinds(db, app_id).count("board_link") == 2


def test_a_script_only_apply_button_stops_with_its_reason(
    db: Session, factory: sessionmaker[Session], person_a: Person, stubbed: str
) -> None:
    on_board(db, person_a)
    app_id = started(db, person_a, Mode.direct)
    board, leases = Board({LISTING: listing(("Apply now", None))}), FakeLeases()
    assert fill(services(factory, board, leases, stub_base=stubbed), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.form_url) == (
        State.needs_person,
        boards.NO_LINK,
        LISTING,
    )
    assert "board_link" not in kinds(db, app_id)
    assert [c for c in board.calls if c[0] == "click"] == []


@pytest.mark.parametrize(
    ("own", "mode"),
    [(True, Mode.direct), (False, Mode.review)],
)
def test_one_click_keeps_its_guards_on_the_followed_address(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stubbed: str,
    own: bool,
    mode: Mode,
) -> None:
    """One click Apply goes on only on the posting's own company's form; another site, a board
    or a shared host goes to Review first (`company_site_unverified`), as for LinkedIn."""
    form = on_board(db, person_a)
    target = form if own else "https://careers.other-company.example/jobs/12"
    app_id = started(db, person_a, Mode.direct)
    board = Board({LISTING: listing(("Apply on company site", target))})
    assert fill(services(factory, board, stub_base=stubbed), app_id) == "queued"
    found = application(db, app_id)
    assert (found.form_url, found.mode) == (target, mode)
    handed = db.scalars(
        sa.select(ApplicationEvent.data).where(
            ApplicationEvent.application_id == app_id,
            ApplicationEvent.kind == "sent_to_review",
        )
    ).all()
    assert handed == ([] if own else [{"code": linkedin.NOT_OWN_COMPANY}])


def test_one_click_on_a_listing_ends_with_exactly_one_submission(
    db: Session, factory: sessionmaker[Session], person_a: Person, stubbed: str
) -> None:
    form = on_board(db, person_a)
    app_id = started(db, person_a, Mode.direct)
    board = Board({LISTING: listing(("Apply now", form))})
    assert fill(services(factory, board, stub_base=stubbed), app_id) == "queued"
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stubbed), app_id) == "ready"
    answered(db, app_id)
    assert job(factory, stubbed, app_id, AT + HOUR)[0] == "approved"
    written, sent = job(factory, stubbed, app_id, AT + HOUR)
    assert (written, len(sent.gate_clicks)) == ("submitted", 1)
    assert db.scalar(sa.select(Job.status).where(Job.id == person_a.posting_id)) == "applied"


# --- no dead ends: I applied, Try again ----------------------------------------------------------


STOPS = ["submit_not_designated", "no_adapter", "forbidden", boards.HOPS, boards.NO_LINK]


def stopped(db: Session, person: Person, needs: str, mode: Mode = Mode.direct) -> uuid.UUID:
    app_id = started(db, person, mode)
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(state=State.needs_person.value, needs=needs)
    )
    db.commit()
    return app_id


def post(api: Api, person: Person, app_id: uuid.UUID, what: str) -> Any:
    token = api.login_token(person.email)
    return api.post(f"/api/applications/{app_id}/{what}", token=token)


@pytest.mark.parametrize("needs", STOPS)
def test_i_applied_records_it_with_no_click_by_the_app(
    api: Api, db: Session, person_a: Person, needs: str
) -> None:
    app_id = stopped(db, person_a, needs)
    answer = post(api, person_a, app_id, "applied")
    assert (answer.status_code, answer.json()["state"]) == (200, "submitted")
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (State.submitted, None, None)
    assert db.scalar(sa.select(Job.status).where(Job.id == person_a.posting_id)) == "applied"
    recorded = [e.data for e in events(db, app_id) if e.kind == "recorded"]
    assert recorded
    assert recorded[-1]["by"] == "person_elsewhere"
    assert not {"approved", "submitting", "authorised"} & set(kinds(db, app_id))
    again = post(api, person_a, app_id, "applied")
    assert (again.status_code, again.json()["code"]) == (409, "already_recorded")


@pytest.mark.parametrize("state", [State.filling, State.ready, State.cancelled])
def test_i_applied_is_refused_while_the_app_works_or_after_an_end(
    api: Api, db: Session, person_a: Person, state: State
) -> None:
    app_id = started(db, person_a)
    db.execute(sa.update(Application).where(Application.id == app_id).values(state=state.value))
    db.commit()
    answer = post(api, person_a, app_id, "applied")
    assert (answer.status_code, answer.json()["code"]) == (409, "not_open")
    assert application(db, app_id).state is state


def test_i_applied_is_the_persons_own_application_only(
    api: Api, db: Session, person_a: Person, person_b: Person
) -> None:
    app_id = stopped(db, person_a, "no_adapter")
    assert post(api, person_b, app_id, "applied").status_code == 404
    assert application(db, app_id).state is State.needs_person


def test_the_move_by_the_person_is_only_from_a_stop() -> None:
    assert {State.queued, State.needs_person} == states.PERSON_SENT_FROM
    found = Application(mode=Mode.review, state=State.ready)
    with pytest.raises(states.TransitionError):
        states.move(found, State.submitted, AT, by_person=True)
    found = Application(mode=Mode.review, state=State.needs_person)
    with pytest.raises(states.TransitionError):
        states.move(found, State.submitted, AT)


@pytest.mark.parametrize("needs", STOPS)
def test_try_again_after_a_stop_queues_a_new_fill(
    api: Api, db: Session, person_a: Person, needs: str
) -> None:
    app_id = stopped(db, person_a, needs)
    answer = post(api, person_a, app_id, "retry")
    assert (answer.status_code, answer.json()["state"]) == (200, "queued")
    found = application(db, app_id)
    assert (found.state, found.needs, found.mode) == (State.queued, None, Mode.direct)
    assert "person_retry" in kinds(db, app_id)


def test_the_fake_board_listing_names_its_form_or_hides_it(forms: Any) -> None:
    link = forms.get("/board/greenhouse/northwind-labs/4012350").body.decode()
    assert '<a href="/greenhouse/northwind-labs/jobs/4012350">Apply now</a>' in link
    button = forms.get("/board/greenhouse/northwind-labs/4012350?variant=button").body.decode()
    assert "Apply now</button>" in button
    assert 'href="/greenhouse' not in button
    assert forms.get("/board/greenhouse/northwind-labs/1").status == 404
