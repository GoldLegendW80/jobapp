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
    # Round 1 of #818: a company only on positive evidence, a recruiter or an organisation word
    # beside a word of its own name; a board whose name is a word only with its domain.
    ("Data Scientist H/F Example Lagoon SAS", None, "", "Data Scientist", "Example Lagoon SAS"),
    ("Data Scientist H/F Example Conseil", None, "", "Data Scientist", "Example Conseil"),
    ("Data Scientist H/F Groupe Example", None, "", "Data Scientist", "Groupe Example"),
    ("Data Scientist (H/F) Example Labs", None, "", "Data Scientist", "Example Labs"),
    ("Data Scientist - Groupe Crit", None, "", "Data Scientist", "Groupe Crit"),
    ("Data Engineer - Talent.com", "Example Lagoon", "", "Data Engineer", None),
    ("Chef de projet - Example IA", "Example IA Solutions", "", "Chef de projet", None),
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
    # Round 1 of #818: a tail that merely shares the stated company's words is no company, a
    # board whose name is a title's word is no board, a short recruiter name alone no recruiter.
    ("Chef de projet - IA", "IA Solutions", ""),
    ("Ingénieur - Cloud", "Cloud Group", ""),
    ("Ingénieur | Data", "Data Group", ""),
    ("Data Scientist - Talent", "Example Lagoon", ""),
    ("Data Scientist - Talent", None, ""),
    ("Talent Acquisition Manager", None, ""),
    ("Data Scientist - Spring", None, ""),
    ("Data Scientist - Actual", None, ""),
    ("Data Scientist - Crit", None, ""),
    ("Data Scientist - Fed", None, ""),
]

# Round 1 of #818: with no employer stated, a tail after the last marker that is a qualifier of
# the title, a place, a sector or a kind of employer stays in the title (the marker goes).
TAILS = [
    ("Développeur Java H/F Expérimenté", "Développeur Java Expérimenté"),
    ("Stage Data Scientist H/F - 6 mois", "Stage Data Scientist 6 mois"),
    ("Data Engineer (H/F) Anglais courant", "Data Engineer Anglais courant"),
    ("Data Engineer H/F Bilingue", "Data Engineer Bilingue"),
    ("Data Engineer H/F Débutant accepté", "Data Engineer Débutant accepté"),
    ("Data Engineer H/F Réf 2024-118", "Data Engineer Réf 2024-118"),
    ("Data Engineer H/F Grand Est", "Data Engineer Grand Est"),
    ("Data Engineer H/F Cabinet", "Data Engineer Cabinet"),
    ("Data Engineer H/F Mission longue", "Data Engineer Mission longue"),
    ("Data Engineer H/F Start-up", "Data Engineer Start-up"),
    ("Data Engineer H/F Fintech", "Data Engineer Fintech"),
    ("Data Engineer H/F Santé", "Data Engineer Santé"),
    ("Data Engineer H/F Banque", "Data Engineer Banque"),
    ("Data Engineer H/F Conseil", "Data Engineer Conseil"),  # an organisation word alone
    ("Data Engineer H/F Groupe", "Data Engineer Groupe"),
    ("Data Engineer H/F SAS", "Data Engineer SAS"),
    ("Data Engineer H/F Cabinet Conseil", "Data Engineer Cabinet Conseil"),
    ("Data Engineer H/F Example Lagoon", "Data Engineer Example Lagoon"),  # no evidence
    ("Data Engineer H/F Spring", "Data Engineer Spring"),
]


@pytest.mark.parametrize(("shown", "title"), TAILS)
def test_a_tail_without_evidence_of_an_organisation_stays(shown: str, title: str) -> None:
    for location in ("", "Saint-Denis (974)"):
        cleaned = pt.clean(shown, None, location)
        assert (cleaned.title, cleaned.company) == (title, None)
        assert pt.clean(shown, sweep.UNKNOWN_EMPLOYER, location).company is None


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
        ("Spring", False),  # a short agency name alone: a stated employer of that name
        ("Actual", False),
        ("Crit", False),
        ("Fed", False),
        ("Spring SAS", False),
        ("Spring France", True),
        ("Groupe Crit", True),
        ("Fed Finance", True),
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
        ("Saint-Denis", None),  # the 93 or the 974: unknown
        ("Saint-Denis, France", None),
        ("Saint-Pierre", None),
        ("Saint-Pierre, Martinique", "saint pierre|972"),
        ("Lyon", "lyon|"),
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
        (("Example Lagoon", "AI Engineer 東京", "Paris"),
         ("Example Lagoon", "مهندس AI Engineer", "Paris"), False),  # every script counts
        (("Example Lagoon", "Ingénieur IA", "Paris"),
         ("example lagoon", "INGENIEUR IA (H/F)", "Paris, France"), True),
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


@pytest.mark.parametrize(
    "location", ["Saint-Denis", "Saint-Denis, France", "Saint-Pierre", "Le Port"]
)
def test_a_shared_town_without_its_departement_joins_nothing(location: str) -> None:
    """Round 1 of #818: "Saint-Denis" of the 93 and of the 974 never merge; a bare shared town
    is no place, so neither another bare one nor one naming a département joins it."""
    assert pt.cross_key("LHH", "Data Scientist", location) is None
    for coded in ("Saint-Denis (93)", "Saint-Denis (974)", "Saint-Pierre, Martinique"):
        assert pt.cross_key("LHH", "Data Scientist", coded) is not None


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


def test_the_title_as_shown_is_added_to_a_text_that_lacks_it_only(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    has = finding("DATA ENGINEER (H/F)", company="Example Lagoon", location="Saint-Denis (974)",
                  text="Offre: Data Engineer H/F. Python.")  # fmt: skip
    row = sweep_of(factory, alice, [has])
    assert row.stats["new"] == 1
    [job] = jobs_of(factory, alice)
    assert (job.title, job.posting_text) == ("DATA ENGINEER", "Offre: Data Engineer H/F. Python.")


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


# --- round 1 of #818: the newest row of a key, and the cleaned identity of stored rows ---------


def test_a_copy_is_compared_with_the_newest_row_of_its_key(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """A row found 90 days ago (past the window) and its later copy found 10 days ago: a third
    board's copy today is the recent row's duplicate, never a third row."""
    profile(factory, alice)
    old = dict(company="LHH", title="Data Scientist", location="Saint-Denis (974)")
    add_job(factory, alice, 90, url="https://jobs.example-old.test/90", source="old", **old)
    recent = add_job(
        factory, alice, 10, url="https://jobs.example-old.test/10", source="recent", **old
    )
    row = sweep_of(factory, alice, [], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (0, 1)
    jobs = {job.id: job for job in jobs_of(factory, alice)}
    assert len(jobs) == 2
    assert sweep.noted_sources(jobs[recent].source) == ["recent", HOST_2]


def test_two_copies_in_one_sweep_after_a_past_window_row_make_one_row(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    add_job(
        factory, alice, 90, company="LHH", title="Data Scientist", location="Saint-Denis (974)",
        url="https://jobs.example-old.test/90", source="old",
    )  # fmt: skip
    copy = finding(
        "Data Scientist (H/F)", company="LHH", location="Saint-Denis 97400",
        url=f"https://{HOST}/emplois/data-scientist-lhh-1",
    )  # fmt: skip
    row = sweep_of(factory, alice, [copy], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (1, 1)
    [new] = [job for job in jobs_of(factory, alice) if job.source != "old"]  # old left as is
    assert sorted(sweep.noted_sources(new.source)) == sorted([HOST, HOST_2])


@pytest.mark.parametrize(
    ("days_ago", "location"), [(3, ""), (90, ""), (90, "Saint-Denis (974)"), (3, "Lyon")]
)
def test_a_row_stored_before_j25_with_a_marker_still_matches_the_exact_rule(
    factory: sessionmaker[Session], alice: uuid.UUID, days_ago: int, location: str
) -> None:
    """A row stored as "Data Scientist H/F" (pre J25, no place or past the window, so no
    cross-board key joins it): the same posting at another address is its duplicate by the
    employer, title and location rule, the row left as it is."""
    profile(factory, alice)
    kept = add_job(
        factory, alice, days_ago, company="Example Lagoon", title="Data Scientist H/F",
        location=location, url="https://jobs.example-old.test/3", source="old",
    )  # fmt: skip
    copy = finding(
        "Data Scientist (H/F)", company="Example Lagoon", location=location,
        url=f"https://{HOST_2}/offres/data-scientist-lagoon",
    )  # fmt: skip
    row = sweep_of(factory, alice, [], [copy])
    assert (row.stats["new"], row.stats["duplicates"]) == (0, 1)
    [job] = jobs_of(factory, alice)
    assert (job.id, job.title) == (kept, "Data Scientist H/F")
    assert sweep.noted_sources(job.source) == ["old", HOST_2]


def test_a_row_whose_title_named_its_employer_still_matches_the_exact_rule(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    """Pre J25, no employer stated and the agency in the title, no place: its copy is still one
    row (the cleaned title and the company it names)."""
    profile(factory, alice)
    kept = add_job(
        factory, alice, 3, company=sweep.UNKNOWN_EMPLOYER, title=HELLOWORK.title, location="",
        url="https://jobs.example-old.test/4", source="old",
    )  # fmt: skip
    copy = finding(
        HELLOWORK.title, company=None, location="", url=f"https://{HOST_2}/offres/lhh-ds"
    )
    row = sweep_of(factory, alice, [], [copy])
    assert (row.stats["new"], row.stats["duplicates"]) == (0, 1)
    [job] = jobs_of(factory, alice)
    assert job.id == kept
