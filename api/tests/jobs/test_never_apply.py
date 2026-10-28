"""The sweep and apply must read the person's exclusion rules alike (#580), and the app's own
rule against cybersecurity (never_apply.CYBER_RULE) holds without any rule of the person's."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.apply import guardrails
from app.jobs import never_apply, sweep
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


def test_the_apps_cyber_rule_skips_without_a_rule_of_the_persons(
    factory: sessionmaker, alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(never_apply=[]))
    row = run(
        factory,
        new_sweep(factory, alice),
        finding("Pentest Engineer", company="Example Robotics"),
        finding(
            "AI Engineer",
            company="Northwind Cyber Labs",
            url="https://jobs.example-robotics.test/northwind/ai-engineer",
        ),
        finding("AI Engineer", company="Example Robotics"),
    )
    assert (row.stats["new"], row.stats["skipped_by_rule"]) == (3, 2)
    by_key = {(job.company, job.title): job for job in jobs_of(factory, alice)}
    for key in (("Example Robotics", "Pentest Engineer"), ("Northwind Cyber Labs", "AI Engineer")):
        assert by_key[key].status is JobStatus.skipped
        assert by_key[key].notes == never_apply.CYBER_RULE.text
    assert by_key[("Example Robotics", "AI Engineer")].status is JobStatus.found
