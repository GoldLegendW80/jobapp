"""The writers of the documents module, imported once by whoever runs or starts them (task D7).

Each writer is its own file (tasks/documents/todo.md, "Architecture decisions"), listed here with
the task that adds it. Importing a writer registers its agent (`definitions.register`) and, in the
worker, its tools (`tools.register`). A file that does not exist yet is skipped; one that exists
and fails to import fails the import of this module. `generate` comes last: it registers the
writers' final hook and D6's overflow hook (`render_job.on_overflow`).
"""

import importlib
import importlib.util
from types import ModuleType

# Modules of app.documents, in the order they are imported.
WRITERS: tuple[str, ...] = (
    "writer_cv",  # D7: documents_cv
    "writer_letter",  # D8: documents_letter
    "writer_base",  # D9: documents_base
    "writer_design",  # D25b: documents_design
    "chat",  # D17: the chat of a document
    "generate",  # D7: the generations, the final hook and the shortening pass
)


def load(
    names: tuple[str, ...] = WRITERS, package: str = "app.documents"
) -> tuple[ModuleType, ...]:
    """Every listed module that exists, imported in the order of the list."""
    found: list[ModuleType] = []
    for name in names:
        if importlib.util.find_spec(f"{package}.{name}") is None:
            continue
        found.append(importlib.import_module(f"{package}.{name}"))
    return tuple(found)


LOADED = load()
