"""Which session a browser tool call of an agent run acts on. B20's Playwright CLI tools
(`finder_tools.py`) are registered once under their names and shared by the website search's
agent (`finder.py`) and One click Apply's (apply A19, `app/apply/applier.py`): each module that
runs such an agent registers how it finds the session of a run, and a run has one at most.
"""

import uuid
from collections.abc import Callable
from typing import Any

Find = Callable[[uuid.UUID | None], Any]
_FINDERS: list[Find] = []


def register(find: Find) -> Find:
    if find not in _FINDERS:
        _FINDERS.append(find)
    return find


def session_of(run_id: uuid.UUID | None) -> Any:
    for find in _FINDERS:
        if (found := find(run_id)) is not None:
            return found
    return None
