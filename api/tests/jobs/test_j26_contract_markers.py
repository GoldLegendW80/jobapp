"""Jobs J26 (#819): contract markers out of posting titles, the contract type kept.

The owner's sweep of 2026-11-19 (f769661a, La Réunion) stored Jobijoba's "Data scientist -
(cdi)" of LHH as a new posting beside the LHH job already stored ("Data Scientist H/F"): title
cleaning took gender markers out but not contract markers, so the cross-board key differed. The
tables are written from the issue first (accept: what is taken out; refuse: what stays), then
the store runs on the fakes of tests/jobs/sweep_helpers.py."""

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import posting_titles as pt
from app.jobs import sweep
from app.jobs.models import ContractType
from tests.jobs.sweep_helpers import (
    SITE,
    SITE_2,
    each_website,
    finding,
    jobs_of,
    new_sweep,
    run,
    search,
    set_profile,
)

HOST, HOST_2 = "jobs.example-robotics.test", "careers.example-labs.test"
REUNION = "Saint-Denis (974)"

# --- the accept table: (title as shown, cleaned title, the contract words taken out) -----------

TAKEN = [
    # The production pair of f769661a (invented copy).
    ("Data scientist - (cdi)", "Data scientist", "cdi"),
    ("Data Scientist (CDI)", "Data Scientist", "CDI"),
    ("Data Scientist (Cdi)", "Data Scientist", "Cdi"),
    ("Data Scientist [CDI]", "Data Scientist", "CDI"),
    ("Data Scientist ( CDI )", "Data Scientist", "CDI"),
    ("Data Scientist - CDI", "Data Scientist", "CDI"),
    ("Data Scientist – CDI", "Data Scientist", "CDI"),
    ("Data Scientist | CDI", "Data Scientist", "CDI"),
    ("Data Scientist, CDI", "Data Scientist", "CDI"),
    ("Data Scientist / CDI", "Data Scientist", "CDI"),
    ("CDI - Data Scientist", "Data Scientist", "CDI"),
    ("cdi - data scientist", "data scientist", "cdi"),
    ("CDI : Data Scientist", "Data Scientist", "CDI"),
    ("(CDI) Data Scientist", "Data Scientist", "CDI"),
    ("Data Scientist (Contrat CDI)", "Data Scientist", "Contrat CDI"),
    ("Data Scientist en CDI", "Data Scientist", "en CDI"),
    ("Data analyst (CDD)", "Data analyst", "CDD"),
    ("[CDD] Data analyst", "Data analyst", "CDD"),
    ("Data analyst CDD 6 mois", "Data analyst", "CDD 6 mois"),
    ("Data analyst - CDD 6 mois", "Data analyst", "CDD 6 mois"),
    ("Data analyst (CDD de 12 mois)", "Data analyst", "CDD de 12 mois"),
    ("Data analyst en CDD", "Data analyst", "en CDD"),
    ("Data analyst (Stage)", "Data analyst", "Stage"),
    ("Stage - Data analyst", "Data analyst", "Stage"),
    ("Data analyst (Stage 6 mois) H/F", "Data analyst", "Stage 6 mois"),
    ("Data analyst (Alternance)", "Data analyst", "Alternance"),
    ("ALTERNANCE - Data analyst", "Data analyst", "ALTERNANCE"),
    ("Développeur Python (Freelance)", "Développeur Python", "Freelance"),
    ("Développeur Python - freelance", "Développeur Python", "freelance"),
    ("Opérateur logistique (Intérim)", "Opérateur logistique", "Intérim"),
    ("Opérateur logistique (interim)", "Opérateur logistique", "interim"),
    ("Data scientist (VIE)", "Data scientist", "VIE"),
    ("Data scientist - V.I.E.", "Data scientist", "V.I.E."),
    ("Data scientist (CDI ou CDD)", "Data scientist", "CDI ou CDD"),
    ("Data scientist (CDI/CDD)", "Data scientist", "CDI/CDD"),
    # With J25's cleaning around it.
    ("Data Scientist H/F - CDI", "Data Scientist", "CDI"),
    ("Data Scientist - CDI H/F", "Data Scientist", "CDI"),
    ("Data Scientist (CDI) H/F", "Data Scientist", "CDI"),
    ("Data Scientist - CDI - Hellowork", "Data Scientist", "CDI"),
    ("Offre d'emploi : CDI - Data Scientist", "Data Scientist", "CDI"),
    ("Data Scientist - CDI - Saint-Denis", "Data Scientist - Saint-Denis", "CDI"),
]


@pytest.mark.parametrize(("shown", "title", "contract"), TAKEN)
def test_a_contract_marker_is_taken_out_and_kept_for_the_contract(
    shown: str, title: str, contract: str
) -> None:
    cleaned = pt.clean(shown, "LHH", REUNION)
    assert (cleaned.title, cleaned.contract, cleaned.changed) == (title, contract, True)


# --- the refuse table: a contract word that is part of the title stays ---------------------

KEPT = [
    "Juriste contrats",
    "Gestionnaire CDD",
    "Responsable des contrats CDI",
    "Gestionnaire paie intérim",
    "Directeur d'agence intérim",
    "Responsable alternance",
    "Chargée des relations écoles et stages",
    "Stage Manager",
    "Freelance Writer",
    "Coordinateur VIE",
    "Actuaire (Vie)",  # "vie": a word of titles, VIE in capitals only
    "Actuaire - Vie",
    "Assurance vie, conseiller",
    "Data Scientist CDI",  # a bare word beside the title's own: fail closed
    "CDI Data Scientist",
    "Data Scientist (Cdiscount)",
    "Data Scientist - CDIscount",
    "Data Scientist (CDI Lab)",
    "Data Scientist - Stage de fin d'études",
    "Data Scientist (Stagiaire)",
    "Data Scientist - 6 mois",
    "Responsable stages - Campus",
    "Data Scientist en CDI à Lyon",
    "Chargé de recrutement CDI/CDD",
    "CDI",  # nothing left: kept as it was
    "(CDD)",
]


@pytest.mark.parametrize("shown", KEPT)
def test_a_contract_word_of_a_real_title_stays(shown: str) -> None:
    cleaned = pt.clean(shown, "Example Lagoon", REUNION)
    assert (cleaned.title, cleaned.contract) == (shown, "")


def test_the_production_pair_has_one_cross_board_key() -> None:
    first = pt.cross_key("LHH", "Data Scientist H/F", "Saint-Denis, La Réunion")
    assert first is not None
    assert pt.cross_key("LHH", "Data scientist - (cdi)", REUNION) == first
    assert pt.cross_key("LHH Recruitment Solutions", "Data Scientist (CDD)", REUNION) == first
    assert pt.cross_key("LHH", "Data Engineer - CDI", REUNION) != first


# --- the store --------------------------------------------------------------------------------

STORED = finding(
    "Data Scientist H/F",
    company="LHH",
    location="Saint-Denis, La Réunion",
    url=f"https://{HOST}/emplois/data-scientist-4410",
    text="Invented Example Lagoon client builds models in Python.",
)
JOBIJOBA = finding(
    "Data scientist - (cdi)",
    company="LHH",
    location=REUNION,
    url=f"https://{HOST_2}/offres/data-scientist-77",
    text="Invented Example Lagoon client builds models in Python.",
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


def test_the_lhh_pair_of_f769661a_is_one_posting(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    profile(factory, alice)
    row = sweep_of(factory, alice, [STORED], [JOBIJOBA])
    assert (row.stats["new"], row.stats["duplicates"]) == (1, 1)
    [job] = jobs_of(factory, alice)
    assert (job.title, job.company) == ("Data Scientist", "LHH")
    assert sweep.noted_sources(job.source) == [HOST, HOST_2]


@pytest.mark.parametrize(
    ("shown", "contract"),
    [
        ("Data analyst (CDD)", ContractType.CDD),
        ("Data analyst - CDD 6 mois", ContractType.CDD),
        ("Data analyst (Stage)", ContractType.Stage),
        ("Alternance - Data analyst", ContractType.Alternance),
        ("Data analyst (Freelance)", ContractType.Freelance),
        ("Data analyst (VIE)", ContractType.VIE),
        ("Data analyst - (cdi)", ContractType.CDI),
    ],
)
def test_the_stored_title_loses_the_marker_and_the_row_keeps_its_contract(
    factory: sessionmaker[Session], alice: uuid.UUID, shown: str, contract: ContractType
) -> None:
    profile(factory, alice)
    found = finding(shown, company="Example Lagoon", location=REUNION,
                    text="Invented Example Lagoon analysis of reef data.")  # fmt: skip
    sweep_of(factory, alice, [found])
    [job] = jobs_of(factory, alice)
    assert (job.title, job.contract_type) == ("Data analyst", contract)
    assert job.posting_text is not None
    assert job.posting_text.startswith(shown)  # the title as shown stays in the text
