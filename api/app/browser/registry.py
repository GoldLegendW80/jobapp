"""The routers of the browser module, included once by app.main (browser B3).

The list is complete from the first day, one line per router file, with the task that adds the
file (tasks/browser/todo.md, "Who owns which shared file"). A file that does not exist yet is
skipped; one that exists and fails to import fails the start. So a later task adds its file and
changes no line here, nor in app.main.
"""

import importlib
import importlib.util

from fastapi import APIRouter, FastAPI

ROUTER_MODULES: tuple[str, ...] = (
    "router",  # B3: /api/browser, /api/browser/sessions
    "live_router",  # B4: the live view's ticket, its check, take over and give back
    "profile_router",  # B7: forget all logins
    "linkedin_router",  # B10: the LinkedIn switch
    "handoff_router",  # B12: the sign in hand off
)


def load(
    names: tuple[str, ...] = ROUTER_MODULES, package: str = "app.browser"
) -> tuple[APIRouter, ...]:
    """The `router` of every listed module that exists, in the order of the list."""
    found: list[APIRouter] = []
    for name in names:
        if importlib.util.find_spec(f"{package}.{name}") is None:
            continue
        router = importlib.import_module(f"{package}.{name}").router
        if not isinstance(router, APIRouter):
            raise TypeError(f"{package}.{name}.router is not an APIRouter")
        found.append(router)
    return tuple(found)


def include(app: FastAPI) -> None:
    for router in load():
        app.include_router(router)
