"""What the person should know before the click (app/jobs/notices.py): the posting's signals read
against the profile, its closing date, a posting of another company with the same text and the
person's applications to the company. Pure sentences first, then through the detail route on a
real Postgres, where only the person's own rows and profile count, and the columns a store and
a rescore write. Every company, person and text is invented."""

import uuid
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import notices, service, signals
from app.jobs.models import ContractType, Job, JobStatus
from app.jobs.notices import Notice, Person, Row
from app.jobs.service import NewJob
from app.profile.models import Profile
from app.profile.schemas import PayRange, WorkAuthorization
from tests.identity.api_helpers import Api, Clock
from tests.jobs.conftest import NOW
from tests.jobs.sweep_helpers import SKILLS
from tests.jobs.test_api import Signed, alice, bob, seed

__all__ = ["alice", "bob"]

TODAY = date(2026, 11, 10)
BODY = (
    "You will design and ship the perception stack of our warehouse robots, from camera "
    "calibration to object tracking. You will own the data pipeline that labels millions of "
    "frames, train detection models in PyTorch, and deploy them on embedded GPUs with TensorRT. "
    "You will work with the controls team to close the loop between perception and motion "
    "planning, write design documents, review code, and mentor two junior engineers."
)


def posting(
    text: str = "",
    *,
    company: str = "Example Robotics",
    location: str = "Berlin, Germany",
    contract: ContractType = ContractType.CDI,
    **fields: Any,
) -> Job:
    values: dict[str, Any] = {
        "pay_min": None, "pay_max": None, "pay_currency": None, "pay_period": None,
        "signals": None, "text_fingerprint": None,
    }  # fmt: skip
    values.update(fields)
    return Job(
        id=uuid.uuid4(),
        company=company,
        title="Perception Engineer",
        location=location,
        contract_type=contract,
        posting_text=text,
        **values,
    )


def entry(country: str, needs: bool | None) -> WorkAuthorization:
    return WorkAuthorization(country=country, answer="As stated.", needs_sponsorship=needs)


def row(
    company: str = "Example Robotics",
    title: str = "Controls Engineer",
    status: JobStatus = JobStatus.applied,
    applied_on: date | None = date(2026, 10, 1),
    found_on: date = date(2026, 9, 30),
    text_fingerprint: int | None = None,
) -> Row:
    return Row(uuid.uuid4(), company, title, status, applied_on, found_on, text_fingerprint)


def texts(found: list[Notice]) -> list[tuple[str, str]]:
    return [(n.code, n.text) for n in found]


def no_dashes(found: list[Notice]) -> bool:
    return all(" - " not in n.text and "—" not in n.text and "–" not in n.text for n in found)


# --- the posting's signals against the profile ---------------------------------------------------


def test_no_sponsorship_where_the_person_needs_it_is_a_warning_with_its_quote() -> None:
    job = posting("We do not sponsor visas.")
    found = notices.notices_for(job, Person(work=(entry("Germany", True),)), [], TODAY)
    assert found == [
        Notice(
            "signal",
            "no_sponsorship",
            "This posting says it does not sponsor visas, and your profile says you need "
            "sponsorship in Germany.",
            "We do not sponsor visas.",
            "warning",
        )
    ]
    plain = notices.notices_for(job, Person(work=(entry("Germany", False),)), [], TODAY)
    assert texts(plain) == [("no_sponsorship", "This posting says it does not sponsor visas.")]
    assert plain[0].level == "info"


def test_an_eu_member_state_reads_the_person_s_eu_entry() -> None:
    job = posting("Nous ne pouvons pas sponsoriser de visa.", location="Paris")
    found = notices.notices_for(job, Person(work=(entry("EU", True),)), [], TODAY)
    assert found[0].text.endswith("you need sponsorship in France.")
    assert (
        notices.notices_for(job, Person(work=(entry("Switzerland", True),)), [], TODAY)[0].level
        == "info"
    )


def test_the_right_to_work_against_the_profile() -> None:
    job = posting("You must have the right to work in the UK.", location="London")
    needs = notices.notices_for(job, Person(work=(entry("United Kingdom", True),)), [], TODAY)
    assert texts(needs) == [
        (
            "work_permit",
            "This posting asks for the right to work in the United Kingdom, and your profile "
            "says you need sponsorship there.",
        )
    ]
    assert needs[0].level == "warning"
    elsewhere = notices.notices_for(job, Person(work=(entry("France", False),)), [], TODAY)
    assert texts(elsewhere) == [
        (
            "work_permit",
            "This posting asks for the right to work in the United Kingdom; your profile names "
            "no work authorisation there.",
        )
    ]
    unset = notices.notices_for(job, Person(), [], TODAY)
    assert texts(unset) == [
        ("work_permit", "This posting asks for the right to work in the United Kingdom.")
    ]


def test_silence_on_sponsorship_where_the_person_needs_it_is_said_as_silence() -> None:
    found = notices.notices_for(posting(BODY), Person(work=(entry("Germany", True),)), [], TODAY)
    assert texts(found) == [
        (
            "sponsorship_unstated",
            "This posting says nothing about visa sponsorship, and your profile says you need "
            "it in Germany.",
        )
    ]
    assert (found[0].quote, found[0].level) == (None, "info")
    assert notices.notices_for(posting(BODY), Person(), [], TODAY) == []
    offered = posting("Visa sponsorship is available.")
    assert texts(notices.notices_for(offered, Person(work=(entry("Germany", True),)), [], TODAY))[
        0
    ] == ("sponsorship", "This posting says it offers visa sponsorship.")


def test_a_contract_the_contract_type_already_says_is_not_repeated() -> None:
    text = "Independent contractor role.\nSix month contract."
    assert [n.code for n in notices.notices_for(posting(text), Person(), [], TODAY)] == [
        "contractor",
        "fixed_term",
    ]
    freelance = posting(text, contract=ContractType.Freelance)
    assert notices.notices_for(freelance, Person(), [], TODAY) == []


def test_unpaid_and_a_fee_are_warnings_first() -> None:
    text = "A degree in physics is required.\nThis is an unpaid internship.\n" + (
        "Candidates must pay a training fee of 300 EUR."
    )
    found = notices.notices_for(posting(text), Person(), [], TODAY)
    assert [(n.code, n.level) for n in found] == [
        ("unpaid", "warning"),
        ("candidate_fee", "warning"),
        ("degree", "info"),
    ]
    assert found[1].text == "This posting asks the candidate to pay a fee."
    assert no_dashes(found)


def test_the_stated_pay_under_the_aim_in_the_same_currency_and_period_only() -> None:
    pay = {"pay_min": 40_000, "pay_max": 45_000, "pay_currency": "EUR", "pay_period": "year"}
    person = Person(salary=PayRange(min=55_000, max=65_000, currency="EUR"))
    assert texts(notices.notices_for(posting(**pay), person, [], TODAY)) == [
        (
            "pay_below",
            "The stated pay tops out at 45,000 EUR a year, under the 55,000 EUR a year you aim "
            "for.",
        )
    ]
    assert notices.notices_for(posting(**{**pay, "pay_currency": "GBP"}), person, [], TODAY) == []
    assert notices.notices_for(posting(**{**pay, "pay_period": "month"}), person, [], TODAY) == []
    assert notices.notices_for(posting(**{**pay, "pay_max": 55_000}), person, [], TODAY) == []
    day = {"pay_min": 400, "pay_max": 500, "pay_currency": "EUR", "pay_period": "day"}
    daily = Person(daily_rate=PayRange(min=600, max=700, currency="EUR"))
    assert [n.code for n in notices.notices_for(posting(**day), daily, [], TODAY)] == ["pay_below"]


# --- the closing date ----------------------------------------------------------------------------


def test_the_closing_date_in_words() -> None:
    def said(days: int) -> tuple[str, str]:
        found = notices.closing_notice(TODAY + timedelta(days=days), "Deadline.", TODAY)
        return found.text, found.level

    assert said(-7) == ("This posting gave 3 November 2026 as its closing date.", "warning")
    assert said(0) == ("This posting closes today, 10 November 2026.", "warning")
    assert said(1) == ("This posting closes tomorrow, 11 November 2026.", "warning")
    assert said(5) == ("This posting closes on 15 November 2026, in 5 days.", "warning")
    assert said(7) == ("This posting closes on 17 November 2026, in 7 days.", "warning")
    assert said(8) == ("This posting closes on 18 November 2026.", "info")
    assert notices.closes_soon(TODAY + timedelta(days=7), TODAY)
    assert not notices.closes_soon(TODAY + timedelta(days=8), TODAY)
    assert not notices.closes_soon(TODAY - timedelta(days=1), TODAY)
    assert not notices.closes_soon(None, TODAY)


def test_a_stored_closing_date_is_a_notice_with_its_sentence() -> None:
    text = "Closing date: 14 November 2026."
    job = posting(**signals.stored(text))
    (found,) = notices.notices_for(job, Person(), [], TODAY)
    assert found == Notice(
        "closing", "closes_on", "This posting closes on 14 November 2026, in 4 days.", text,
        "warning",
    )  # fmt: skip


# --- the company and the same text ---------------------------------------------------------------


def test_one_application_to_the_company_told_whole() -> None:
    job = posting(company="Example Robotics SAS")
    waiting = row()
    (found,) = notices.notices_for(job, Person(), [waiting], TODAY)
    assert found == Notice(
        "history",
        "applied_before",
        "You applied to Example Robotics SAS on 1 October 2026 for Controls Engineer, with no "
        "reply recorded in 40 days.",
        job_id=waiting.id,
    )
    for status, tail in (
        (JobStatus.interview, ", and an interview followed."),
        (JobStatus.rejected, ", and it ended in a rejection."),
        (JobStatus.closed, ", and that posting is now closed."),
    ):
        (one,) = notices.notices_for(job, Person(), [row(status=status)], TODAY)
        assert one.text.endswith(f"for Controls Engineer{tail}")
    undated = row(status=JobStatus.rejected, applied_on=None)
    assert notices.notices_for(job, Person(), [undated], TODAY)[0].text == (
        "You applied to Example Robotics SAS (date not recorded) for Controls Engineer, and it "
        "ended in a rejection."
    )


def test_several_applications_counted_by_outcome_and_others_left_out() -> None:
    job = posting()
    rows = [
        row(title="Controls Engineer", status=JobStatus.rejected, applied_on=date(2026, 6, 1)),
        row(title="Fleet Engineer", status=JobStatus.interview, applied_on=date(2026, 8, 1)),
        row(title="Vision Engineer", applied_on=date(2026, 10, 20)),
        row(title="Data Engineer", status=JobStatus.found, applied_on=None),  # never applied
        row(company="Example Labs", title="ML Engineer"),  # another company
    ]
    (found,) = notices.notices_for(job, Person(), rows, TODAY)
    assert found.text == (
        "You applied to Example Robotics 3 times, most recently on 20 October 2026 for Vision "
        "Engineer: 1 interview, 1 rejection, 1 with no reply recorded."
    )
    assert found.job_id == rows[2].id
    same_row = Row(job.id, "Example Robotics", "Perception Engineer", JobStatus.applied, TODAY,
                   TODAY, None)  # fmt: skip
    assert notices.notices_for(job, Person(), [same_row], TODAY) == []


def test_the_same_text_under_another_company() -> None:
    direct = f"Example Robotics is hiring.\n{BODY}"
    agency = f"Our client, a robotics scaleup in Lyon, is hiring.\n{BODY}"
    printed = signals.fingerprint(direct)
    job = posting(agency, company="Example Talent")
    applied = row(title="Perception Engineer", text_fingerprint=printed)
    (found,) = notices.notices_for(job, Person(), [applied], TODAY)
    assert found == Notice(
        "cross_listing",
        "same_text",
        "Its text is nearly the same as Perception Engineer at Example Robotics, which you "
        "applied to on 1 October 2026.",
        level="warning",
        job_id=applied.id,
    )
    seen = row(status=JobStatus.found, applied_on=None, text_fingerprint=printed)
    twice = notices.notices_for(job, Person(), [seen, applied], TODAY)
    assert twice[0].job_id == applied.id
    assert twice[0].text.endswith("It matches 1 more of your postings too.")
    found_only = notices.notices_for(job, Person(), [seen], TODAY)[0]
    assert (found_only.text, found_only.level) == (
        "Its text is nearly the same as Controls Engineer at Example Robotics, found on 30 "
        "September 2026.",
        "info",
    )
    # The same company is the duplicate guard's, and an unrelated text is nothing.
    assert notices.cross_listing_notice(posting(agency), printed, [applied]) is None
    other = row(text_fingerprint=signals.fingerprint("Payments platform lead. " * 30 + BODY[:40]))
    assert notices.cross_listing_notice(job, signals.fingerprint(agency), [other]) is None


# --- through the route, the person's own rows only ------------------------------------------------


def give_profile(factory: sessionmaker[Session], user: uuid.UUID, sections: dict[str, Any]) -> None:
    with factory() as db, db.begin():
        db.add(Profile(user_id=user, sections=sections, revision=1))


def test_the_detail_says_what_to_know_from_the_person_s_own_rows(
    api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session], clock: Clock
) -> None:
    closes = notices.today_of(clock()) + timedelta(days=3)
    text = f"We do not sponsor visas.\nClosing date: {closes.isoformat()}.\n{BODY}"
    give_profile(
        factory,
        alice.id,
        {"answers": {"work_authorization": [
            {"country": "Germany", "answer": "Needs a permit.", "needs_sponsorship": True}
        ]}},
    )  # fmt: skip
    job, _ = seed(
        factory,
        alice.id,
        [
            {"posting_text": text, "location": "Berlin", "company": "Example Robotics GmbH"},
            {"company": "Example Robotics", "status": JobStatus.rejected,
             "applied_on": date(2026, 9, 1), "title": "Controls Engineer"},
        ],
    )  # fmt: skip
    # Bob applied to the same company with the same text: none of it is Alice's.
    seed(
        factory,
        bob.id,
        [
            {"company": "Example Robotics", "status": JobStatus.applied, "applied_on": NOW.date()},
            {"company": "Example Talent", "status": JobStatus.applied, "applied_on": NOW.date(),
             "text_fingerprint": signals.fingerprint(text)},
        ],
    )  # fmt: skip
    body = api.get(f"/api/jobs/{job.id}", token=alice.token).json()
    assert [(n["kind"], n["code"], n["level"]) for n in body["notices"]] == [
        ("signal", "no_sponsorship", "warning"),
        ("closing", "closes_on", "warning"),
        ("history", "applied_before", "info"),
    ]
    first, closing, history = body["notices"]
    assert first["text"].endswith("you need sponsorship in Germany.")
    assert first["quote"] == "We do not sponsor visas."
    assert closing["text"].endswith("in 3 days.")
    assert history["text"] == (
        "You applied to Example Robotics GmbH on 1 September 2026 for Controls Engineer, and it "
        "ended in a rejection."
    )
    assert history["job_id"] is not None
    assert first["job_id"] is None
    assert api.get(f"/api/jobs/{job.id}", token=bob.token).status_code == 404


def test_a_posting_added_by_hand_stores_what_its_text_says(db: Session, alice: Signed) -> None:
    text = f"We do not sponsor visas.\nDeadline: 2026-12-01.\n{BODY}"
    job = service.create_job(
        db, alice.id, NewJob("Example Robotics", "Perception", posting_text=text), NOW
    )
    db.commit()
    assert job.signals == [
        {"code": "no_sponsorship", "quote": "We do not sponsor visas.", "value": None},
        {"code": "closing", "quote": "Deadline: 2026-12-01.", "value": "2026-12-01"},
    ]
    assert job.closes_on == date(2026, 12, 1)
    assert job.text_fingerprint == signals.fingerprint(text)


def test_a_rescore_reads_the_rows_never_read_and_touches_only_those(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    text = f"This is an unpaid internship.\n{BODY}"
    unread, empty = seed(factory, alice.id, [{"posting_text": text}, {}])
    give_profile(factory, alice.id, {"skills": SKILLS})
    later = NOW + timedelta(hours=1)

    def rescore(at: Any) -> None:
        with factory() as db:
            outcome = service.RescoreOutcome()
            service.rescore_batch(
                db, alice.id, service.user_skills(db, alice.id), at, ids=None, after=None,
                limit=100, overwrite_manual=False, outcome=outcome,
            )  # fmt: skip
            db.commit()

    rescore(later)
    with factory() as db:
        rows = {
            j.id: j for j in db.execute(sa.select(Job).where(Job.user_id == alice.id)).scalars()
        }
    assert [s["code"] for s in rows[unread.id].signals] == ["unpaid"]
    assert rows[unread.id].text_fingerprint == signals.fingerprint(text)
    assert (rows[empty.id].signals, rows[empty.id].text_fingerprint) == ([], None)
    stamped = {i: j.updated_at for i, j in rows.items()}
    rescore(later + timedelta(hours=1))  # nothing new to read: nothing touched
    with factory() as db:
        again = {
            j.id: j.updated_at
            for j in db.execute(sa.select(Job).where(Job.user_id == alice.id)).scalars()
        }
    assert again == stamped
