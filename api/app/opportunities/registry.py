"""The routers of the opportunities module, included once by app.main (opportunities O1).

The list is complete from the first day, one line per router file, with the task that adds the
file (tasks/opportunities/todo.md, "Who owns which shared file"). A file that does not exist yet
is skipped; one that exists and fails to import fails the start. So a later task adds its file
and changes no line here, nor in app.main.
"""

import importlib
import importlib.util

from fastapi import APIRouter, FastAPI

ROUTER_MODULES: tuple[str, ...] = (
    "router",  # O1: the deck, the Pass and its undo
    "onboarding_router",  # O5: the first login wizard
    "platforms_router",  # O7b: the platforms read with the person's opt in
)


def load(
    names: tuple[str, ...] = ROUTER_MODULES, package: str = "app.opportunities"
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
