"""The merge of an English CV and a French profile export that tell the same facts (owner,
2026-11-08): values that agree become one proposal (rule A), one entry written two ways is one
group (rule B), and a figure a document states beats the derived years (rule 7). On a real
Postgres, the real engine driving the run. Invented person and organisations only."""

import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import ProposalStatus
from app.config import Settings
from app.ingestion import items
from tests.ingestion import merge_helpers as mh
from tests.ingestion.extract_helpers import key, keyring, settings
from tests.ingestion.test_item_proposals import accept, bulk, row, stored, undo
from tests.ingestion.test_merge import alice

_FIXTURES = (alice, key, keyring, settings)
Factory = sessionmaker[Session]

CV = (
    "Jane-Ann Doe\n"
    "GenAI Engineer\n"
    "Phone: 06 12 34 56 78\n"
    "NOVACARE, Software Engineer, March 2019 to June 2021\n"
    "Example Robotics, Self-employed, since 2021\n"
    "ITECHNOVA - Institute of Applied Computing, 2015 to 2018\n"
    "Université de Valmont Haute-Rive, 2011 to 2015\n"
    "Lycée Jean Morvel Exempleville, 2009 to 2011\n"
    "Languages: English, French, Spanish\n"
    "Skills: Rust\n"
)
LINKEDIN = (
    "JANE-ANN DOE\n"
    "Ingénieure logicielle passionnée par l'IA générative et les systèmes distribués\n"
    "+33 6 12 34 56 78\n"
    "NOVACARE GIE, Software Engineer, Port-Exemple, France, mars 2019 à juin 2021\n"
    "Example Robotics, Auto-entrepreneur, depuis 2021\n"
    "ITECHNOVA – Institute of Applied Computing, octobre 2015 à juin 2018\n"
    "University of Valmont Haute-Rive, 2011 à 2015\n"
    "Lycée Jean Morvel, 2009 à 2011\n"
    "Langues : Anglais, Français, Espagnol\n"
    "Compétences : Rust\n"
    "Trois ans et demi d'ingénierie logicielle\n"
)


def job(organisation: str, start: str, end: str | None) -> dict[str, Any]:
    return {"organisation": organisation, "title": "Software Engineer", "start": start, "end": end}


def two(
    factory: Factory,
    user: uuid.UUID,
    settings: Settings,
    cv: list[mh.Fact],
    linkedin: list[mh.Fact],
) -> list[Any]:
    run_id, _ = mh.merged(factory, user, settings, [CV, LINKEDIN], [cv, linkedin])
    return mh.proposals_of(factory, user, run_id)


def without_years(found: list[Any]) -> list[Any]:
    """The proposals but the derived years, which any employer of a run brings."""
    return [one for one in found if one.payload["key"] != "years_experience"]


def documents_of(proposal: Any) -> int:
    return len({one["document_id"] for one in proposal.payload["citations"]})


# --- rule A: values that agree --------------------------------------------------------------------


def test_one_name_in_capitals_in_one_file_is_one_proposal_with_both_sources(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = two(
        factory,
        alice,
        settings,
        [("identity", "full_name", "Jane-Ann Doe", "Jane-Ann Doe")],
        [("identity", "full_name", "JANE-ANN DOE", "JANE-ANN DOE")],
    )
    assert one.payload["value"] == "Jane-Ann Doe"
    assert one.payload["flags"] == []
    assert "conflict" not in one.payload
    assert documents_of(one) == 2
    assert one.payload["value_hash"] == items.value_hash("Jane-Ann Doe")


def test_one_phone_written_two_ways_is_one_proposal_in_its_international_form(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = two(
        factory,
        alice,
        settings,
        [("identity", "phone", "06 12 34 56 78", "06 12 34 56 78")],
        [("identity", "phone", "+33 6 12 34 56 78", "+33 6 12 34 56 78")],
    )
    assert (one.payload["value"], one.payload["flags"]) == ("+33 6 12 34 56 78", [])
    assert documents_of(one) == 2


def test_a_location_one_file_leaves_out_is_filled_from_the_other(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = without_years(
        two(
            factory,
            alice,
            settings,
            [
                (
                    "experience",
                    "employers",
                    job("NOVACARE", "2019-03", "2021-06"),
                    "NOVACARE, Software",
                )
            ],
            [
                (
                    "experience",
                    "employers",
                    {**job("NOVACARE", "2019-03", "2021-06"), "location": "Port-Exemple, France"},
                    "Port-Exemple, France",
                )
            ],
        )
    )
    assert one.payload["key"] == "employers:novacare-2019"
    assert one.payload["value"]["location"] == "Port-Exemple, France"
    assert one.payload["flags"] == []
    assert documents_of(one) == 2


def test_a_skill_whose_terms_one_file_leaves_empty_takes_the_other_terms(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = two(
        factory,
        alice,
        settings,
        [("skills", "have", {"name": "Rust", "terms": ["Rust"]}, "Skills: Rust")],
        [("skills", "have", {"name": "Rust", "terms": []}, "Compétences : Rust")],
    )
    assert (one.payload["key"], one.payload["value"]["terms"]) == ("have:rust", ["Rust"])
    assert one.payload["flags"] == []


def test_a_real_disagreement_stays_a_conflict(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    long = "Ingénieure logicielle passionnée par l'IA générative et les systèmes distribués"
    title = "Example Robotics, Self-employed"
    found = two(
        factory,
        alice,
        settings,
        [
            ("identity", "headline", "GenAI Engineer", "GenAI Engineer"),
            (
                "experience",
                "employers",
                {**job("Example Robotics", "2021", None), "title": "Self-employed"},
                title,
            ),
        ],
        [
            ("identity", "headline", long, long),
            (
                "experience",
                "employers",
                {**job("Example Robotics", "2021", None), "title": "Auto-entrepreneur"},
                "Example Robotics, Auto-entrepreneur",
            ),
        ],
    )
    assert len(found) == 2
    for one in found:
        assert one.payload["flags"] == [items.FLAG_CONFLICT]
        assert len(one.payload["conflict"]) == 1


# --- rule B: one entry under two keys -------------------------------------------------------------


def test_an_organisation_and_its_longer_name_over_the_same_dates_are_one_job(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = without_years(
        two(
            factory,
            alice,
            settings,
            [
                (
                    "experience",
                    "employers",
                    job("NOVACARE", "2019-03", "2021-06"),
                    "NOVACARE, Software",
                )
            ],
            [
                (
                    "experience",
                    "employers",
                    job("NOVACARE GIE", "2019-03", "2021-06"),
                    "NOVACARE GIE",
                )
            ],
        )
    )
    assert (one.payload["key"], one.payload["value"]["organisation"]) == (
        "employers:novacare-gie-2019",
        "NOVACARE GIE",
    )
    assert one.payload["flags"] == []
    assert documents_of(one) == 2


def test_two_schools_written_two_ways_are_one_entry_each(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    itechnova = "Institute of Applied Computing"
    found = two(
        factory,
        alice,
        settings,
        [
            (
                "experience",
                "education",
                {
                    "degree": "Master",
                    "school": f"ITECHNOVA - {itechnova}",
                    "start": "2015",
                    "end": "2018",
                },
                "ITECHNOVA - ",
            ),
            (
                "experience",
                "education",
                {
                    "degree": "Master",
                    "school": "Université de Valmont Haute-Rive",
                    "start": "2011",
                    "end": "2015",
                },
                "Université de Valmont",
            ),
            (
                "experience",
                "education",
                {
                    "degree": "Master",
                    "school": "Lycée Jean Morvel Exempleville",
                    "start": "2009",
                    "end": "2011",
                },
                "Lycée Jean Morvel Exempleville",
            ),
        ],
        [
            (
                "experience",
                "education",
                {
                    "degree": "Master",
                    "school": f"ITECHNOVA – {itechnova}",
                    "start": "2015-10",
                    "end": "2018-06",
                },
                "ITECHNOVA –",
            ),
            (
                "experience",
                "education",
                {
                    "degree": "Master",
                    "school": "University of Valmont Haute-Rive",
                    "start": "2011",
                    "end": "2015",
                },
                "University of Valmont",
            ),
            (
                "experience",
                "education",
                {"degree": "Master", "school": "Lycée Jean Morvel", "start": "2009", "end": "2011"},
                "Lycée Jean Morvel, 2009",
            ),
        ],
    )
    assert len(found) == 3
    by_school = {one.payload["value"]["school"].split()[0]: one for one in found}
    # The month of one start and the year alone of the other are two values: one conflict.
    assert by_school["ITECHNOVA"].payload["flags"] == [items.FLAG_CONFLICT]
    # Two spellings of one university over the same years: one conflict, never two schools.
    valmont = next(one for one in found if "Valmont" in one.payload["value"]["school"])
    assert valmont.payload["flags"] == [items.FLAG_CONFLICT]
    # A school inside the other's name over the same years: one school, its longer name.
    lycee = by_school["Lycée"].payload
    assert (lycee["value"]["school"], lycee["value"]["start"], lycee["value"]["end"]) == (
        "Lycée Jean Morvel Exempleville",
        "2009",
        "2011",
    )
    assert lycee["flags"] == []


def test_a_school_without_years_under_a_shorter_name_stays_its_own_entry(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    town = "Lycée Jean Morvel Exempleville"
    found = two(
        factory,
        alice,
        settings,
        [
            (
                "experience",
                "education",
                {"degree": "Bac", "school": town, "start": "2009", "end": "2011"},
                town,
            )
        ],
        [("experience", "education", {"degree": "Bac", "school": "Lycée Jean Morvel"}, "Lycée")],
    )
    assert sorted(one.payload["value"]["school"] for one in found) == ["Lycée Jean Morvel", town]
    assert all(one.payload["flags"] == [] for one in found)


VALMONT = "Université de Valmont Haute-Rive"
UNDATED_CV = f"Jane-Ann Doe\n{VALMONT}, Master\nNOVACARE, Software Engineer\n"
TWO_DEGREES = (
    f"{VALMONT}, Licence, 2011 à 2014\n"
    f"{VALMONT}, Master, 2014 à 2016\n"
    "NOVACARE GIE, Software Engineer, mars 2019 à juin 2021\n"
    "NOVACARE SANTÉ, Software Engineer, mars 2019 à juin 2021\n"
)


def degree(name: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    return {"degree": name, "school": VALMONT, "start": start, "end": end}


def chain(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> tuple[uuid.UUID, list[uuid.UUID], list[Any]]:
    """A CV that names a university and an employer once, and an export that gives two degrees
    there and two employers whose names the CV's starts: each is like the CV's entry, not like
    the other."""
    run_id, documents = mh.merged(
        factory,
        alice,
        settings,
        [UNDATED_CV, TWO_DEGREES],
        [
            [
                ("experience", "education", degree("Master"), f"{VALMONT}, Master"),
                (
                    "experience",
                    "employers",
                    job("NOVACARE", "2019-03", "2021-06"),
                    "NOVACARE, Software",
                ),
            ],
            [
                ("experience", "education", degree("Licence", "2011", "2014"), "Licence, 2011"),
                ("experience", "education", degree("Master", "2014", "2016"), "Master, 2014"),
                (
                    "experience",
                    "employers",
                    job("NOVACARE GIE", "2019-03", "2021-06"),
                    "NOVACARE GIE",
                ),
                (
                    "experience",
                    "employers",
                    job("NOVACARE SANTÉ", "2019-03", "2021-06"),
                    "NOVACARE SANTÉ",
                ),
            ],
        ],
    )
    return run_id, documents, without_years(mh.proposals_of(factory, alice, run_id))


def test_an_entry_like_two_entries_that_are_not_one_joins_neither(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    _, _, found = chain(factory, alice, settings)
    schools = [one.payload for one in found if one.payload["key"].startswith("education:")]
    assert sorted((one["value"]["start"] or "", one["value"]["degree"]) for one in schools) == [
        ("", "Master"),
        ("2011", "Licence"),
        ("2014", "Master"),
    ]
    jobs = [one.payload for one in found if one.payload["key"].startswith("employers:")]
    assert sorted(one["value"]["organisation"] for one in jobs) == [
        "NOVACARE",
        "NOVACARE GIE",
        "NOVACARE SANTÉ",
    ]
    for one in schools + jobs:
        assert one["flags"] == []
        assert "conflict" not in one


def test_the_wizard_filling_empty_slots_keeps_both_dated_degrees(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, documents, found = chain(factory, alice, settings)
    result = bulk(
        factory,
        alice,
        proposal_ids=[one.id for one in found],
        ingestion_run_id=run_id,
        fill_empty=[str(one) for one in documents],
    )
    assert result.done
    kept = [
        (one["start"], one["end"])
        for one in stored(factory, alice, "experience").get("education") or []
    ] + [
        (one.payload["value"]["start"], one.payload["value"]["end"])
        for one in mh.proposals_of(factory, alice, run_id)
        if one.payload["key"].startswith("education:")
    ]
    assert {("2011", "2014"), ("2014", "2016")} <= set(kept)
    turned_down = mh.proposals_of(factory, alice, run_id, ProposalStatus.rejected)
    assert turned_down == []
    assert all(row(factory, one.id).edited_value is None for one in found)


def test_three_entries_that_are_each_one_entry_with_the_others_are_one_group(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    (one,) = without_years(
        two(
            factory,
            alice,
            settings,
            [
                (
                    "experience",
                    "employers",
                    job("NOVACARE", "2019-03", "2021-06"),
                    "NOVACARE, Software",
                )
            ],
            [
                (
                    "experience",
                    "employers",
                    job("NOVACARE GIE", "2019-03", "2021-06"),
                    "NOVACARE GIE",
                ),
                (
                    "experience",
                    "employers",
                    job("NOVACARE GIE SUD", "2019-03", "2021-06"),
                    "NOVACARE GIE, Software",
                ),
            ],
        )
    )
    assert (one.payload["value"]["organisation"], one.payload["flags"]) == ("NOVACARE GIE SUD", [])
    assert documents_of(one) == 2


def test_the_items_like_an_entry_of_the_profile_but_not_one_another_stay_additions(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    mh.save(factory, alice, "experience", {"education": [degree("Master")]})
    run_id, _ = mh.merged(
        factory,
        alice,
        settings,
        [TWO_DEGREES],
        [
            [
                ("experience", "education", degree("Licence", "2011", "2014"), "Licence, 2011"),
                ("experience", "education", degree("Master", "2014", "2016"), "Master, 2014"),
            ]
        ],
    )
    found = mh.proposals_of(factory, alice, run_id)
    assert sorted(one.payload["key"] for one in found) == [
        "education:université de valmont haute-rive:2011",
        "education:université de valmont haute-rive:2014",
    ]
    assert stored(factory, alice, "experience")["education"][0]["start"] is None


def test_three_languages_named_in_two_languages_are_three_languages(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    found = two(
        factory,
        alice,
        settings,
        [
            ("identity", "languages", {"name": name}, "Languages: English")
            for name in ("English", "French", "Spanish")
        ],
        [
            ("identity", "languages", {"name": name}, "Langues : Anglais")
            for name in ("Anglais", "Français", "Espagnol")
        ],
    )
    assert sorted(one.payload["value"]["name"] for one in found) == ["English", "French", "Spanish"]
    assert all(one.payload["flags"] == [] and documents_of(one) == 2 for one in found)


def test_an_entry_the_profile_holds_under_another_name_is_updated_never_doubled(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    held = {**job("NOVACARE", "2019-03", "2021-06"), "key": "novacare-kept"}
    mh.save(factory, alice, "experience", {"employers": [held]})
    mh.save(
        factory,
        alice,
        "identity",
        {"full_name": "Jane-Ann Doe", "languages": [{"name": "English", "level": "C1"}]},
    )
    found = two(
        factory,
        alice,
        settings,
        [("identity", "full_name", "JANE-ANN DOE", "Jane-Ann Doe")],
        [
            ("identity", "full_name", "JANE-ANN DOE", "JANE-ANN DOE"),
            ("identity", "languages", {"name": "Anglais", "level": "C1"}, "Langues : Anglais"),
            (
                "experience",
                "employers",
                {**job("NOVACARE GIE", "2019-03", "2021-06"), "location": "Port-Exemple, France"},
                "NOVACARE GIE",
            ),
        ],
    )
    # The name and the language agree with the profile: nothing to propose for them.
    (one,) = without_years(found)
    assert one.payload["key"] == "employers:novacare-2019"
    assert one.payload["value"]["organisation"] == "NOVACARE"
    assert one.payload["value"]["location"] == "Port-Exemple, France"
    assert "key" not in one.payload["value"]  # the slug of a stored employer is never proposed
    accept(factory, alice, one.id)
    [kept] = stored(factory, alice, "experience")["employers"]
    assert (kept["key"], kept["organisation"], kept["location"]) == (
        "novacare-kept",
        "NOVACARE",
        "Port-Exemple, France",
    )
    undo(factory, alice, one.id)
    assert stored(factory, alice, "experience")["employers"][0]["location"] is None


def test_a_school_whose_start_a_document_fills_keeps_its_review_and_its_undo(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    mh.save(
        factory,
        alice,
        "experience",
        {"education": [{"degree": "Master", "school": "Lycée Jean Morvel"}]},
    )
    (one,) = two(
        factory,
        alice,
        settings,
        [
            (
                "experience",
                "education",
                {"degree": "Master", "school": "LYCÉE JEAN MORVEL", "start": "2009", "end": "2011"},
                "Lycée Jean Morvel Exempleville",
            )
        ],
        [],
    )
    assert one.payload["key"] == "education:lycée jean morvel:"
    assert one.payload["value"]["start"] == "2009"
    accept(factory, alice, one.id)
    [school] = stored(factory, alice, "experience")["education"]
    assert (school["school"], school["start"]) == ("Lycée Jean Morvel", "2009")
    undo(factory, alice, one.id)
    assert stored(factory, alice, "experience")["education"][0]["start"] is None
    assert row(factory, one.id).status is ProposalStatus.undone


def test_a_value_rejected_under_its_own_key_is_not_proposed_again_under_the_group_key(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    facts = [
        [("experience", "employers", job("NOVACARE", "2019-03", "2021-06"), "NOVACARE, Software")],
        [("experience", "employers", job("NOVACARE GIE", "2019-03", "2021-06"), "NOVACARE GIE")],
    ]
    first, _ = mh.merged(factory, alice, settings, [f"{CV}An earlier copy.\n"], [facts[0]])
    (proposal,) = without_years(mh.proposals_of(factory, alice, first))
    with factory() as db, db.begin():
        proposals.reject(db, alice, proposal.id, mh.now())
    run_id, _ = mh.merged(factory, alice, settings, [CV, LINKEDIN], facts)
    (again,) = without_years(mh.proposals_of(factory, alice, run_id))
    assert again.payload["value"]["organisation"] == "NOVACARE GIE"


# --- rule 7: the years a document states ----------------------------------------------------------


def test_a_stated_figure_is_proposed_and_nothing_is_derived(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    found = two(
        factory,
        alice,
        settings,
        [("experience", "employers", job("NOVACARE", "2014-03", "2021-06"), "NOVACARE, Software")],
        [("skills", "years_experience", 3.5, "Trois ans et demi d'ingénierie logicielle")],
    )
    years = mh.at(found, "years_experience")
    assert (years.payload["value"], years.payload["flags"]) == (3.5, [])
    assert "depends_on" not in years.payload


def test_without_a_stated_figure_the_years_are_derived_once_for_one_job_written_twice(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    found = two(
        factory,
        alice,
        settings,
        [("experience", "employers", job("NOVACARE", "2019-03", "2021-03"), "NOVACARE, Software")],
        [("experience", "employers", job("NOVACARE GIE", "2019-03", "2021-03"), "NOVACARE GIE")],
    )
    years = mh.at(found, "years_experience")
    assert (years.payload["value"], years.payload["flags"]) == (2.0, [items.FLAG_DERIVED])


def test_an_item_like_two_entries_of_the_profile_stays_under_its_own_key(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    held = [
        {**job("NOVACARE", "2019-03", "2021-06"), "key": "novacare"},
        {**job("NOVACARE GIE", "2019-03", "2021-06"), "key": "novacare-gie"},
    ]
    mh.save(factory, alice, "experience", {"employers": held})
    mh.save(factory, alice, "skills", {"years_experience": 2.5})
    found = two(
        factory,
        alice,
        settings,
        [
            (
                "experience",
                "employers",
                job("NOVACARE GIE SUD", "2019-03", "2021-06"),
                "NOVACARE, Software",
            )
        ],
        [
            (
                "experience",
                "employers",
                job("NOVACARE GIE SUD", "2019-03", "2021-06"),
                "NOVACARE GIE",
            )
        ],
    )
    (one,) = found
    assert one.payload["key"] == "employers:novacare-gie-sud-2019"
    assert len(stored(factory, alice, "experience")["employers"]) == 2
