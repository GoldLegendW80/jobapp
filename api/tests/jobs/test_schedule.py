"""app/jobs/schedule.py with a frozen clock: slots, the due window of a tick, the next run, and
daylight saving time (the skipped spring hour and the repeated autumn hour), simulated tick by
tick over whole weeks so every slot is proven to fire exactly once."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.jobs import schedule
from app.profile.schemas import WEEKDAYS, SweepSchedule

PARIS = ZoneInfo("Europe/Paris")
NEW_YORK = ZoneInfo("America/New_York")


def plan(**fields: Any) -> SweepSchedule:
    return SweepSchedule.model_validate({"enabled": True, **fields})


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


# --- instants -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "clock", "expected_utc", "local_wall"),
    [
        (date(2026, 10, 10), "06:17", utc(2026, 10, 10, 4, 17), "06:17"),  # summer, +02:00
        (date(2026, 12, 1), "06:17", utc(2026, 12, 1, 5, 17), "06:17"),  # winter, +01:00
        # Spring: 02:00 to 02:59 do not exist on 29 March 2026; they fire as the clock jumps.
        (date(2026, 3, 29), "02:00", utc(2026, 3, 29, 1, 0), "03:00"),
        (date(2026, 3, 29), "02:30", utc(2026, 3, 29, 1, 0), "03:00"),
        (date(2026, 3, 29), "01:59", utc(2026, 3, 29, 0, 59), "01:59"),
        (date(2026, 3, 29), "03:00", utc(2026, 3, 29, 1, 0), "03:00"),
        # Autumn: 02:00 to 02:59 happen twice on 25 October 2026; the first one counts.
        (date(2026, 10, 25), "02:30", utc(2026, 10, 25, 0, 30), "02:30"),
        (date(2026, 10, 25), "03:00", utc(2026, 10, 25, 2, 0), "03:00"),
        (date(2026, 10, 25), "00:00", utc(2026, 10, 24, 22, 0), "00:00"),
    ],
)
def test_slot_instants_in_paris(
    day: date, clock: str, expected_utc: datetime, local_wall: str
) -> None:
    at = schedule.slot_instant(day, clock, PARIS)
    assert at == expected_utc
    assert at.astimezone(PARIS).strftime("%H:%M") == local_wall


def test_slot_instants_in_new_york_dst() -> None:
    # 8 March 2026: 02:30 does not exist in New York; 1 November 2026: 01:30 happens twice.
    assert schedule.slot_instant(date(2026, 3, 8), "02:30", NEW_YORK) == utc(2026, 3, 8, 7, 0)
    assert schedule.slot_instant(date(2026, 11, 1), "01:30", NEW_YORK) == utc(2026, 11, 1, 5, 30)


# --- slots and the window -----------------------------------------------------------------------


def test_slots_follow_days_times_and_window_bounds() -> None:
    weekdays = plan(days=["monday", "wednesday"], times=["18:00", "06:17"])
    # Monday 5 to Sunday 11 October 2026.
    slots = schedule.slots_between(weekdays, utc(2026, 10, 5, 0, 0), utc(2026, 10, 11, 23, 59))
    assert [s.key for s in slots] == [
        "2026-10-05T06:17 Europe/Paris",
        "2026-10-05T18:00 Europe/Paris",
        "2026-10-07T06:17 Europe/Paris",
        "2026-10-07T18:00 Europe/Paris",
    ]
    at = slots[0].at
    # Start exclusive, end inclusive.
    assert schedule.slots_between(weekdays, at, at + timedelta(minutes=1)) == []
    assert [s.at for s in schedule.slots_between(weekdays, at - timedelta(seconds=1), at)] == [at]


def test_due_window_and_disabled_schedules() -> None:
    daily = plan()
    tick = utc(2026, 10, 10, 4, 20)  # 06:20 in Paris
    assert [s.key for s in schedule.due(daily, tick)] == ["2026-10-10T06:17 Europe/Paris"]
    assert schedule.due(daily, utc(2026, 10, 10, 4, 15)) == []  # before the slot
    assert schedule.due(daily, utc(2026, 10, 10, 4, 33)) == []  # past the 15 minute window
    assert schedule.due(SweepSchedule(), tick) == []  # off by default
    assert schedule.due(daily.model_copy(update={"enabled": False}), tick) == []


def test_next_run() -> None:
    weekly = plan(days=["sunday"], times=["09:00"], timezone="America/New_York")
    now = utc(2026, 10, 9, 12, 0)  # a Friday
    assert schedule.next_run(weekly, now) == utc(2026, 10, 11, 13, 0)
    assert schedule.next_run(weekly, utc(2026, 10, 11, 13, 0)) == utc(2026, 10, 18, 13, 0)
    assert schedule.next_run(SweepSchedule(), now) is None


# --- ticks over whole weeks ---------------------------------------------------------------------


def simulate(
    plan_: SweepSchedule, start: datetime, end: datetime, skip_ticks: set[datetime] = frozenset()
) -> dict[str, datetime]:
    """Run a tick every 5 minutes from start to end, as the worker does; a slot fires the first
    time a tick sees it (the recorded slot makes later ticks skip it). Returns key -> tick."""
    fired: dict[str, datetime] = {}
    tick = start
    while tick <= end:
        if tick not in skip_ticks:
            for slot in schedule.due(plan_, tick):
                fired.setdefault(slot.key, tick)
        tick += schedule.TICK
    return fired


@pytest.mark.parametrize(
    ("label", "start"),
    [
        ("spring forward week", utc(2026, 3, 26, 0, 0)),
        ("autumn back week", utc(2026, 10, 22, 0, 0)),
        ("ordinary week", utc(2026, 10, 5, 0, 0)),
    ],
)
def test_every_slot_fires_once_over_a_week(label: str, start: datetime) -> None:
    everything = plan(times=["00:00", "02:30", "06:17", "23:59"])
    end = start + timedelta(days=7)
    fired = simulate(everything, start, end)
    expected = {
        s.key for s in schedule.slots_between(everything, start - timedelta(minutes=15), end)
    }
    assert set(fired) == expected
    assert len(fired) >= 4 * 7
    for key, tick in fired.items():
        day, rest = key.split("T")
        clock = rest.split(" ")[0]
        at = schedule.slot_instant(date.fromisoformat(day), clock, PARIS)
        assert at <= tick < at + schedule.TICK, (label, key, at, tick)


def test_the_repeated_autumn_hour_fires_once_at_its_first_occurrence() -> None:
    once = plan(times=["02:30"])
    fired = simulate(once, utc(2026, 10, 24, 22, 0), utc(2026, 10, 25, 4, 0))
    assert fired == {"2026-10-25T02:30 Europe/Paris": utc(2026, 10, 25, 0, 30)}


def test_the_skipped_spring_hour_fires_once_when_the_clock_jumps() -> None:
    once = plan(times=["02:30"])
    fired = simulate(once, utc(2026, 3, 28, 22, 0), utc(2026, 3, 29, 4, 0))
    assert fired == {"2026-03-29T02:30 Europe/Paris": utc(2026, 3, 29, 1, 0)}


def test_a_missed_tick_still_fires_the_slot_once() -> None:
    daily = plan(times=["06:17"])
    tick = utc(2026, 10, 10, 4, 20)
    late = simulate(
        daily,
        utc(2026, 10, 10, 4, 0),
        utc(2026, 10, 10, 5, 0),
        skip_ticks={tick, tick + schedule.TICK},
    )
    assert late == {"2026-10-10T06:17 Europe/Paris": tick + 2 * schedule.TICK}


def test_weekday_names_are_the_schema_order() -> None:
    assert WEEKDAYS[date(2026, 10, 12).weekday()] == "monday"
    assert SweepSchedule().days == list(WEEKDAYS)
