"""The states of an application and the moves between them (SPEC-apply, "States").

`queued` → `filling` → `needs_person` or `ready` → `approved` (Review first) or `held` (Full
auto's hold) → `submitting` → `submitted`, `submit_unknown`, `refused_by_site` or `failed`.
Besides: `rehearsed` (a dry run stopped before the click), `cancelled`, `blocked` (a guardrail
refused it, with its code), `posting_closed`.

`TRANSITIONS` is the whole table: a move not in it raises `TransitionError`, and `move` is the
one function that changes `applications.state`. The ends (`models.ENDED`: failed, rehearsed,
cancelled, blocked, posting_closed) have no way out, and neither has `submitted`. A new attempt
after an end is a new application. Two rules of the spec are in the table itself:

- `submit_unknown` never goes back to `submitting`: the app never submits it again by itself.
  The person says "It was submitted" (to `submitted`) or "Try again", which is a new approval
  of the refilled form (to `ready`).
- `submitting` is reached only from `approved` or `held`, the two states that follow a decision.
"""

from datetime import datetime

from app.apply.models import ENDED, Application, State

S = State

# Before the click, a fill may stop for good at any point: the person cancels, a guardrail
# refuses, the posting closed, the retries ran out.
_STOPS = frozenset({S.cancelled, S.blocked, S.posting_closed, S.failed})

TRANSITIONS: dict[State, frozenset[State]] = {
    # filling: a lease took it. queued again: a retry of A10 before the click.
    S.queued: frozenset({S.filling}) | _STOPS,
    S.filling: frozenset({S.queued, S.needs_person, S.ready}) | _STOPS,
    S.needs_person: frozenset({S.queued, S.filling, S.ready}) | _STOPS,
    # filling: refilled (an edit, or the submit lease of an approval reopening the form).
    S.ready: frozenset({S.queued, S.filling, S.needs_person, S.approved, S.held, S.rehearsed})
    | _STOPS,
    # ready: the approval no longer matches (form_changed, a newer document), back to the queue.
    S.approved: frozenset({S.ready, S.submitting, S.rehearsed}) | _STOPS,
    # ready: Full auto's refusal sends the item to the review queue, never to the bin.
    S.held: frozenset({S.ready, S.submitting, S.rehearsed}) | _STOPS,
    # blocked: the kill switch, checked again just before the click. ready: form_changed on the
    # refill. The three outcomes of the click.
    S.submitting: frozenset({S.ready, S.submitted, S.submit_unknown, S.refused_by_site}) | _STOPS,
    S.refused_by_site: frozenset({S.ready, S.filling, S.cancelled}),
    S.submit_unknown: frozenset({S.submitted, S.ready}),
    S.submitted: frozenset(),
    **{end: frozenset() for end in ENDED},
}

# The states in which the app has clicked Submit: they hold the posting for good.
CLICKED = frozenset({S.submitted, S.submit_unknown})


class TransitionError(ValueError):
    """A move that the table does not allow. Its message names the two states only."""


def allowed(current: State, to: State) -> bool:
    return to in TRANSITIONS[current]


def move(application: Application, to: State, now: datetime) -> None:
    """Move `application` to `to`, or raise TransitionError. The caller holds its row lock and
    commits. `submitted_at` is set when the move records the click, and kept after it."""
    current = State(application.state)
    if not allowed(current, to):
        raise TransitionError(f"an application cannot move from {current.value} to {to.value}")
    application.state = to
    application.updated_at = now
    if to in CLICKED and application.submitted_at is None:
        application.submitted_at = now
