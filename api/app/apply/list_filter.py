"""The application filter of the Jobs list (jobs J29): how a posting's newest application reads.

The jobs module owns the list and must not import apply (apply depends on jobs), so apply
registers this condition with `jobs.service.register_application_condition` when the app
includes its routers (registry.include). The words follow the Jobs list row (ApplyRow):
- none: no application, or the newest ended without reaching the site's Submit (cancelled, a
  rehearsal, the posting closed);
- under_way: the app works on it (queued, filling, approved, held, submitting, and ready when
  the app sends it itself: Full auto and one click Apply);
- waiting: the person's turn (needs_person, ready for the person's approval, and submit_unknown,
  where the person says whether it was sent);
- sent: submitted;
- failed: failed, blocked by a guardrail, refused by the site.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from app.apply.models import Application, Mode, State
from app.jobs import service as jobs_service
from app.jobs.models import Job
from app.jobs.service import ApplicationChoice

UNDER_WAY = (State.queued, State.filling, State.approved, State.held, State.submitting)
WAITING = (State.needs_person, State.ready, State.submit_unknown)
FAILED = (State.failed, State.blocked, State.refused_by_site)
DECIDED_BY_APP = (Mode.full_auto, Mode.direct)


def _reading() -> sa.Case[str]:
    return sa.case(
        (
            sa.and_(Application.state == State.ready, Application.mode.in_(DECIDED_BY_APP)),
            ApplicationChoice.under_way.value,
        ),
        (Application.state.in_(UNDER_WAY), ApplicationChoice.under_way.value),
        (Application.state.in_(WAITING), ApplicationChoice.waiting.value),
        (Application.state == State.submitted, ApplicationChoice.sent.value),
        (Application.state.in_(FAILED), ApplicationChoice.failed.value),
        else_=ApplicationChoice.none.value,
    )


def condition(choices: Sequence[ApplicationChoice]) -> sa.ColumnElement[bool]:
    """The jobs whose newest application (latest created, then id) reads as one of `choices`."""
    newest = (
        sa.select(_reading())
        .where(Application.posting_id == Job.id, Application.user_id == Job.user_id)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(1)
        .correlate(Job)
        .scalar_subquery()
    )
    wanted = [choice.value for choice in choices]
    return sa.func.coalesce(newest, ApplicationChoice.none.value).in_(wanted)


def register() -> None:
    jobs_service.register_application_condition(condition)
