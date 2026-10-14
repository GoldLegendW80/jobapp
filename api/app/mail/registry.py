"""What the mail module registers with the rest of the app, loaded once by the api and by the
worker (mail M4).

The list is complete from the first day, one line per module, with the task that adds the file
and what it registers (tasks/mail/todo.md, "Every hook, its file and its task"). A module that
does not exist yet is skipped; one that exists and fails to import, or has no `register`, fails
the start. Each module exposes `register()`, called once per process. So a later task adds its
file and changes no line here.
"""

import importlib
import importlib.util

MODULES: tuple[str, ...] = (
    "linking",  # M11b: the proposal kind `mail_link`
    "status",  # M13: the proposal kind `mail_status`
    "jobs_filter",  # M16: the `needs_reply` subquery of the jobs list
    "assist",  # M17: the agents `mail_link` and `mail_label`
    "reply_agent",  # M19: the agent `mail_reply`
    "checker_agent",  # M20: the agent `mail_check`
)

_loaded: set[str] = set()


def load(names: tuple[str, ...] = MODULES, package: str = "app.mail") -> tuple[str, ...]:
    """Call `register()` of every listed module that exists, once per process; the names that
    are registered after the call."""
    for name in names:
        full = f"{package}.{name}"
        if full in _loaded or importlib.util.find_spec(full) is None:
            continue
        importlib.import_module(full).register()
        _loaded.add(full)
    return tuple(name for name in names if f"{package}.{name}" in _loaded)
