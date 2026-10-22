"""The sweep and apply must read the person's exclusion rules alike (#580)."""

import uuid

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
from tests.jobs import fake_ats as fake_server
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import (
    board,
    fake_ats,
    jobs_of,
    new_sweep,
    run,
    search,
    set_profile,
)

__all__ = ["fake_ats"]


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


@pytest.mark.parametrize("ats", ["ashby", "greenhouse", "lever"])
def test_hidden_exclusions_are_stored_skipped_with_the_rule_and_its_events(
    factory: sessionmaker,
    alice: uuid.UUID,
    fake_ats: FakeAts,
    monkeypatch: pytest.MonkeyPatch,
    ats: str,
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
    original = fake_server.postings

    def postings(org: str) -> list[dict]:
        if org != "fake-security":
            return original(org)
        return [
            {
                "title": title,
                "location": "Paris",
                "url": f"https://jobs.example.org/fake-security/{i}",
                "published": fake_server._day(1),
                "text": "Build agents with Python.",
            }
            for i, title in enumerate(titles)
        ]

    monkeypatch.setattr(fake_server, "postings", postings)
    set_profile(
        factory,
        alice,
        search(board("fake-security", ats, "Example Robotics"), never_apply=[OWNER.text]),
    )
    row = run(factory, new_sweep(factory, alice), fake_ats)
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
