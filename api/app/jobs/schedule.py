"""When a user's automatic sweeps fire: the slots of a `search.sweep_schedule` (pure functions).

A schedule is weekdays, local times ("HH:MM") and an IANA time zone. A slot is one (local date,
time) pair on one of those weekdays; its key, "<YYYY-MM-DD>T<HH:MM> <zone>", is the slot's
identity everywhere (sweeps.stats.schedule_slot, the queue's job id), so a slot fires at most
once whatever the number of ticks, workers or retries that see it.

Instants, daylight saving time included:
- a local time that exists once is that instant;
- a local time skipped by the spring transition (02:30 on the last Sunday of March in Paris)
  fires once, at the instant the clock jumps over it (03:00 local, 01:00 UTC): Python reads the
  missing wall time with the offset in force before the gap, which lands just past the jump;
- a local time repeated by the autumn transition (02:30 on the last Sunday of October) fires
  once, at its first occurrence (fold 0); the second occurrence is the same slot key.

The worker's cron ticks every TICK and fires every slot whose instant falls in the WINDOW before
the tick (a few ticks wide), so a tick that runs late or is missed still fires the slot once.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.profile.schemas import WEEKDAYS, SweepSchedule

TICK = timedelta(minutes=5)
WINDOW = timedelta(minutes=15)
LOOKAHEAD = timedelta(days=8)


@dataclass(frozen=True)
class Slot:
    key: str
    at: datetime  # UTC


def slot_instant(day: date, clock: str, zone: ZoneInfo) -> datetime:
    """The UTC instant of `clock` (HH:MM) on local `day` in `zone` (module docstring for DST)."""
    hour, minute = (int(part) for part in clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)  # fold 0
    instant = local.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        # A wall time in the spring gap: fire when the clock jumps over it.
        instant = _gap_end(local, zone)
    return instant


def _gap_end(local: datetime, zone: ZoneInfo) -> datetime:
    """The first instant after the spring gap that swallowed the wall time `local`: from the
    instant `local` names with the later offset (just before the gap), minute by minute until
    the local clock reads `local` or later."""
    wall = local.replace(tzinfo=None)
    candidate = local.replace(fold=1).astimezone(UTC).replace(second=0, microsecond=0)
    while candidate.astimezone(zone).replace(tzinfo=None) < wall:
        candidate += timedelta(minutes=1)
    return candidate


def slots_between(schedule: SweepSchedule, start: datetime, end: datetime) -> list[Slot]:
    """Every slot of the schedule with start < instant <= end, in time order (enabled or not)."""
    zone = ZoneInfo(schedule.timezone)
    first = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    days = set(schedule.days)
    found = []
    day = first
    while day <= last:
        if WEEKDAYS[day.weekday()] in days:
            for clock in schedule.times:
                at = slot_instant(day, clock, zone)
                if start < at <= end:
                    found.append(Slot(key=f"{day.isoformat()}T{clock} {schedule.timezone}", at=at))
        day += timedelta(days=1)
    return sorted(found, key=lambda slot: (slot.at, slot.key))


def due(schedule: SweepSchedule, now: datetime, window: timedelta = WINDOW) -> list[Slot]:
    """The slots a tick at `now` fires (those in the window before it), when enabled."""
    if not schedule.enabled:
        return []
    return slots_between(schedule, now - window, now)


def next_run(schedule: SweepSchedule, now: datetime) -> datetime | None:
    """The next instant the schedule fires after `now`, or None when it is off."""
    if not schedule.enabled:
        return None
    upcoming = slots_between(schedule, now, now + LOOKAHEAD)
    return upcoming[0].at if upcoming else None
