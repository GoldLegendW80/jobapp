"""One click Apply (issue #738; SPEC-apply, "Owner decision (taken on 2026-11-01)").

The owner's words: "when I click on apply, it open a browser, navigate to the job offer, fill in
the form and click on apply. I want a checkbox in settings to set if I want to review the form
filled. By default its no (full auto)". So the Apply button of the job page, with the person's
"Let me review each filled form before it is sent" unticked (`apply_settings.review_before_send`,
off by default), starts an application of mode `direct`: the fill is Review first's (the same
adapters, answer resolver, form agent, documents wait, sign in hand off and `needs_person`
stops), and as soon as the fill is `ready` the gate decides and the gate's code clicks Submit,
with no Approve step. The person's click on Apply is the approval (CLAUDE.md rule 4): the
application's `created_at` is the click, its `apply_click` event records it, and its
`apply_click` authorisation row is written at the decision, bound to the hash of the filled
answers and the document versions, used once, valid at most `CLICK_VALID` after the click. No
model ever clicks Submit: the one click path stays the private one of `apply/gate.py`.

The order, every step through the gate, as Full auto's (`full_auto.py`) without its own rules:

1. `decide`, which the decision job (`submit.decision`) runs for a `ready` direct item
   (`submit.due` lists them): under the gate's locks, the click never decided before (one click
   gives at most one Submit), not older than `CLICK_VALID`, then the `apply_click` row written
   and `gate.decide` with it: the gate's checks (the kill switches, never apply, LinkedIn's
   consent and checkpoint pause, duplicates, the person's pause, company caps and the safety
   ceiling, the documents still the ones filled), then `checks` (this module's). A pass spends
   the row and moves the item to `approved` at once: there is no hold.
2. The submit lease, as for an approved Review first item: the pace per host (a wait keeps the
   authorisation), the read back of every value, the kill switches and `recheck` again just
   before the click.

Dropped for an explicit Apply click only (owner, 2026-11-01): the hold, the rehearsals, the fit
threshold "Apply automatically from" and Full auto's one application per company in 30 days.

Every refusal that `decide` keeps at `ready`, and every stop of the submit lease, sends the item
to the review queue (`full_auto.hand_over`: `mode` `review`, with the reason the job page shows),
so only the person's Approve can click it then. A stop the person clears in the live view (a
sign in, a captcha, a required answer the app does not know) keeps it `direct`: the fill goes on
and it is sent by itself once the person is done, with the same click's authorisation. The mode is
fixed at the click: ticking the box afterwards does not change an application under way.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import fallback, full_auto, model_check
from app.apply.models import (
    AnswerSource,
    Application,
    ApplicationAnswer,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.jobs.models import Job

CLICK_VALID = timedelta(days=7)

SENTENCES = {
    "review_only_site": (
        "The app does not send this site's form by itself yet, so you check it and send it."
    ),
    "email_review": (
        "This posting asks for an application by e-mail, and the app never sends mail by itself,"
        " so it waits for you."
    ),
}


def undecided(db: Session, application_id: uuid.UUID) -> bool:
    """A `ready` direct item: its decision job (`submit.decision`) decides it."""
    found = db.execute(
        sa.select(Application.state, Application.mode).where(Application.id == application_id)
    ).one_or_none()
    return found is not None and found[0] is State.ready and found[1] is Mode.direct


def _decided(db: Session, application: Application) -> bool:
    """Whether this click was already decided once: it authorises one decision, ever."""
    found = db.scalar(
        sa.select(Authorisation.id)
        .where(
            Authorisation.application_id == application.id,
            Authorisation.user_id == application.user_id,
            Authorisation.kind == AuthorisationKind.apply_click,
        )
        .limit(1)
    )
    return found is not None


def before_gate(db: Session, application: Application, now: datetime) -> str | None:
    """The refusals `decide` makes before the gate's own: the click decided once already, or
    older than `CLICK_VALID`. The check by model reads them too, to spare its call."""
    if _decided(db, application):
        return "authorisation_used"
    if now >= application.created_at + CLICK_VALID:
        return "authorisation_expired"
    return None


def checks(
    db: Session, application: Application, posting: Job, now: datetime | None = None
) -> str | None:
    """One click Apply's own checks, the code of the first that fails, or None: an application by
    e-mail waits for the person (CLAUDE.md rule 3), a submit button the agent designated without
    the strategy's binding goes to the person, and the free text the agent wrote is sent only on
    a stored `passed` of the check by model for the current answers (A13c)."""
    if application.ats == "email":
        return "email_review"
    answers = list(
        db.scalars(
            sa.select(ApplicationAnswer).where(
                ApplicationAnswer.application_id == application.id,
                ApplicationAnswer.user_id == application.user_id,
            )
        )
    )
    rows = [(row.kind, row.source) for row in answers]
    designated = any(
        source is AnswerSource.agent and kind == full_auto.DESIGNATED for kind, source in rows
    )
    if designated and fallback.binding(answers, application.form_url, application.ats) is None:
        return "agent_designated"
    return model_check.code(db, application, list(rows))


def recheck(db: Session, application: Application, posting: Job, now: datetime) -> str | None:
    """Before the click, beside the gate's own checks again: the documents still the ones filled,
    and `checks` again."""
    from app.apply import gate  # gate imports this module

    return gate.documents_code(db, application) or checks(db, application, posting, now)


def decide(db: Session, application_id: uuid.UUID, now: datetime, switches: Any) -> str:
    """Step 1 of the module docstring. Returns what was written (a code, no value); the caller
    commits."""
    from app.apply import gate  # gate imports this module

    user_id = db.scalar(sa.select(Application.user_id).where(Application.id == application_id))
    if user_id is None:
        return "gone"
    try:
        application, _ = gate.lock(db, user_id, application_id)
    except gate.NotFoundError:
        return "gone"
    if application.state is not State.ready or application.mode is not Mode.direct:
        return "superseded"
    clicked_at = application.created_at
    if (code := before_gate(db, application, now)) is not None:
        full_auto.to_review(db, application, code, now)
        gate._event(db, application, "decision", now, outcome="refused", code=code)
        return code
    authorisation = Authorisation(
        id=uuid.uuid4(),
        user_id=user_id,
        application_id=application.id,
        kind=AuthorisationKind.apply_click,
        answers_hash=gate.current_hash(db, application),
        created_at=now,
        expires_at=clicked_at + CLICK_VALID,
    )
    db.add(authorisation)
    db.flush()
    gate._event(
        db,
        application,
        "authorisation",
        now,
        authorisation_id=str(authorisation.id),
        kind=authorisation.kind.value,
        clicked_at=clicked_at.isoformat(),
    )
    decision = gate.decide(db, user_id, authorisation.id, now, switches=switches)
    return decision.state.value if decision.passed else (decision.code or "refused")
