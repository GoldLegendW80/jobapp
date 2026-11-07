"""What the hiring panel hands a run in this process only (task D29): the first message of a
reviewer or the merge, and the instructions of a writer's rebuild, under a random key that the
run's input names. Nothing of it is stored with the run or logged: the run's input holds the key
only (the pattern of apply/model_check.py). A key nobody holds (a run started otherwise, or its
job gone) makes the definition raise LookupError, so the run fails before any model call.

Its own module so that the writers (writer_cv.py, writer_letter.py, writer_base.py), which
panel.py imports, can read a rebuild's instructions without importing panel.py.
"""

from __future__ import annotations

import secrets
import threading

KEY_PATTERN = r"^[0-9a-f]{32}$"

_HELD: dict[str, str] = {}
_LOCK = threading.Lock()


def hold(text: str) -> str:
    """Keep `text` under a new key, which the caller names in the run's input."""
    key = secrets.token_hex(16)
    with _LOCK:
        _HELD[key] = text
    return key


def read(key: object) -> str:
    """The text held under `key`. LookupError when nobody holds it."""
    with _LOCK:
        found = _HELD.get(str(key))
    if found is None:
        raise LookupError("no panel text held for this run")
    return found


def drop(key: str) -> None:
    with _LOCK:
        _HELD.pop(key, None)
