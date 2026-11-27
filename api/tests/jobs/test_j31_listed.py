"""Jobs J31 (owner, 2026-11-26: "Oui, avec une mention"): a posting kept from a card of a list is
stored with the list's address (`jobs.list_url`, migration 0052) and its card's text, counted per
source; a full report of its address by a later search replaces the card's text, scores it again
and removes the mention; the Jobs list filters it, Discover never takes it, and Write now and
Apply say why they wait. Invented postings only."""

import dataclasses
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import guardrails
from app.browser import finder
from app.jobs import service, sweep
from app.jobs.models import ContractType, Job, JobEvent, JobEventKind, JobStatus
from tests.jobs.sweep_helpers import (
    finding,
    jobs_of,
    new_sweep,
    now,
    run,
    search,
    set_profile,
)

LIST = "https://aggregator.example.test/emplois?q=data"


def card(title: str = "GenAI engineer", **fields) -> object:
    full = finding(title, **fields)
    return dataclasses.replace(
        full,
        text=f"{title} Acme Robotics Paris",
        evidence={"title": title, "rule": "card", "card": "page", "list": LIST},
        list_url=LIST,
    )


def test_a_card_is_stored_with_its_list_then_its_page_read_replaces_it(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    listed = card()
    first = run(factory, new_sweep(factory, alice), listed)
    [job] = jobs_of(factory, alice)
    assert (job.list_url, job.posting_text) == (LIST, "GenAI engineer Acme Robotics Paris")
    assert (first.stats["new"], first.stats["listed"]) == (1, 1)
    assert first.stats["sources"][0]["listed"] == 1
    again = run(factory, new_sweep(factory, alice), listed)  # the same card: a duplicate
    assert (again.stats["duplicates"], again.stats["listed"], again.stats["listed_read"]) == (
        1,
        0,
        0,
    )
    assert jobs_of(factory, alice)[0].list_url == LIST

    page = dataclasses.replace(finding("GenAI engineer"), url=listed.url)
    read = run(factory, new_sweep(factory, alice), page)
    [same] = jobs_of(factory, alice)
    assert same.id == job.id
    assert same.list_url is None
    assert same.posting_text == page.text
    assert same.read_at is None
    assert (read.stats["duplicates"], read.stats["listed_read"]) == (1, 1)
    assert read.stats["sources"][0]["listed_read"] == 1
    with factory() as db:
        events = db.scalars(
            sa.select(JobEvent.data).where(
                JobEvent.job_id == job.id, JobEvent.kind == JobEventKind.sweep
            )
        ).all()
    assert {"page_read": {"list_url": LIST}}.items() <= events[-1].items()
    # A card found after the page was read changes nothing.
    run(factory, new_sweep(factory, alice), listed)
    assert jobs_of(factory, alice)[0].posting_text == page.text


def test_the_list_filters_it_and_write_now_and_apply_say_why_they_wait(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    run(factory, new_sweep(factory, alice), card(), finding("Applied AI engineer"))
    with factory() as db:

        def titles(choice: service.ListingChoice) -> list[str]:
            filters = service.JobFilters(listing=choice)
            found = db.scalars(service.filtered(alice, filters)).all()
            return sorted(job.title for job in found)

        assert titles(service.ListingChoice.all) == ["Applied AI engineer", "GenAI engineer"]
        assert titles(service.ListingChoice.only) == ["GenAI engineer"]
        assert titles(service.ListingChoice.without) == ["Applied AI engineer"]
        listed = db.scalar(sa.select(Job).where(Job.user_id == alice, Job.list_url.is_not(None)))
        full = db.scalar(sa.select(Job).where(Job.user_id == alice, Job.list_url.is_(None)))
        assert service.listed_refusal(db, alice, listed.id) == service.LISTED_WAITS
        assert service.listed_refusal(db, alice, full.id) is None
        assert service.listed_refusal(db, bob, listed.id) is None  # another person's: nothing
        assert "its text is needed first" in guardrails.SENTENCES["posting_listed"]


def stored(factory: sessionmaker[Session], alice: uuid.UUID, listed: object) -> Job:
    run(factory, new_sweep(factory, alice), listed)
    [job] = jobs_of(factory, alice)
    assert job.list_url == LIST
    return job


def read_later(factory: sessionmaker[Session], alice: uuid.UUID, page: object):
    done = run(factory, new_sweep(factory, alice), page)
    [job] = jobs_of(factory, alice)
    return done, job


def events_of(factory: sessionmaker[Session], job_id: uuid.UUID, kind: JobEventKind) -> list:
    with factory() as db:
        return db.scalars(
            sa.select(JobEvent.data)
            .where(JobEvent.job_id == job_id, JobEvent.kind == kind)
            .order_by(JobEvent.at, JobEvent.id)
        ).all()


PAGE_TEXT = "GenAI engineer. We build agents with Python, LLMs, RAG and evaluation pipelines."


def test_a_fit_the_person_set_stays_when_the_page_is_read(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    listed = card()
    job = stored(factory, alice, listed)
    with factory() as db:
        db.execute(
            sa.update(Job)
            .where(Job.id == job.id)
            .values(fit=1, fit_overridden=True, competencies=["Invented care"])
        )
        db.commit()
    before = len(events_of(factory, job.id, JobEventKind.fit))
    page = dataclasses.replace(finding("GenAI engineer", text=PAGE_TEXT), url=listed.url)
    _, read = read_later(factory, alice, page)
    assert (read.fit, read.fit_overridden, read.competencies) == (1, True, ["Invented care"])
    assert (read.list_url, read.posting_text) == (None, PAGE_TEXT)
    assert len(events_of(factory, job.id, JobEventKind.fit)) == before


def test_the_page_read_scores_it_again(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    set_profile(factory, alice, search())
    listed = dataclasses.replace(card(), text="GenAI engineer")
    job = stored(factory, alice, listed)
    page = dataclasses.replace(finding("GenAI engineer", text=PAGE_TEXT), url=listed.url)
    _, read = read_later(factory, alice, page)
    assert read.fit is not None
    assert read.fit != job.fit
    last = events_of(factory, job.id, JobEventKind.fit)[-1]
    assert last == {"from": job.fit, "to": read.fit, "manual": False, "via": "sweep"}


@pytest.mark.parametrize(("card_date", "page_date", "kept"), [
    (None, "2026-11-20", "2026-11-20"),
    ("2026-11-01", "2026-11-20", "2026-11-01"),
    (None, None, None),
])  # fmt: skip
def test_the_page_read_takes_its_date_only_when_it_had_none(
    factory: sessionmaker[Session], alice: uuid.UUID, card_date, page_date, kept
) -> None:
    set_profile(factory, alice, search())
    listed = card(published_on=card_date)
    stored(factory, alice, listed)
    page = dataclasses.replace(finding("GenAI engineer", published_on=page_date), url=listed.url)
    _, read = read_later(factory, alice, page)
    assert (read.published_on.isoformat() if read.published_on else None) == kept


def test_the_page_read_computes_the_badge_again_from_the_page(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """The card showed no contract; the page's title states an internship, outside the CDI the
    search asks for: the badge says so once the page is read."""
    set_profile(factory, alice, search(contract_types=["CDI"]))
    listed = card()
    job = stored(factory, alice, listed)
    assert not job.outside_criteria
    page = dataclasses.replace(finding("GenAI engineer (Stage)"), url=listed.url)
    _, read = read_later(factory, alice, page)
    assert (read.contract_type, read.contract_stated) == (ContractType.Stage, True)
    assert "contract" in (read.outside_criteria or [])
    assert read.title == "GenAI engineer"  # the card's title stays


def test_a_page_read_through_the_cross_board_key_keeps_the_readable_address(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    listed = dataclasses.replace(card(), url="https://aggregator.example.test/r/1")
    job = stored(factory, alice, listed)
    page = finding("GenAI engineer", url="https://careers.acme-robotics.test/jobs/1")
    done, read = read_later(factory, alice, page)
    assert read.id == job.id
    assert (read.url, read.list_url, read.posting_text) == (page.url, None, page.text)
    assert done.stats["listed_read"] == 1
    said = events_of(factory, job.id, JobEventKind.sweep)[-1]["page_read"]
    assert said == {"list_url": LIST, "url_was": listed.url}


@pytest.mark.parametrize(
    ("kept_at", "read_at"),
    [
        ("http://ch.indeed.test:8080/rc/clk?jk=5f1c2d3e4a5b6c7d&bb=x0&tk=t1&from=serp",
         "http://www.indeed.test:8080/viewjob?jk=5f1c2d3e4a5b6c7d&from=serp&vjs=3"),
        ("http://www.linkedin.test:8080/jobs/view/genai-engineer-at-acme-4012345678?position=1"
         "&pageNum=0&trk=public_jobs", "http://ch.linkedin.test:8080/jobs/view/4012345678/"),
    ],
)  # fmt: skip
def test_a_card_kept_under_one_spelling_is_one_row_with_its_page_read_under_another(
    factory: sessionmaker[Session], alice: uuid.UUID, kept_at: str, read_at: str
) -> None:
    """Browser B31, review of #854, round 1: one posting, one row. The store reads the session's
    rule of the same posting (`slug.posting_url_key`): a card kept under the list's spelling,
    its employer not on the card, and its page read later under another spelling with its
    employer, is one row whose mention is removed, at the address of the page read."""
    set_profile(factory, alice, search())
    listed = dataclasses.replace(card(company=None), url=kept_at)
    job = stored(factory, alice, listed)
    assert job.company == sweep.UNKNOWN_EMPLOYER
    page = finding("GenAI engineer", url=read_at, text=PAGE_TEXT)
    done, read = read_later(factory, alice, page)
    assert read.id == job.id
    assert (read.url, read.list_url, read.posting_text) == (read_at, None, PAGE_TEXT)
    assert (done.stats["duplicates"], done.stats["listed_read"]) == (1, 1)


def test_an_address_another_posting_holds_keeps_the_cards(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search())
    listed = dataclasses.replace(card(), url="https://aggregator.example.test/r/2")
    job = stored(factory, alice, listed)
    other = "https://careers.acme-robotics.test/jobs/2"
    run(factory, new_sweep(factory, alice), finding("Robot welder", url=other))
    with factory() as db:
        plan = SimpleNamespace(user_id=alice, known=sweep.Known(db, alice))
        assert sweep._moved(db, plan, db.get(Job, job.id), other) is False
        db.commit()
    assert {j.url for j in jobs_of(factory, alice)} == {listed.url, other}


@pytest.mark.parametrize("status", [JobStatus.found, JobStatus.shortlisted])
def test_the_page_read_meets_the_never_apply_rules_on_the_pages_own_title(
    factory: sessionmaker[Session], alice: uuid.UUID, status: JobStatus
) -> None:
    """The card "GenAI engineer" met no rule; its page says "Senior GenAI engineer": the person's
    rule on "senior" skips it, unless the person moved it already (their decision stays)."""
    rule = "No senior roles: senior"
    set_profile(factory, alice, search(never_apply=[rule]))
    listed = card()
    job = stored(factory, alice, listed)
    assert job.status is JobStatus.found
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == job.id).values(status=status))
        db.commit()
    page = dataclasses.replace(finding("Senior GenAI engineer"), url=listed.url)
    done, read = read_later(factory, alice, page)
    if status is JobStatus.found:
        assert (read.status, read.notes) == (JobStatus.skipped, rule)
        said = events_of(factory, job.id, JobEventKind.status)[-1]
        assert said == {"from": "found", "to": "skipped", "via": "never_apply"}
    else:
        assert (read.status, read.notes) == (status, "")
    assert read.list_url is None
    # Counted as a new posting's skip is, by the sweep and by its source.
    skipped = int(status is JobStatus.found)
    assert done.stats["skipped_by_rule"] == skipped
    assert done.stats["sources"][0]["skipped_by_rule"] == skipped


def test_listed_read_counts_only_a_page_read_that_wrote(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Another run read the page first (the row's mention cleared) after this run's plan was
    made: this run's page read writes nothing, and counts nothing."""
    set_profile(factory, alice, search())
    listed = card()
    job = stored(factory, alice, listed)
    plan = sweep.claim(factory, new_sweep(factory, alice), now())
    assert plan is not None
    assert job.id in plan.known.listed
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == job.id).values(list_url=None))
        db.commit()
    page = dataclasses.replace(finding("GenAI engineer"), url=listed.url)
    delta = sweep.Stats()
    with factory() as db:
        sweep._store_finding(db, plan, "s", "jobs.example-robotics.test", page, delta, now())
        db.commit()
    assert (delta.duplicates, delta.listed_read) == (1, 0)
    assert jobs_of(factory, alice)[0].posting_text == listed.text


def through_the_site(*found: finder.VerifiedFinding) -> sweep.BrowserHook:
    """The website search's way to the store: its filter of the person's exclusions
    (`interactive._Site.verified`) over the run's own commits."""
    from app.browser.sources import interactive

    async def hook(plan: sweep.Plan, stats: sweep.Stats, progress, deadline: float) -> None:
        del deadline
        entry = next(e for e in stats.sources if e.get("host") and e.get("kind") != "board")
        entry.update(phase="running", outcome=None, reason=None)
        assert plan.commits is not None
        assert plan.search is not None
        plan.commits.admit(entry["source_id"])
        site = interactive._Site(
            interactive.searches_for(plan.search),
            commits=plan.commits,
            source_id=entry["source_id"],
            host=entry["host"],
        )
        await site.verified(finder.VerifiedBatch(tuple(found)))
        await plan.commits.settle(entry["source_id"])
        sweep.finish_source(entry)
        await progress()

    return hook


EXCLUDED = {
    "title_exclude": {"terms": ["stage"], "advanced_regex": False},
    "location_exclude": {"terms": ["lyon"], "advanced_regex": False},
}


@pytest.mark.parametrize(
    ("title", "location", "note"),
    [
        ("GenAI engineer (Stage)", "Paris, France", "Unwanted title: Stage"),
        ("GenAI engineer", "Lyon, France", "Unwanted place: Lyon"),  # the card had no place
    ],
)
def test_a_page_the_persons_exclusions_set_aside_skips_its_posting_kept_from_a_card(
    factory: sessionmaker[Session], alice: uuid.UUID, title: str, location: str, note: str
) -> None:
    """The card "GenAI engineer", no place shown, passed the person's unwanted titles and places;
    its page, read later, shows "(Stage)" or "Lyon": the posting is set Skipped with the
    exclusion as its note, its page read, with an event in its history."""
    set_profile(factory, alice, search(**EXCLUDED))
    listed = dataclasses.replace(card(), location="")
    job = stored(factory, alice, listed)
    page = dataclasses.replace(finding(title, location=location), url=listed.url)
    done = run(factory, new_sweep(factory, alice), browser=through_the_site(page))
    [read] = jobs_of(factory, alice)
    assert (read.id, read.status, read.notes) == (job.id, JobStatus.skipped, note)
    assert (read.list_url, read.posting_text) == (None, page.text)
    status = events_of(factory, job.id, JobEventKind.status)
    noted = events_of(factory, job.id, JobEventKind.note)
    assert status[-1] == {"from": "found", "to": "skipped", "via": "unwanted"}
    assert noted[-1] == {"from": "", "to": note, "via": "unwanted"}
    assert {"page_read": {"list_url": LIST}}.items() <= events_of(
        factory, job.id, JobEventKind.sweep
    )[-1].items()
    assert (done.stats["new"], done.stats["listed_read"], done.stats["skipped_by_rule"]) == (
        0,
        1,
        0,
    )


@pytest.mark.parametrize(
    "status", [JobStatus.shortlisted, JobStatus.cv_ready, JobStatus.skipped, JobStatus.applied]
)
def test_a_status_the_person_set_stays_when_an_exclusion_sets_the_page_aside(
    factory: sessionmaker[Session], alice: uuid.UUID, status: JobStatus
) -> None:
    set_profile(factory, alice, search(**EXCLUDED))
    listed = card()
    job = stored(factory, alice, listed)
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == job.id).values(status=status, notes="Mine"))
        db.commit()
    page = dataclasses.replace(finding("GenAI engineer (Stage)"), url=listed.url)
    run(factory, new_sweep(factory, alice), browser=through_the_site(page))
    [read] = jobs_of(factory, alice)
    assert (read.status, read.notes, read.list_url) == (status, "Mine", None)
    assert events_of(factory, job.id, JobEventKind.note) == []


def test_a_persons_note_stays_and_a_never_apply_rule_names_the_skip_first(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    rule = "No senior roles: senior"
    set_profile(factory, alice, search(never_apply=[rule], **EXCLUDED))
    first, second = card(), card("Applied AI engineer")
    run(factory, new_sweep(factory, alice), first, second)
    one, two = sorted(jobs_of(factory, alice), key=lambda job: job.url == second.url)
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == two.id).values(notes="Mine"))
        db.commit()
    pages = (
        dataclasses.replace(finding("Senior GenAI engineer (Stage)"), url=first.url),
        dataclasses.replace(finding("Applied AI engineer (Stage)"), url=second.url),
    )
    done = run(factory, new_sweep(factory, alice), browser=through_the_site(*pages))
    read = {job.id: job for job in jobs_of(factory, alice)}
    assert (read[one.id].status, read[one.id].notes) == (JobStatus.skipped, rule)
    assert (read[two.id].status, read[two.id].notes) == (JobStatus.skipped, "Mine")
    assert done.stats["skipped_by_rule"] == 1  # the rule's skip, not the exclusion's


@pytest.mark.parametrize("kind", ["unknown", "card", "read_already"])
def test_a_page_set_aside_changes_nothing_but_a_posting_waiting_for_its_page(
    factory: sessionmaker[Session], alice: uuid.UUID, kind: str
) -> None:
    """Set aside, a page of no posting stays out of the list (nothing stored), a card set aside
    reads no page, and a posting whose page was read already stays as it is."""
    set_profile(factory, alice, search(**EXCLUDED))
    listed = card()
    job = stored(factory, alice, listed)
    if kind == "read_already":
        page = dataclasses.replace(finding("GenAI engineer"), url=listed.url)
        run(factory, new_sweep(factory, alice), page)
    before = jobs_of(factory, alice)
    aside = dataclasses.replace(finding("GenAI engineer (Stage)"), url=listed.url)
    if kind == "unknown":
        aside = finding("Robot welder (Stage)", company="Example Labs")
    if kind == "card":
        aside = dataclasses.replace(card("GenAI engineer (Stage)"), url=listed.url)
    done = run(factory, new_sweep(factory, alice), browser=through_the_site(aside))
    after = jobs_of(factory, alice)
    assert [(j.id, j.status, j.list_url, j.posting_text) for j in after] == [
        (j.id, j.status, j.list_url, j.posting_text) for j in before
    ]
    assert (done.stats["new"], done.stats["listed_read"]) == (0, 0)
    assert job.id in {j.id for j in after}
