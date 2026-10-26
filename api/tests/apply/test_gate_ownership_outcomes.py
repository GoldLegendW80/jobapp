"""Only a refusal proven to come before the click moves a submitting application back: browserd's
input refusal, or the gate's checks again between the arm and the click."""

import pytest

from app.apply import states
from app.apply.models import Application, Mode, State
from tests.apply.conftest import AT


def test_only_an_explicit_browser_refusal_can_restore_a_submitting_application():
    application = Application(mode=Mode.review, state=State.submitting)
    with pytest.raises(states.TransitionError):
        states.move(application, State.ready, AT)
    states.move(application, State.ready, AT, click_refused=True)
    assert application.state is State.ready
    assert application.submitted_at is None
    with pytest.raises(states.TransitionError):
        states.move(application, State.submitted, AT, click_refused=True)


@pytest.mark.parametrize("to", list(State))
def test_a_refusal_before_the_click_leaves_submitting_only_for_ready_or_recheck_ends(to):
    """`click_refused` opens `ready`, `blocked` and `posting_closed` (the ends `recheck` gives a
    never apply or a closed posting), never `cancelled`, `failed` or an outcome of a click."""
    application = Application(mode=Mode.review, state=State.submitting)
    if to in {State.ready, State.blocked, State.posting_closed}:
        with pytest.raises(states.TransitionError):
            states.move(application, to, AT)
        states.move(application, to, AT, click_refused=True)
        assert (application.state, application.submitted_at) == (to, None)
    elif to in states.TRANSITIONS[State.submitting]:
        states.move(application, to, AT, click_refused=True)  # a click's outcome, unchanged
        assert application.state is to
    else:
        with pytest.raises(states.TransitionError):
            states.move(application, to, AT, click_refused=True)
        assert application.state is State.submitting
