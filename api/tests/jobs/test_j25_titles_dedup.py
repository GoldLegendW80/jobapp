"""Jobs J25 (#817): clean posting titles, one row per job across boards.

The owner's sweep of 2026-11-19 (81ab9533, La Réunion, Guadeloupe, Tahiti) stored the same LHH
job twice: a Hellowork copy titled "Data Scientist H/F LHH Recruitment Solutions" with no
employer stated, and a Jobijoba copy titled "Data scientist h/f". The tables below are written
from the issue first (accept: what is taken out; refuse: what stays), then the store runs on the
sweep's real commits with invented copies of that run's pairs (`.test` hosts, nothing leaves the
test). Existing rows are never edited: a new copy of one is a duplicate naming its source."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import posting_titles as pt
from app.jobs import sweep
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from tests.jobs.sweep_helpers import (
    SITE,
    SITE_2,
    each_website,
    finding,
    jobs_of,
    new_sweep,
    now,
    run,
    search,
    set_profile,
)

HOST, HOST_2 = "jobs.example-robotics.test", "careers.example-labs.test"

# --- titles: the accept table (what is taken out) --------------------------------------------

CLEANED = [
    # (title as shown, company stated, location, cleaned title, company the title names)
    ("Data Scientist H/F LHH Recruitment Solutions", None, "Saint-Denis (974)", "Data Scientist",
     "LHH Recruitment Solutions"),
    ("Data scientist h/f", "LHH", "Saint-Denis, La Réunion", "Data scientist", None),
    ("Data Scientist (H/F)", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist (F/H)", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist F/H", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist M/F", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist (m/w/d)", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist F/H/X", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist (H/F/X)", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist [H/F]", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist H / F", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist (h/f)", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist, H/F", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist - H/F", "Example Lagoon", "", "Data Scientist", None),
    ("Ingénieur IA (H/F) CDI", "Example Lagoon", "", "Ingénieur IA CDI", None),
    ("Chef de projet Data & IA H/F", None, "Papeete", "Chef de projet Data & IA", None),
    ("Data Scientist (F/H) Emploi DomTom", "Emploi DomTom", "Saint-Denis 97400", "Data Scientist",
     None),
    ("Data Scientist H/F Example Lagoon", "Example Lagoon SAS", "", "Data Scientist", None),
    ("Data Scientist - Example Lagoon", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist | Example Lagoon", "Example Lagoon", "", "Data Scientist", None),
    ("Data Scientist – Example Lagoon", "Example Lagoon", "", "Data Scientist", None),
    ("Data Engineer (H/F) - Hellowork", "Example Lagoon", "", "Data Engineer", None),
    ("Data Engineer | Indeed.com", "Example Lagoon", "", "Data Engineer", None),
    ("Data Engineer - Jobijoba", None, "", "Data Engineer", None),
    ("Data Engineer H/F Hellowork", None, "", "Data Engineer", None),
    ("Offre d'emploi : Data Engineer (H/F)", "Example Lagoon", "", "Data Engineer", None),
    ("Offre d’emploi Data Engineer", "Example Lagoon", "", "Data Engineer", None),
    ("Data Scientist | Michael Page", None, "Tahiti", "Data Scientist", "Michael Page"),
    ("Data Scientist - Actual Group", None, "", "Data Scientist", "Actual Group"),
    ("Chef de projet IA H/F Michael Page", None, "Papeete", "Chef de projet IA", "Michael Page"),
    ("Data Scientist H/F Kaptiv", None, "Saint-Denis", "Data Scientist", "Kaptiv"),
    ("Data Scientist H/F Example Reef Analytics", None, "", "Data Scientist",
     "Example Reef Analytics"),
    ("Développeur C/C++ H/F", "Example Lagoon", "", "Développeur C/C++", None),
    ("Consultant H/F Data & IA", None, "", "Consultant Data & IA", None),
    ("  Data   Scientist   (H/F)  ", "Example Lagoon", "", "Data Scientist", None),
]  # fmt: skip


@pytest.mark.parametrize(("shown", "company", "location", "title", "named"), CLEANED)
def test_a_title_loses_its_markers_and_board_boilerplate(
    shown: str, company: str | None, location: str, title: str, named: str | None
) -> None:
    cleaned = pt.clean(shown, company, location)
    assert (cleaned.title, cleaned.company) == (title, named)
    assert cleaned.changed is (title != shown)


# --- titles: the refuse table (what stays) ----------------------------------------------------

KEPT = [
    ("Data Scientist", "Example Lagoon", ""),
    ("Chef de projet Data & IA", None, ""),
    ("Data Scientist - Saint-Denis", None, "Saint-Denis"),  # a place, not an employer
    ("Data Scientist - Saint-Denis", "Example Lagoon", "Saint-Denis"),
    ("Data Scientist - Paris", None, "Lyon"),  # a place the gazetteer knows
    ("Data Scientist - Example Lagoon", None, ""),  # no recruiter after a separator: kept
    ("Data Scientist - Example Lagoon", "Other Example", ""),  # another company than stated
    ("Ingénieur I/O embarqué", "Example Lagoon", ""),
    ("Développeur C/C++", "Example Lagoon", ""),
    ("Analyste B/C", "Example Lagoon", ""),
    ("Responsable R/D", "Example Lagoon", ""),
    ("Data Engineer w/f", "Example Lagoon", ""),  # not a marker of the allow list
    ("Data Engineer H/H", "Example Lagoon", ""),  # one letter twice
    ("Data Engineer XH/F", "Example Lagoon", ""),  # glued to a word
    ("Data Engineer - Full Stack", None, ""),
    ("Lead Data - Machine Learning", None, ""),
    ("Head of AI | Agents", None, ""),
    ("Ingénieur IA - CDI", None, ""),
    ("Emploi store manager", "Example Lagoon", ""),
    ("Offres", "Example Lagoon", ""),
    ("", "Example Lagoon", ""),
    ("H/F", "Example Lagoon", ""),  # nothing left with a letter: kept as shown
    ("(H/F)", None, ""),
    ("Data Scientist / Data Engineer", "Example Lagoon", ""),
]


@pytest.mark.parametrize(("shown", "company", "location"), KEPT)
def test_a_real_part_of_a_title_stays(shown: str, company: str | None, location: str) -> None:
    cleaned = pt.clean(shown, company, location)
    assert (cleaned.title, cleaned.company, cleaned.changed) == (shown, None, False)


def test_a_marker_after_a_kept_tail_still_goes_and_the_tail_stays() -> None:
    cleaned = pt.clean("Data Scientist H/F Data & IA", None, "")
    assert (cleaned.title, cleaned.company) == ("Data Scientist Data & IA", None)


def test_a_huge_title_is_read_in_its_first_characters_only() -> None:
    cleaned = pt.clean("Data " * 5000 + "(H/F)", "Example Lagoon", "")
    assert len(cleaned.title) <= pt.TITLE_MAX


# --- companies and recruiters -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "recruiter"),
    [
        ("LHH Recruitment Solutions", True),
        ("LHH", True),
        ("Michael Page", True),
        ("Actual Group", True),
        ("Kaptiv", True),
        ("Emploi DomTom", True),
        ("Example Recrutement", True),
        ("Example Interim", True),
        ("Example Lagoon", False),
        ("Example Reef Analytics", False),
        ("Page", False),
        ("", False),
        (None, False),
        (sweep.UNKNOWN_EMPLOYER, False),
    ],
)
def test_recruiters_are_told_by_name_or_agency_word(name: str | None, recruiter: bool) -> None:
    assert pt.is_recruiter(name) is recruiter


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("LHH Recruitment Solutions", "LHH", True),
        ("Example Lagoon SAS", "example lagoon", True),
        ("Actual Group", "Actual", True),
        ("Example Lagoon", "Example Reef", False),
        ("Michael Page", "Page Group", False),
        ("", "", False),
    ],
)
def test_a_company_is_the_same_without_its_generic_words(a: str, b: str, same: bool) -> None:
    assert pt.same_company(a, b) is same


# --- places -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("location", "key"),
    [
        ("Saint-Denis (974)", "saint denis|974"),
        ("Saint-Denis, La Réunion", "saint denis|974"),
        ("Saint-Denis, Réunion, France", "saint denis|974"),
        ("Saint-Denis 97400", "saint denis|974"),
        ("97400 Saint-Denis", "saint denis|974"),
        ("Saint-Denis (93)", "saint denis|93"),
        ("Saint-Denis 93200", "saint denis|93"),
        ("Saint-Denis", "saint denis|"),
        ("Pointe-à-Pitre, Guadeloupe", "pointe a pitre|971"),
        ("Papeete, Tahiti", "papeete|987"),
        ("Tahiti", "|987"),
        ("La Réunion", "|974"),
        ("Lyon, France", "lyon|"),
        ("", None),
        (None, None),
        ("France", None),
    ],
)
def test_a_place_is_its_town_and_its_departement(location: str | None, key: str | None) -> None:
    assert pt.place_key(location) == key


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        # The LHH pair and the Emploi DomTom pair of 81ab9533 (invented copies).
        (("LHH Recruitment Solutions", "Data Scientist", "Saint-Denis (974)"),
         ("LHH", "Data scientist h/f", "Saint-Denis, La Réunion"), True),
        (("Emploi DomTom", "Data Scientist (F/H) Emploi DomTom", "Saint-Denis 97400"),
         ("Emploi DomTom", "Data Scientist F/H", "Saint-Denis, Réunion"), True),
        # Another job of the same company, another town of the same name, another employer.
        (("LHH", "Data Scientist", "Saint-Denis (974)"),
         ("LHH", "Data Engineer", "Saint-Denis (974)"), False),
        (("LHH", "Data Scientist", "Saint-Denis (974)"),
         ("LHH", "Data Scientist", "Saint-Denis (93)"), False),
        (("LHH", "Data Scientist", "Saint-Denis (974)"),
         ("Example Lagoon", "Data Scientist", "Saint-Denis (974)"), False),
        (("LHH", "Data Scientist", "Saint-Denis (974)"),
         ("LHH", "Data Scientist", "Saint-Denis"), False),  # unsure of the place: two rows
    ],
)  # fmt: skip
def test_the_cross_board_key(first: tuple[str, ...], second: tuple[str, ...], same: bool) -> None:
    one, other = pt.cross_key(*first), pt.cross_key(*second)
    assert one is not None
    assert other is not None
    assert (one == other) is same


@pytest.mark.parametrize(
    ("company", "location"),
    [(None, "Saint-Denis (974)"), (sweep.UNKNOWN_EMPLOYER, "Saint-Denis (974)"), ("LHH", "")],
)
def test_no_cross_key_without_an_employer_or_a_place(company: str | None, location: str) -> None:
    assert pt.cross_key(company, "Data Scientist", location) is None


# --- the store --------------------------------------------------------------------------------

HELLOWORK = finding(
    "Data Scientist H/F LHH Recruitment Solutions",
    company=None,
    location="Saint-Denis (974)",
    url=f"https://{HOST}/emplois/data-scientist-4410",
    text="Invented Example Lagoon client builds models in Python.",
)
JOBIJOBA = finding(
    "Data scientist h/f",
    company="LHH",
    location="Saint-Denis, La Réunion",
    url=f"https://{HOST_2}/offres/data-scientist-77",
    text="Invented Example Lagoon client builds models in Python.",
)
DOMTOM_A = finding(
    "Data Scientist (F/H) Emploi DomTom",
    company="Emploi DomTom",
    location="Saint-Denis 97400",
    url=f"https://{HOST}/emplois/data-scientist-domtom-9",
)
DOMTOM_B = finding(
    "Data Scientist F/H",
    company="Emploi DomTom",
    location="Saint-Denis, Réunion",
    url=f"https://{HOST_2}/offres/data-scientist-domtom-3",
)
OTHER_JOB = finding(
    "Data Engineer H/F LHH Recruitment Solutions",
    company=None,
    location="Saint-Denis (974)",
    url=f"https://{HOST}/emplois/data-engineer-12",
)


def profile(factory: sessionmaker[Session], user: uuid.UUID) -> None:
    set_profile(
        factory,
        user,
        search(
            title_include={"terms": ["data"], "advanced_regex": False},
            interactive_sites=[SITE, SITE_2],
        ),
    )


def sweep_of(factory: sessionmaker[Session], user: uuid.UUID, *found: Any) -> Any:
    return run(factory, new_sweep(factory, user), browser=each_website(*found))


def events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[JobEvent]:
    with factory() as db:
        return list(db.scalars(sa.select(JobEvent).where(JobEvent.job_id == job_id)))


def test_the_lhh_pair_is_one_posting_with_two_sources(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    row = sweep_of(factory, alice, [HELLOWORK, OTHER_JOB], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (2, 1)
    jobs = {job.title: job for job in jobs_of(factory, alice)}
    assert set(jobs) == {"Data Scientist", "Data Engineer"}  # the other job stays apart
    job = jobs["Data Scientist"]
    assert job.company == "LHH Recruitment Solutions"
    assert job.url == HELLOWORK.url  # the first address stays the row's
    assert sweep.noted_sources(job.source) == [HOST, HOST_2]
    assert job.posting_text is not None
    assert job.posting_text.startswith(HELLOWORK.title)  # the title as shown is kept
    [found] = [e for e in events(factory, job.id) if e.kind is JobEventKind.sweep]
    discovery = found.data["discovery"]
    assert discovery["title_shown"] == HELLOWORK.title
    assert (discovery["company_from_title"], discovery["recruiter"]) == (True, True)
    assert "company" not in discovery["unknown"]


def test_the_emploi_domtom_pair_is_one_posting_across_two_sweeps(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    first = sweep_of(factory, alice, [DOMTOM_A])
    assert first.stats["new"] == 1
    again = sweep_of(factory, alice, [], [DOMTOM_B])
    assert (again.stats["new"], again.stats["duplicates"]) == (0, 1)
    [job] = jobs_of(factory, alice)
    assert (job.title, job.company) == ("Data Scientist", "Emploi DomTom")
    assert sweep.noted_sources(job.source) == [HOST, HOST_2]


def add_job(
    factory: sessionmaker[Session], user: uuid.UUID, days_ago: int, **values: Any
) -> uuid.UUID:
    job_id = uuid.uuid4()
    with factory() as db, db.begin():
        db.add(
            Job(
                id=job_id,
                user_id=user,
                slug=f"kept-{job_id.hex[:8]}",
                status=JobStatus.shortlisted,
                found_on=(now() - timedelta(days=days_ago)).date(),
                notes="mine",
                **values,
            )
        )
    return job_id


def test_an_existing_row_with_a_board_title_is_matched_and_left_as_it_is(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """A row stored before J25, its title as Hellowork wrote it and no employer stated: a new
    copy is its duplicate; the row keeps its title, company and status (no migration)."""
    profile(factory, alice)
    kept = add_job(
        factory, alice, 3, company=sweep.UNKNOWN_EMPLOYER, title=HELLOWORK.title,
        location=HELLOWORK.location, url="https://jobs.example-old.test/1", source="old",
    )  # fmt: skip
    row = sweep_of(factory, alice, [], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (0, 1)
    [job] = jobs_of(factory, alice)
    assert job.id == kept
    assert (job.title, job.company, job.status) == (
        HELLOWORK.title,
        sweep.UNKNOWN_EMPLOYER,
        JobStatus.shortlisted,
    )
    assert sweep.noted_sources(job.source) == ["old", HOST_2]


def test_a_copy_seen_after_the_window_is_another_posting(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    add_job(
        factory, alice, pt.WINDOW_DAYS + 30, company="LHH", title="Data Scientist",
        location="Saint-Denis (974)", url="https://jobs.example-old.test/2",
    )  # fmt: skip
    row = sweep_of(factory, alice, [], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (1, 0)


def test_two_unnamed_employers_with_the_same_clean_title_stay_two(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Fail closed: with no employer stated nor named by the title, only the address joins."""
    profile(factory, alice)
    one = finding("Data Scientist (H/F)", company=None, location="Saint-Denis (974)",
                  url=f"https://{HOST}/a")  # fmt: skip
    other = finding("Data Scientist", company=None, location="Saint-Denis (974)",
                    url=f"https://{HOST_2}/b")  # fmt: skip
    row = sweep_of(factory, alice, [one], [other])
    assert (row.stats["new"], row.stats["duplicates"]) == (2, 0)
    assert [j.title for j in jobs_of(factory, alice)] == ["Data Scientist", "Data Scientist"]
