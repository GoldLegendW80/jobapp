"""Only a definite browser refusal before the click returns a submitting application to ready."""

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
