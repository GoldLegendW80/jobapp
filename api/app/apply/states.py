"""The states of an application and the moves between them (SPEC-apply, "States").

`queued` → `filling` → `needs_person` or `ready` → `approved` (Review first) or `held` (Full
auto's hold) → `submitting` → `submitted`, `submit_unknown` or `refused_by_site`.
Besides: `rehearsed` (a dry run stopped before the click), `cancelled`, `blocked` (a guardrail
refused it, with its code), `posting_closed`.

`TRANSITIONS` is the whole table: a move not in it raises `TransitionError`, and `move` is the
one function that changes `applications.state`. The ends (`models.ENDED`: failed, rehearsed,
cancelled, blocked, posting_closed) have no way out, and neither has `submitted`, with one
exception: a Full auto rehearsal the person answers Yes to goes from `rehearsed` to `ready`
(A13a, `full_auto.confirm_rehearsal`; `move` refuses it in any other mode). A new attempt after
an end is a new application. Two rules of the spec are in the table itself:

- `submit_unknown` never goes back to `submitting`: the app never submits it again by itself.
  The person says "It was submitted" (to `submitted`) or "Try again", which is a new approval
  of the refilled form (to `ready`).
- `submitting` is reached only from `approved` or `held`, the two states that follow a decision.

Tightened by apply A6 (issue #296):
- `approved` and `held` are entered only by the gate's decision (`move(..., by_gate=True)`, which
  only `gate.decide` passes, after it spent an unused authorisation). Every way back to `ready`
  (`form_changed`, "Try again" after `submit_unknown` or `refused_by_site`) therefore needs a new
  approval before `submitting` again (test_try_again_needs_a_new_approval).
- `submitting` is committed just before the click, so it ends only in the three outcomes of a
  click: `submitted`, `submit_unknown`, `refused_by_site`. It never ends in a state that frees the
  unique indexes (cancelled, blocked, posting_closed, failed) as if nothing was sent; a crash
  after it is `submit_unknown` (`submit.py`).
- A rehearsal (`dry_run`) never reaches `submitting`: the gate records `rehearsed` from
  `approved` after the refill and the read back, before the click. `ready` has no move to
  `rehearsed`: a rehearsal always passes the gate first.
- Apply myself (A9, `apply/manual.py`): the person clicks the site's own Submit, so the app
  records `submitted` from `queued`, `filling` or `needs_person` without `submitting` and
  without the gate (`MANUAL_SENT_FROM`), for a `manual` application only
  (test_the_manual_move_is_for_manual_applications_only).
- Before the click the submit lease may stop: `approved` to `ready` (a difference on the read
  back, the approval spent), `needs_person` (a captcha on the refilled form), or an end.
"""

from datetime import datetime

from app.apply.models import ENDED, Application, Mode, State

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
    # Never `rehearsed` directly: a rehearsal ends only after the gate (A5 stops a dry run at
    # `ready`, the submit lease records `rehearsed` from `approved` or `held`).
    S.ready: frozenset({S.queued, S.filling, S.needs_person, S.approved, S.held}) | _STOPS,
    # ready: the approval no longer matches (form_changed on the refill), back to the queue.
    # needs_person: a captcha on the refilled form. cancelled: the kill switch, checked again
    # just before the click.
    S.approved: frozenset({S.ready, S.needs_person, S.submitting, S.rehearsed}) | _STOPS,
    # ready: Full auto's refusal sends the item to the review queue, never to the bin.
    S.held: frozenset({S.ready, S.needs_person, S.submitting, S.rehearsed}) | _STOPS,
    # The three outcomes of the click, and nothing else: the click may have gone through.
    S.submitting: frozenset({S.submitted, S.submit_unknown, S.refused_by_site}),
    S.refused_by_site: frozenset({S.ready, S.filling, S.cancelled}),
    S.submit_unknown: frozenset({S.submitted, S.ready}),
    S.submitted: frozenset(),
    **{end: frozenset() for end in ENDED},
    # The Yes to a Full auto rehearsal: to the review queue, `mode` review and `dry_run` false.
    S.rehearsed: frozenset({S.ready}),
}

# Apply myself: the person sent it from the live view, the one way to `submitted` past the gate.
MANUAL_SENT_FROM = frozenset({S.queued, S.filling, S.needs_person})

# The states in which the app has clicked Submit: they hold the posting for good.
CLICKED = frozenset({S.submitted, S.submit_unknown})
# The states only the gate's decision enters.
DECIDED = frozenset({S.approved, S.held})


class TransitionError(ValueError):
    """A move that the table does not allow. Its message names the two states only."""


def allowed(current: State, to: State) -> bool:
    return to in TRANSITIONS[current]


def move(application: Application, to: State, now: datetime, *, by_gate: bool = False) -> None:
    """Move `application` to `to`, or raise TransitionError. The caller holds its row lock and
    commits. `submitted_at` is set when the move records the click, and kept after it. Only the
    gate's decision passes `by_gate` (into `approved` or `held`); a rehearsal never moves to
    `submitting`."""
    current = State(application.state)
    sent_by_person = (
        application.mode is Mode.manual and to is S.submitted and current in MANUAL_SENT_FROM
    )
    if not sent_by_person and not allowed(current, to):
        raise TransitionError(f"an application cannot move from {current.value} to {to.value}")
    if to in DECIDED and not by_gate:
        raise TransitionError(
            f"an application cannot move from {current.value} to {to.value} without a decision"
        )
    if current is S.rehearsed and application.mode is not Mode.full_auto:
        raise TransitionError(
            f"an application cannot move from {current.value} to {to.value} outside Full auto"
        )
    if to is S.submitting and application.dry_run:
        raise TransitionError(
            f"an application cannot move from {current.value} to {to.value} in a rehearsal"
        )
    application.state = to
    application.updated_at = now
    if to in CLICKED and application.submitted_at is None:
        application.submitted_at = now
