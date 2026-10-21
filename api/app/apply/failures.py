"""Failures, retries and "needs you" of the apply run (SPEC-apply, "Failures and retries"; apply
A10a). Pure rules and the few writes `run.py` asks for; no browser call here.

- **Retries before the click.** A fill that ends on a passing fault (`RETRYABLE`: a navigation
  timeout or failure, a 5xx page, browserd unavailable, the lease gone under the fill, a stop or a
  crash of the worker) goes back to `queued` with `needs` = `retry` while retries are left: the
  waits are APPLY_RETRY_DELAYS_S (600 then 3,600 s), the first after attempt 1, the second after
  attempt 2; after the last it ends `failed` with its code. Any other code ends `failed` at once
  (an allow list: a code this module does not know is never retried). `run.due` asks for a retry
  only once its time came (`retry_waiting`, by the clock it is given), so the person's next
  application goes meanwhile. A retry is a fill: it never moves an application past `ready`, so
  never to a click, and the authorisation of a Like or an approval is spent only by the gate.
- **The person.** `needs_person` keeps its reason in `needs`: `captcha`, `required_left`,
  `email_code`, `payment_requested`, `signin` (until A10b, the review queue's "Needs you: sign
  in"), and the run's own (`form_changed`, `no_adapter`, ...). In Review first, for a step on the
  page (`WAIT_ON_PAGE`) the run keeps the lease and waits for the person at most
  APPLY_NEEDS_PERSON_WAIT_S, then the lease ends and the application keeps waiting. Full auto never
  waits: its item goes to the review queue (`full_auto.hand_over`).
- **A payment page** (`payment_page`): a field for a card number or a card's security code, or a
  card payment frame, ends the attempt as `needs_person: payment_requested`, nothing typed.

Declared open points: the retry's time is counted from `updated_at`, which nothing writes on a
`queued` application before it is due; the wait for the person runs on the lease's active time,
since browserd has no wait for a captcha (only the sign in hand off suspends its timers), so a
lease that reaches its maximum ends the wait sooner; a payment asked in words only, with no card
field, is not seen.
"""

import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.ats import base as ats_base
from app.apply.models import Application, Mode, State

RETRY = "retry"
HTTP_5XX = "http_5xx"
PAYMENT = "payment_requested"
RETRYABLE = frozenset(
    {
        "navigation_timeout",
        "navigation_failed",
        HTTP_5XX,
        "unavailable",
        "ended",
        "interrupted",
        "internal_error",
    }
)
# The steps the person takes on the page itself, for which a Review first run keeps the lease.
WAIT_ON_PAGE = frozenset({"captcha", "email_code"})
WAIT_POLL_S = 5.0


def _setting(name: str) -> Any:
    from app.config import ConfigError, Settings, get_settings

    try:
        return getattr(get_settings(), name)
    except ConfigError:
        return Settings.model_fields[name].default


def retry_delays() -> tuple[int, ...]:
    return tuple(int(part) for part in str(_setting("apply_retry_delays_s")).split(","))


def needs_person_wait_s() -> int:
    return int(_setting("apply_needs_person_wait_s"))


def retry_delay(attempt: int, code: str | None, delays: Sequence[int]) -> int | None:
    """The wait before the next attempt after attempt number `attempt` failed with `code`, or
    None when the application ends `failed`."""
    if code not in RETRYABLE or not 1 <= attempt <= len(delays):
        return None
    return delays[attempt - 1]


def after_failure(
    application: Application, code: str | None, now: datetime, delays: Sequence[int]
) -> tuple[State, dict[str, Any]]:
    """The state a failed fill moves to and its event's data. The caller moves it, under the
    application's row lock."""
    delay = retry_delay(application.attempt, code, delays)
    if delay is None:
        return State.failed, {}
    due = now + timedelta(seconds=delay)
    return State.queued, {"attempt": application.attempt, "delay_s": delay, "due": due.isoformat()}


def retry_waiting(now: datetime | None, delays: Sequence[int]) -> sa.ColumnElement[bool]:
    """True for a `queued` application whose retry's time has not come (`now` None: the
    database's clock)."""
    wait = sa.case(
        *((Application.attempt == i, delay) for i, delay in enumerate(delays, start=1)),
        else_=0,
    )
    since = sa.func.make_interval(0, 0, 0, 0, 0, 0, sa.cast(wait, sa.Float))
    clock: Any = sa.func.now() if now is None else sa.literal(now, sa.DateTime(timezone=True))
    # Never null: the tick negates it, and a null would hide every application without `needs`.
    retrying = sa.func.coalesce(Application.needs, "") == RETRY
    return sa.and_(retrying, Application.updated_at + since > clock)


def needs_count(db: Session, user_id: uuid.UUID) -> int:
    """How many of the person's applications wait in `needs_person` (the app shell's dot)."""
    return int(
        db.execute(
            sa.select(sa.func.count()).where(
                Application.user_id == user_id,
                Application.mode != Mode.manual,
                Application.state == State.needs_person,
            )
        ).scalar_one()
    )


# --- a payment page ----------------------------------------------------------------------------

_CARD = r"(?:(?:credit|debit|bank|payment) )?card"
_PAYMENT_FIELD = re.compile(
    rf"{_CARD} (?:number|no|#)|{_CARD} security code"
    r"|cvv2?|cvc2?|cvn|card verification (?:code|value|number)"
    r"|numero de (?:la )?carte(?: (?:bancaire|de credit|de paiement))?"
    r"|cryptogramme(?: visuel)?|code de securite de la carte"
)
_PAYMENT_FRAME = re.compile(r"secure (?:card )?(?:payment|card number) input frame")
_FIELD_ROLES = frozenset({"textbox", "spinbutton"})


def _plain(name: str) -> str:
    folded = ats_base.fold(ats_base.label_of(name)[0])
    kept = "".join(c if c.isalnum() or c in " #" else " " for c in folded)
    return " ".join(kept.split())


def payment_page(snapshot: str) -> bool:
    """The page asks for a card: a text field named, whole, as a card's number or security code
    (English or French), or a card payment frame. A label it does not name is no payment."""
    for node in ats_base.parse_snapshot(snapshot).walk():
        if node.role in _FIELD_ROLES and _PAYMENT_FIELD.fullmatch(_plain(node.name)):
            return True
        if node.role == "iframe" and _PAYMENT_FRAME.fullmatch(_plain(node.name)):
            return True
    return False
