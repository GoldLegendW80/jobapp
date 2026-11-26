"""Jobs J30 (#829): the search keeps what it sees, with a badge for what is outside the criteria.

Owner, 2026-11-26: "Je veux vraiment qu'on remonte un max de job"; for a posting in a wanted place
that fits the profile but is outside a criterion, "Garder avec un badge". The badge is one pure
function of the posting's own fields and the criteria of the search that found it
(`criteria_badge.codes`); it runs at the store (a posting found again included), once the posting
reader filled the fields, when a person edits the contract or the work mode, and for the open
postings of a search when its criteria are saved. The tables below are built from that list."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import criteria_badge, searches, service, sweep
from app.jobs.criteria_badge import CONTRACT, OLDER, WORK_MODE, Criteria, Facts, codes
from app.jobs.models import ContractType, Job, JobStatus, SweepTrigger, WorkMode
from app.profile import service as profile_service
from app.profile.schemas import validate_section
from tests.jobs import test_reader as reading
from tests.jobs.sweep_helpers import (
    SKILLS,
    finding,
    jobs_of,
    new_sweep,
    now,
    run,
    search,
    set_profile,
)

FOUND = date(2026, 11, 26)
WITHIN = Criteria(frozenset({"CDI"}), "office", 30)


def facts(
    contract: str | None = None,
    mode: str | None = None,
    published: date | None = None,
    nature: str | None = None,
) -> Facts:
    return Facts(contract, mode, published, FOUND, nature)


# --- the function: accept and refuse tables -----------------------------------------------------


@pytest.mark.parametrize(
    ("saved", "contract", "outside"),
    [
        ((), "Freelance", False),  # none saved: any contract
        ((), None, False),
        (("CDI",), "CDI", False),
        (("CDI",), "Freelance", True),
        (("CDI",), "Stage", True),
        (("CDI",), "Alternance", True),
        (("CDI",), "CDD", True),
        (("CDI",), "VIE", True),
        (("CDI",), None, False),  # a contract nobody stated is not another contract
        (("CDI", "CDD"), "CDD", False),
        (("CDI", "CDD"), "Freelance", True),
        (("Freelance",), "CDI", True),  # a stated CDI
        (("Freelance",), None, False),  # kind()'s default CDI reads as unstated
        (("VIE",), "VIE", False),
        (("VIE",), "CDI", True),
        (("CDI", "CDD", "VIE", "Stage", "Alternance", "Freelance"), "Stage", False),
    ],
)
def test_the_contract_code(saved: tuple[str, ...], contract: str | None, outside: bool) -> None:
    found = codes(facts(contract), Criteria(frozenset(saved), None, None))
    assert found == ([CONTRACT] if outside else [])


def test_the_contract_a_website_has_by_nature_is_the_persons_choice() -> None:
    cdi = Criteria(frozenset({"CDI"}), None, None)
    assert codes(facts("VIE", nature="VIE"), cdi) == []
    assert codes(facts("Stage", nature="VIE"), cdi) == [CONTRACT]
    assert criteria_badge.nature_of("mon-vie-via.businessfrance.fr") == "VIE"
    assert criteria_badge.nature_of("jobs.example-robotics.test, mon-vie-via.businessfrance.fr")
    assert criteria_badge.nature_of("www.linkedin.com") is None
    assert criteria_badge.nature_of(None) is None
    assert criteria_badge.nature_of("") is None


STANCES = (None, "any", "remote", "hybrid", "office")
MODES = (None, "remote", "hybrid", "office")


@pytest.mark.parametrize("stance", STANCES)
@pytest.mark.parametrize("mode", MODES)
def test_every_pair_of_remote_stance_and_work_mode(stance: str | None, mode: str | None) -> None:
    """Within only when the posting's mode is the saved stance; any stance, none saved or a mode
    the posting does not state give no code."""
    outside = stance not in (None, "any") and mode is not None and mode != stance
    found = codes(facts(mode=mode), Criteria(frozenset(), stance, None))
    assert found == ([WORK_MODE] if outside else [])


@pytest.mark.parametrize(
    ("days_before", "days", "outside"),
    [
        (0, 30, False),
        (29, 30, False),
        (30, 30, False),  # the window's last day is within
        (31, 30, True),
        (45, 30, True),
        (400, 365, True),
        (365, 365, False),
        (1, 1, False),
        (2, 1, True),
        (-3, 30, False),  # a date after it was found
    ],
)
def test_the_age_counts_from_the_day_it_was_found(
    days_before: int, days: int, outside: bool
) -> None:
    published = FOUND - timedelta(days=days_before)
    found = codes(facts(published=published), Criteria(frozenset(), None, days))
    assert found == ([OLDER] if outside else [])


def test_an_unknown_date_is_not_older_and_no_window_holds_none() -> None:
    assert codes(facts(published=None), Criteria(frozenset(), None, 1)) == []
    old = facts(published=FOUND - timedelta(days=900))
    assert codes(old, Criteria(frozenset(), None, None)) == []
    assert codes(Facts(None, None, FOUND - timedelta(days=90), None), WITHIN) == []


def test_the_codes_come_in_one_order_and_together() -> None:
    every = facts("Freelance", "remote", FOUND - timedelta(days=60))
    assert codes(every, WITHIN) == [CONTRACT, WORK_MODE, OLDER]
    assert codes(facts("CDI", "office", FOUND - timedelta(days=3)), WITHIN) == []
    assert criteria_badge.CODES == (CONTRACT, WORK_MODE, OLDER)


@pytest.mark.parametrize(
    ("title", "location", "url", "stated"),
    [
        ("Data Scientist (CDI)", "", "", ContractType.CDI),
        ("CDI - Data Scientist", "", "", ContractType.CDI),
        ("Data Scientist", "", "https://jobs.example.test/offre-cdi-data-scientist", "CDI"),
        ("Data scientist cdi", "", "", ContractType.CDI),
        ("Data Scientist", "Paris, CDI", "", ContractType.CDI),
        ("Stage Data Scientist", "", "", ContractType.Stage),
        ("Data Science Internship", "", "", ContractType.Stage),
        ("Data Scientist Intern", "", "", ContractType.Stage),
        ("Freelance data mission", "", "", ContractType.Freelance),
        ("Data contractor", "", "", ContractType.Freelance),
        ("Data analyst VIE", "", "", ContractType.VIE),
        ("Data analyst", "", "https://mon-vie-via.businessfrance.fr/offres/1", ContractType.VIE),
        ("Alternance data", "", "", ContractType.Alternance),
        ("Apprenti data", "", "", ContractType.Alternance),
        ("Data analyst CDD 6 mois", "", "", ContractType.CDD),
        ("Fixed-term data analyst", "", "", ContractType.CDD),
        ("Temporary data analyst", "", "", ContractType.CDD),
        # Nothing says a contract: kind() would answer CDI, the badge reads none.
        ("Data Scientist", "", "", None),
        ("Data Scientist", "Paris, France", "https://jobs.example.test/42", None),
        ("Full-time Data Scientist", "", "", None),
        ("Data Scientist, permanent role", "", "", None),
        ("Data Scientist CDIs team", "", "", None),
        ("ACDI coordinator", "", "", None),
        ("Cdiscount data analyst", "", "", None),
        ("", "", "", None),
    ],
)
def test_the_contract_a_posting_states(
    title: str, location: str, url: str, stated: ContractType | str | None
) -> None:
    assert criteria_badge.stated_contract(title, location, url) == stated


class Row:
    def __init__(self, contract: ContractType, stated: bool | None, **more: Any) -> None:
        self.contract_type, self.contract_stated = contract, stated
        self.work_mode = more.get("work_mode")
        self.published_on = more.get("published_on")
        self.found_on = FOUND
        self.source = more.get("source", "jobs.example-robotics.test")


@pytest.mark.parametrize(
    ("contract", "stated", "read", "seen"),
    [
        (ContractType.CDI, None, False, None),  # before 0051: a CDI may be kind()'s default
        (ContractType.Freelance, None, False, "Freelance"),  # only a word gives it
        (ContractType.CDI, False, False, None),
        (ContractType.Stage, False, False, None),
        (ContractType.CDI, True, False, "CDI"),
        (ContractType.CDI, None, True, "CDI"),  # the posting reader quoted it
        (ContractType.CDI, False, True, "CDI"),
    ],
)
def test_a_rows_contract_as_the_badge_reads_it(
    contract: ContractType, stated: bool | None, read: bool, seen: str | None
) -> None:
    assert criteria_badge.facts_of(Row(contract, stated), read).contract == seen


def test_the_criteria_of_a_stored_search() -> None:
    assert criteria_badge.Criteria.read(None) == Criteria()
    assert criteria_badge.Criteria.read({"days": "thirty"}) is None  # unreadable: kept as is
    read = criteria_badge.Criteria.read(
        {"contract_types": ["CDI", "CDD"], "remote_stance": "hybrid", "days": 14}
    )
    assert read == Criteria(frozenset({"CDI", "CDD"}), "hybrid", 14)


# --- the store ------------------------------------------------------------------------------------


def days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


CRITERIA = {"contract_types": ["Freelance"], "remote_stance": "office", "days": 30}
FINDINGS = {
    "freelance": finding("Freelance Applied AI engineer", published_on=days_ago(3)),
    "cdi": finding("ML platform engineer (CDI)", published_on=days_ago(3)),
    "unstated": finding("Applied AI engineer", published_on=days_ago(3)),
    "remote": finding("GenAI engineer", location="Remote", published_on=days_ago(3)),
    "older": finding("Forward deployed AI engineer", published_on=days_ago(45)),
    "undated": finding("AI engineer platform"),
}


def test_the_store_keeps_each_posting_with_its_badge_and_counts_them(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(**CRITERIA))
    row = run(factory, new_sweep(factory, alice), *FINDINGS.values())
    stored = {job.url: job for job in jobs_of(factory, alice)}
    assert {
        name: (stored[one.url].outside_criteria, stored[one.url].contract_stated)
        for name, one in FINDINGS.items()
    } == {
        "freelance": ([], True),
        "cdi": ([CONTRACT], True),  # its "(CDI)" said it, and left the title
        "unstated": ([], False),  # kind()'s CDI is no other contract
        "remote": ([WORK_MODE], False),
        "older": ([OLDER], False),
        "undated": ([], False),
    }
    assert stored[FINDINGS["cdi"].url].title == "ML platform engineer"
    assert all(job.criteria_search_id is None for job in stored.values())
    assert (row.stats["new"], row.stats["outside"]) == (6, 3)
    [entry] = row.stats["sources"]
    assert (entry["postings"], entry["outside"]) == (6, 3)
    # The counters of the web of today stay as they were.
    assert (row.stats["seen"], row.stats["matched"]) == (6, 6)


def test_a_posting_found_again_stays_one_row_and_takes_the_new_criteria(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    posting = FINDINGS["freelance"]
    set_profile(factory, alice, search(**CRITERIA))
    run(factory, new_sweep(factory, alice), posting)
    [job] = jobs_of(factory, alice)
    assert job.outside_criteria == []
    set_profile(factory, alice, search(contract_types=["CDI"]))  # no callback: written directly
    again = run(factory, new_sweep(factory, alice), posting)
    [same] = jobs_of(factory, alice)
    assert (same.id, same.outside_criteria) == (job.id, [CONTRACT])
    assert same.updated_at == job.updated_at
    assert (again.stats["new"], again.stats["duplicates"], again.stats["outside"]) == (0, 1, 0)


def saved_search(factory: sessionmaker[Session], user_id: uuid.UUID, **criteria: Any) -> uuid.UUID:
    keys = {key: value for key, value in search(**criteria).items() if key in searches.CRITERIA}
    with factory() as db, db.begin():
        return searches.create(db, user_id, "Freelance", keys, searches.SavedSchedule()).id


def test_a_saved_searchs_sweep_reads_its_own_criteria(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(contract_types=["CDI"]))
    saved_id = saved_search(factory, alice, contract_types=["Freelance"])
    with factory() as db:
        saved = searches.get(db, alice, saved_id)
        sweep_id = sweep.start(db, alice, SweepTrigger.manual, now(), saved=saved).id
        db.commit()
    run(factory, sweep_id, FINDINGS["freelance"], FINDINGS["cdi"])
    found = {job.url: job for job in jobs_of(factory, alice)}
    assert found[FINDINGS["freelance"].url].outside_criteria == []
    assert found[FINDINGS["cdi"].url].outside_criteria == [CONTRACT]
    assert {job.criteria_search_id for job in found.values()} == {saved_id}


# --- the passes after the store -----------------------------------------------------------------


def seed(factory: sessionmaker[Session], user_id: uuid.UUID, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "slug": f"posting-{uuid.uuid4().hex[:10]}",
        "company": "Example Robotics",
        "title": "Applied AI engineer",
        "contract_type": ContractType.Freelance,
        "contract_stated": True,
        "status": JobStatus.found,
        "found_on": FOUND,
    }
    values.update(fields)
    with factory() as db, db.begin():
        job = Job(user_id=user_id, **values)
        db.add(job)
        db.flush()
        return job.id


def row_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


def save_main(factory: sessionmaker[Session], user_id: uuid.UUID, **criteria: Any) -> None:
    with factory() as db:
        revision = profile_service.get_profile(db, user_id, now()).revision
        data = validate_section("search", search(**criteria))
        profile_service.save_section(db, user_id, "search", data, revision, now())
        db.commit()


def test_saving_the_main_search_badges_its_open_postings_again(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(contract_types=["Freelance"]))
    open_ = seed(factory, alice)
    shortlisted = seed(factory, alice, status=JobStatus.shortlisted, work_mode=WorkMode.remote)
    applied = seed(factory, alice, status=JobStatus.applied)
    before_0051 = seed(factory, alice, contract_type=ContractType.CDI, contract_stated=None)
    saved_id = saved_search(factory, alice, contract_types=["Freelance"])
    of_saved = seed(factory, alice, criteria_search_id=saved_id)
    gone = seed(factory, alice, criteria_search_id=uuid.uuid4())  # a search deleted since
    updated = row_of(factory, open_).updated_at
    save_main(factory, alice, contract_types=["CDI"], remote_stance="office")
    assert row_of(factory, open_).outside_criteria == [CONTRACT]
    assert row_of(factory, open_).updated_at == updated  # no edit of the person's
    assert row_of(factory, shortlisted).outside_criteria == [CONTRACT, WORK_MODE]
    assert row_of(factory, applied).outside_criteria is None  # not open: untouched
    assert row_of(factory, before_0051).outside_criteria == []  # its CDI may be a default
    assert row_of(factory, of_saved).outside_criteria is None  # another search's
    assert row_of(factory, gone).outside_criteria == [CONTRACT]


def test_another_section_or_another_person_changes_no_badge(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search(contract_types=["Freelance"]))
    set_profile(factory, bob, search(contract_types=["Freelance"]))
    alices, bobs = seed(factory, alice), seed(factory, bob)
    with factory() as db:
        revision = profile_service.get_profile(db, alice, now()).revision
        skills = validate_section("skills", SKILLS)
        profile_service.save_section(db, alice, "skills", skills, revision, now())
        db.commit()
    assert row_of(factory, alices).outside_criteria is None
    save_main(factory, alice, contract_types=["CDI"])
    assert (row_of(factory, alices).outside_criteria, row_of(factory, bobs).outside_criteria) == (
        [CONTRACT],
        None,
    )


def test_changing_or_deleting_a_saved_search_badges_its_postings_again(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(contract_types=["Stage"]))
    saved_id = saved_search(factory, alice, contract_types=["Freelance"])
    mine, main = seed(factory, alice, criteria_search_id=saved_id), seed(factory, alice)
    with factory() as db:
        row = searches.get(db, alice, saved_id)
        assert row is not None
        criteria = {**row.criteria, "contract_types": ["CDD"]}
        searches.change(db, alice, saved_id, row.revision, row.name, criteria, row_schedule(row))
        db.commit()
    assert (row_of(factory, mine).outside_criteria, row_of(factory, main).outside_criteria) == (
        [CONTRACT],
        None,
    )
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == mine).values(contract_type="Stage"))
        searches.delete(db, alice, saved_id)
        db.commit()
    # Its postings follow the main search (Stage saved), and so does the main search's own.
    assert (row_of(factory, mine).outside_criteria, row_of(factory, main).outside_criteria) == (
        [],
        [CONTRACT],
    )


def row_schedule(row: Any) -> searches.SavedSchedule:
    return searches.schedule_of(row)


def test_the_readers_pass_badges_the_fields_it_filled(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    user = alice
    set_profile(factory, user, search(contract_types=["CDI"], days=7))
    job_id = reading.add_posting(factory, user, url=reading.OTHER_URL)
    reading.save(factory, user, job_id)  # published 2026-10-20, found 2026-11-09
    job = row_of(factory, job_id)
    assert (job.outside_criteria, job.contract_stated) == ([OLDER], None)
    contract = {"value": "CDD", "quote": "Durée : 12 mois"}
    reading.save(factory, user, job_id, contract_type=contract)
    job = row_of(factory, job_id)
    assert (job.contract_type, job.contract_stated) == (ContractType.CDD, True)
    assert job.outside_criteria == [CONTRACT, OLDER]


def test_the_persons_edit_of_the_contract_or_the_mode_badges_it_again(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(contract_types=["Freelance"], remote_stance="remote"))
    job_id = seed(factory, alice, contract_type=ContractType.CDI, contract_stated=None)
    with factory() as db:
        job = service.lock_job(db, alice, job_id)
        assert job is not None
        changes = service.JobChanges(fields={"work_mode": WorkMode.office})
        service.edit_job(db, job, changes, now())
        db.commit()
    assert row_of(factory, job_id).outside_criteria == [WORK_MODE]
    with factory() as db:
        job = service.lock_job(db, alice, job_id)
        assert job is not None
        service.edit_job(db, job, service.JobChanges(fields={"contract_type": "CDD"}), now())
        db.commit()
    job = row_of(factory, job_id)
    assert (job.contract_stated, job.outside_criteria) == (True, [CONTRACT, WORK_MODE])
