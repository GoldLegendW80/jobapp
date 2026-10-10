"""The routers of the ingestion module, included once by app.main.

One line per router file, added by the task that owns the file (tasks/ingestion/todo.md, "Who
owns which shared file"): documents_router G1, prompt_router G3, estimate_router G16,
runs_router G10, review_router G12. No two tasks of one group add a line here.
"""

from fastapi import APIRouter

from app.ingestion import documents_router

routers: tuple[APIRouter, ...] = (documents_router.router,)
