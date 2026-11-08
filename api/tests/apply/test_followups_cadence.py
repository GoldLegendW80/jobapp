"""The follow up cadence and the text rules of a draft, as pure functions (followups.py)."""

from datetime import date, timedelta

import pytest

from app.apply import followups
from app.apply.followups import Track, cadence

APPLIED = date(2026, 11, 2)


def days(n: int) -> date:
    return APPLIED + timedelta(days=n)


def test_a_silent_application_waits_seven_days_for_its_first_follow_up() -> None:
    assert cadence(Track("applied", APPLIED), days(6)) == followups.Cadence(
        "follow_up", "waiting", days(7), 0
    )
    assert cadence(Track("applied", APPLIED), days(7)).urgency == "due"
    assert cadence(Track("applied", APPLIED), days(30)).urgency == "due"


def test_the_second_is_due_seven_days_after_the_first_and_never_a_third() -> None:
    first = Track("applied", APPLIED, sent=(days(9),))
    assert cadence(first, days(15)) == followups.Cadence("follow_up", "waiting", days(16), 1)
    assert cadence(first, days(16)).urgency == "due"
    both = Track("applied", APPLIED, sent=(days(9), days(17)))
    found = cadence(both, days(60))
    assert (found.urgency, found.next_on, found.count) == ("cold", None, 2)
    assert not followups.can_draft(found)


def test_a_follow_up_before_the_application_day_does_not_count() -> None:
    track = Track("applied", APPLIED, sent=(APPLIED - timedelta(days=3),))
    assert cadence(track, days(7)) == followups.Cadence("follow_up", "due", days(7), 0)


def test_the_persons_own_mail_after_the_application_day_counts_once_a_day() -> None:
    same_day = Track("applied", APPLIED, mail_out=(APPLIED,))
    assert cadence(same_day, days(7)).count == 0  # the application mail itself
    track = Track("applied", APPLIED, sent=(days(8),), mail_out=(days(8), days(15)))
    assert cadence(track, days(16)).urgency == "cold"


def test_an_answer_of_the_company_ends_the_cadence_until_the_person_writes_again() -> None:
    answered = Track("applied", APPLIED, mail_in=(days(3),))
    assert cadence(answered, days(10)) == followups.Cadence("follow_up", "answered", None, 0)
    # The acknowledgement on the day of the application is no answer.
    assert cadence(Track("applied", APPLIED, mail_in=(APPLIED,)), days(7)).urgency == "due"
    # An answer older than the person's last follow up: the cadence goes on.
    older = Track("applied", APPLIED, sent=(days(9),), mail_in=(days(4),))
    assert cadence(older, days(16)).urgency == "due"


def test_an_interview_asks_one_thank_you_note_the_next_day() -> None:
    track = Track("interview", APPLIED, interview_on=days(10))
    assert cadence(track, days(10)) == followups.Cadence("thank_you", "waiting", days(11), 0)
    assert cadence(track, days(11)).urgency == "due"
    thanked = Track("interview", APPLIED, interview_on=days(10), sent=(days(4), days(11)))
    assert cadence(thanked, days(40)) == followups.Cadence("thank_you", "done", None, 1)
    # No date of the move: the application day stands in.
    assert cadence(Track("interview", APPLIED), APPLIED).next_on == days(1)


def test_the_list_order_puts_the_longest_due_first_then_the_soonest_waiting() -> None:
    entries = [
        (cadence(Track("applied", days(-1)), days(7)), days(-1)),  # due since day 6
        (cadence(Track("applied", days(-20)), days(7)), days(-20)),  # due since day -13
        (cadence(Track("applied", days(-9)), days(7)), days(-9)),  # due since day -2
        (cadence(Track("applied", days(-30), sent=(days(-20), days(-10))), days(7)), days(-30)),
    ]
    ordered = sorted(entries, key=lambda pair: followups.sort_key(*pair))
    assert [pair[1] for pair in ordered] == [days(-20), days(-9), days(-1), days(-30)]


@pytest.mark.parametrize(
    ("subject", "body", "code"),
    [
        ("", "Hello", "subject"),
        ("Two\nlines", "Hello", "subject"),
        ("x" * 201, "Hello", "subject"),
        ("Data Engineer", "  ", "empty"),
        ("Data Engineer", "word " * 151, "too_long"),
        ("Data Engineer", "word " * 150, None),
    ],
)
def test_the_shape_of_a_draft(subject: str, body: str, code: str | None) -> None:
    assert followups.check_text(subject, body) == code


def test_words_are_counted_as_a_reader_counts_them() -> None:
    assert followups.word_count("I'm a full-stack engineer, since 2019-2021.") == 6
    assert followups.word_count("") == 0


def test_dashes_used_as_separators_become_commas() -> None:
    fixed = followups.undash("I applied on 3 November \u2014 the Data Engineer role - at Example")
    assert "\u2014" not in fixed
    assert " - " not in fixed
    assert followups.undash("full-stack work in 2019-2021") == "full-stack work in 2019-2021"
