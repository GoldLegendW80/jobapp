"""The routers of the mail module, included once by app.main, and the one middleware its routes
need (mail M4).

The list is complete from the first day, one line per router file, with the task that adds the
file (tasks/mail/todo.md, "Who owns which shared file"). A file that does not exist yet is
skipped; one that exists and fails to import fails the start. So a later task adds its file and
changes no line here.
"""

import importlib
import importlib.util
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response

ROUTER_MODULES: tuple[str, ...] = (
    "account_router",  # M4
    "drafts_router",  # M8
    "sync_router",  # M10
    "threads_router",  # M11b
    "send_router",  # M12
    "status_router",  # M13
    "attachments_router",  # M15
)

PREFIX = "/api/mail"


def load(
    names: tuple[str, ...] = ROUTER_MODULES, package: str = "app.mail"
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


def private(path: str) -> bool:
    """True for a path of this module: no answer under it is ever stored."""
    return path == PREFIX or path.startswith(f"{PREFIX}/")


async def no_store(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Every answer under /api/mail is `Cache-Control: no-store`, the ones built outside the
    routers included (the 401 of the session check, the 403 of the CSRF check, a 405, a 422).
    The 500 of an unhandled error is built outside every middleware: app.main sets the header
    there, with `private`."""
    response = await call_next(request)
    if private(request.url.path):
        response.headers["Cache-Control"] = "no-store"
    return response
