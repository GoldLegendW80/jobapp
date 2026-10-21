"""The first login wizard's state and its end (opportunities O5, SPEC-opportunities "The first
login wizard").

- `state`: what the web needs to decide, for the signed in person only: the two dates of
  `preferences.onboarding`, which steps the profile already answers, whether the app opens
  `/welcome` by itself (`open_wizard`) and whether the home page shows "Finish setting up"
  (`show_card`).
- `finish` and `dismiss`: the only callers of `profile.service.record_onboarding`. A date once
  recorded is kept: finishing twice, or putting off a finished wizard, changes nothing.
- `pass_step`: the only caller of `profile.service.record_onboarding_step`: a step passed (Next
  or "Skip this step") is kept in the profile, so another device resumes at the same step; once
  the wizard is finished it records nothing.
- `start_first_sweep`: the sweep "Start" begins, through the jobs module's own start
  (`sweep.begin`, shared with POST /api/jobs/sweeps).

The rules, each tested in tests/opportunities/test_o5_onboarding.py:
- the wizard opens by itself only for a member (never an admin: the owner's account) whose
  profile has no section saved but `preferences`, and whose wizard has neither date;
- the card shows while the wizard is not finished and an essential step is left (`STEPS_LEFT`),
  so an account whose profile is already filled never sees it.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.identity.models import Role, User
from app.jobs import sweep
from app.profile import service as profile_service
from app.profile.models import Profile
from app.profile.schemas import WIZARD_STEPS

# The step ids of the web wizard (web/src/features/onboarding/steps.ts), in order.
STEPS = WIZARD_STEPS
# The steps whose answer the card waits for: without them a sweep has nothing to look for.
STEPS_LEFT = ("profile", "looking", "where", "work")


@dataclass(frozen=True)
class State:
    completed_at: str | None
    dismissed_at: str | None
    profile_empty: bool
    done: list[str]
    passed: list[str]
    steps_left: list[str]
    open_wizard: bool
    show_card: bool


def _terms(section: dict[str, Any], name: str) -> list[Any]:
    value = section.get(name)
    terms = value.get("terms") if isinstance(value, dict) else None
    return terms if isinstance(terms, list) else []


def done_steps(sections: dict[str, dict[str, Any] | None]) -> list[str]:
    """The steps the stored profile already answers. `apply` is never among them (its answer is
    apply's setting, which always has a value) and `summary` is the end itself."""
    search = sections.get("search") or {}
    compensation = search.get("compensation")
    found = {
        "profile": bool(sections.get("identity") or sections.get("experience")),
        "looking": bool(_terms(search, "title_include")),
        "where": search.get("remote_stance") is not None,
        "work": bool(search.get("contract_types")),
        "pay": isinstance(compensation, dict)
        and bool(compensation.get("salary") or compensation.get("daily_rate")),
    }
    return [step for step in STEPS if found.get(step)]


def state(db: Session, user: User, now: datetime) -> State:
    profile = profile_service.get_profile(db, user.id, now)
    sections = profile_service.sections_view(profile)
    onboarding = (sections.get("preferences") or {}).get("onboarding") or {}
    completed_at = onboarding.get("completed_at")
    dismissed_at = onboarding.get("dismissed_at")
    passed = onboarding.get("steps_passed")
    empty = all(value is None for name, value in sections.items() if name != "preferences")
    done = done_steps(sections)
    left = [step for step in STEPS_LEFT if step not in done]
    return State(
        completed_at=completed_at,
        dismissed_at=dismissed_at,
        profile_empty=empty,
        done=done,
        passed=[step for step in STEPS if isinstance(passed, list) and step in passed],
        steps_left=left,
        open_wizard=(
            user.role is Role.member and empty and completed_at is None and dismissed_at is None
        ),
        show_card=completed_at is None and bool(left),
    )


def _record_once(db: Session, user: User, field: str, now: datetime) -> None:
    """Under the profile row lock, so a finish and a dismissal at once are read in turn."""
    profile_service.get_profile(db, user.id, now)  # the row exists from here on
    db.execute(
        sa.select(Profile)
        .where(Profile.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = state(db, user, now)
    if current.completed_at is not None:
        return
    if field == "dismissed_at" and current.dismissed_at is not None:
        return
    profile_service.record_onboarding(db, user.id, field, now)


def finish(db: Session, user: User, now: datetime) -> State:
    """Record `completed_at` (kept when already there); the caller commits."""
    _record_once(db, user, "completed_at", now)
    return state(db, user, now)


def dismiss(db: Session, user: User, now: datetime) -> State:
    """Record `dismissed_at`, unless the wizard is finished or already put off; the caller
    commits."""
    _record_once(db, user, "dismissed_at", now)
    return state(db, user, now)


def pass_step(db: Session, user: User, step: str, now: datetime) -> State:
    """Keep `step` among the steps passed (once); nothing once the wizard is finished. The
    caller commits."""
    if state(db, user, now).completed_at is None:
        profile_service.record_onboarding_step(db, user.id, step, now)
    return state(db, user, now)


async def start_first_sweep(
    sessions: sessionmaker[Session], queue: queues.Queue, user_id: uuid.UUID, now: datetime
) -> sweep.Begun[uuid.UUID]:
    """The first sweep, through the jobs module's own start (`sweep.begin`, the start of
    POST /api/jobs/sweeps): started and queued, the one already running, or why none started."""
    return await sweep.begin(sessions, queue, user_id, now, lambda row: row.id)
