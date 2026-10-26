"""A render wait belongs to one actual fill attempt, not to a poll or a dead lease."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from app.apply import run
from tests.apply.conftest import AT


def test_a_changed_needs_does_not_restart_the_same_attempts_render_wait(monkeypatch) -> None:
    previous = SimpleNamespace(at=AT, data={"needs": run.RENDERING, "document": "cv", "attempt": 1})
    db = Mock()
    db.execute.return_value.first.return_value = previous
    application = SimpleNamespace(id="application", user_id="person", needs="retry", attempt=1)
    event = Mock()
    monkeypatch.setattr(run, "_event", event)
    assert run._render_wait_began(db, application, "cv", AT + timedelta(hours=2)) == AT
    event.assert_not_called()
