"""The routers of the documents module, included once by app.main (documents D6).

The list is complete from the first day, one line per router of the plan, with the task that
adds its file (tasks/documents/todo.md, "Who owns which shared file"). A file that does not
exist yet is skipped; one that exists and fails to import fails the start. The literal paths
(`settings`, `templates`, then `base-cvs` and `consent` of D9, `spend` of D10) come before D6's
`router`, which serves `/api/documents/{document_id}`: FastAPI does not fall through to a
later route when a path segment fails its type, so `/api/documents/settings` read as a document
id would answer 422.
"""

import importlib
import importlib.util
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.documents")

# (module of app.documents, its attribute), in the order they are included.
ROUTERS: tuple[tuple[str, str], ...] = (
    ("router", "settings_router"),  # D6: /api/documents/settings
    ("router", "templates_router"),  # D6: /api/document-templates
    ("base_router", "router"),  # D9: /api/documents/base-cvs, /api/documents/consent
    ("auto_router", "router"),  # D10: /api/documents/spend
    ("freeze_router", "router"),  # D14
    ("chat_router", "router"),  # D17
    ("design_router", "router"),  # D25b
    ("router", "router"),  # D6: /api/jobs/{id}/documents, /api/documents/{id} and below, last
)

PREFIXES = ("/api/documents", "/api/document-generations")


def private(path: str) -> bool:
    """Match the documents and generation prefixes, leaving bundled template thumbnails out."""
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in PREFIXES)


async def no_store(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Private cache headers for documents answers, including early refusals. An unhandled
    error under these prefixes is answered here, a fixed 500 logged by its class only: one that
    left the app would be logged whole by the server, its message and traceback included."""
    if not private(request.url.path):
        return await call_next(request)
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("documents.request.failed", extra={"error": type(exc).__name__})
        response = JSONResponse(status_code=500, content={"detail": "internal server error"})
    response.headers["Cache-Control"] = "private, no-store"
    return response


def load(
    names: tuple[tuple[str, str], ...] = ROUTERS, package: str = "app.documents"
) -> tuple[APIRouter, ...]:
    """Every listed router whose module exists, in the order of the list."""
    found: list[APIRouter] = []
    for module, attribute in names:
        if importlib.util.find_spec(f"{package}.{module}") is None:
            continue
        router = getattr(importlib.import_module(f"{package}.{module}"), attribute)
        if not isinstance(router, APIRouter):
            raise TypeError(f"{package}.{module}.{attribute} is not an APIRouter")
        found.append(router)
    return tuple(found)
