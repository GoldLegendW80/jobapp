"""The sweep and apply must read the person's exclusion rules alike (#580), and those rules are
the only ones: the app adds none of its own about a kind of role (owner, 2026-11-05)."""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.apply import guardrails
from app.jobs import sweep
from app.jobs.models import JobEvent, JobStatus
from tests.apply.test_a2_guardrails import (
    HIDDEN_FROM_THE_RULE,
    KEPT_BY_OWNER,
    OWNER,
    REFUSED_BY_OWNER,
    REVIEWER_INPUTS,
    TRUE_TITLES,
)
from tests.jobs.sweep_helpers import finding, jobs_of, new_sweep, run, search, set_profile


@pytest.mark.parametrize("title", REFUSED_BY_OWNER + HIDDEN_FROM_THE_RULE)
def test_the_sweep_refuses_every_role_apply_refuses(title: str) -> None:
    assert OWNER.matches("Example Robotics", title)


@pytest.mark.parametrize("title", KEPT_BY_OWNER + TRUE_TITLES)
def test_the_sweep_preserves_the_same_real_titles(title: str) -> None:
    assert not OWNER.matches("Example Robotics", title)


@pytest.mark.parametrize(
    ("round_", "rule", "title", "refused"), REVIEWER_INPUTS, ids=range(len(REVIEWER_INPUTS))
)
def test_every_reviewed_rule_case_has_the_same_outcome(
    round_: str, rule: sweep.NeverApplyRule, title: str, refused: bool
) -> None:
    assert rule.matches("Example Robotics", title) is refused


@pytest.mark.parametrize("company", ["Northwind Secu\u00adrity", "Northwind Ѕесurity"])
def test_the_sweep_also_folds_the_company(company: str) -> None:
    assert OWNER.matches(company, "Applied AI Engineer")


def test_hidden_exclusions_are_stored_skipped_with_the_rule_and_its_events(
    factory: sessionmaker, alice: uuid.UUID
) -> None:
    titles = [
        "AI Engineer, Secu\u00adrity",
        "AI Engineer, Pen\u200btest",
        "AI Engineer, Ѕесurity",
        "AI Engineer, Ｓｅｃｕｒｉｔｙ",
        "AI Engineer, AppSecurity",
        "AI Engineer, Cybersecurity",
        "AI Engineer, Securities Trading",
    ]
    found = [
        finding(
            title,
            company="Example Robotics",
            location="Paris",
            url=f"https://jobs.example-robotics.test/jobs/{i}",
            text="Build agents with Python.",
        )
        for i, title in enumerate(titles)
    ]
    set_profile(factory, alice, search(never_apply=[OWNER.text]))
    row = run(factory, new_sweep(factory, alice), *found)
    assert (row.stats["new"], row.stats["skipped_by_rule"]) == (7, 6)
    by_title = {posting.title: posting for posting in jobs_of(factory, alice)}
    assert by_title[titles[-1]].status is JobStatus.found
    with factory() as db:
        for title in titles[:-1]:
            posting = by_title[title]
            assert (posting.status, posting.notes) == (JobStatus.skipped, OWNER.text)
            events = list(db.scalars(sa.select(JobEvent).where(JobEvent.job_id == posting.id)))
            assert any(
                event.kind.value == "status"
                and event.data == {"from": "found", "to": "skipped", "via": "never_apply"}
                for event in events
            )
            assert any(
                event.kind.value == "note"
                and event.data == {"from": "", "to": OWNER.text, "via": "never_apply"}
                for event in events
            )
            assert guardrails.swept_never_apply(db, alice, posting.id)


# The app has no rule of its own about a kind of role (owner, 2026-11-05): a security role is a
# posting like any other for a person whose rules do not exclude it, and the same postings are
# skipped, with the person's own rule as their note, for a person whose rules do.
SECURITY_ROLES = [
    ("Example Robotics", "Security Engineer"),
    ("Example Robotics", "Pentest Engineer"),
    ("Northwind Cyber Labs", "AI Engineer"),
]
OWNERS_RULE = "No cybersecurity roles: cyber, security, pentest, threat"


def sweep_security_roles(factory: sessionmaker, user: uuid.UUID, rules: list[str]) -> Any:
    set_profile(factory, user, search(never_apply=rules))
    found = [
        finding(title, company=company, url=f"https://jobs.example-robotics.test/jobs/{i}")
        for i, (company, title) in enumerate(SECURITY_ROLES)
    ]
    return run(factory, new_sweep(factory, user), *found)


@pytest.mark.parametrize("rules", [[], ["No sales roles: sales (test rule)"]])
def test_a_sweep_keeps_security_roles_for_a_person_whose_rules_do_not_exclude_them(
    factory: sessionmaker, alice: uuid.UUID, rules: list[str]
) -> None:
    row = sweep_security_roles(factory, alice, rules)
    assert (row.stats["new"], row.stats["skipped_by_rule"]) == (3, 0)
    jobs = jobs_of(factory, alice)
    assert {(job.company, job.title): (job.status, job.notes) for job in jobs} == {
        key: (JobStatus.found, "") for key in SECURITY_ROLES
    }
    with factory() as db:
        assert not any(guardrails.swept_never_apply(db, alice, job.id) for job in jobs)


def test_a_sweep_skips_the_same_roles_under_the_persons_own_rule(
    factory: sessionmaker, alice: uuid.UUID, bob: uuid.UUID
) -> None:
    row = sweep_security_roles(factory, alice, [OWNERS_RULE])
    assert (row.stats["new"], row.stats["skipped_by_rule"]) == (3, 3)
    jobs = jobs_of(factory, alice)
    assert {(job.company, job.title): (job.status, job.notes) for job in jobs} == {
        key: (JobStatus.skipped, OWNERS_RULE) for key in SECURITY_ROLES
    }
    with factory() as db:
        assert all(guardrails.swept_never_apply(db, alice, job.id) for job in jobs)
    # Another person's sweep reads that person's rules only: Alice's rule is not Bob's.
    row = sweep_security_roles(factory, bob, [])
    assert (row.stats["new"], row.stats["skipped_by_rule"]) == (3, 0)
    assert {job.status for job in jobs_of(factory, bob)} == {JobStatus.found}
