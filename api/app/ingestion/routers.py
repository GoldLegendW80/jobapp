"""The routers of the ingestion module, included once by app.main, and the one middleware its
routes need.

One line per router file, added by the task that owns the file (tasks/ingestion/todo.md, "Who
owns which shared file"): documents_router G1, prompt_router G3, estimate_router G16,
runs_router G10, review_router G12. No two tasks of one group add a line here.
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response

from app.ingestion import documents_router, estimate_router, prompt_router, runs_router

routers: tuple[APIRouter, ...] = (
    documents_router.router,
    prompt_router.router,
    estimate_router.router,
    runs_router.router,
)

PREFIX = "/api/ingestion"


def private(path: str) -> bool:
    """True for a path of this module: no answer under it is ever stored."""
    return path == PREFIX or path.startswith(f"{PREFIX}/")


async def no_store(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Every answer under /api/ingestion is `Cache-Control: no-store`: documents, their text and
    what is drawn from them are private, and no browser or proxy keeps a copy. A middleware,
    because some answers are built outside the routers: the 401 of the session check, the 403
    of the CSRF check, a 405, the 422 of request validation. The 500 of an unhandled error is
    built outside every middleware: app.main sets the header there, with `private`."""
    response = await call_next(request)
    if private(request.url.path):
        response.headers["Cache-Control"] = "no-store"
    return response
