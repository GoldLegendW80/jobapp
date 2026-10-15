"""The design capture of an uploaded CV (SPEC-documents.md, "Design capture of an upload"): the
second step of a conversion, after its content.

`enqueue(final, document_id)` is the hook the base writer's final hook calls once an upload's
content conversion is done (task D24), with the run that converted it and the base CV's id. It
does nothing until task D25b writes the capture here; the base CV keeps `classic` meanwhile.
"""

from __future__ import annotations

import uuid

from app.agents.definitions import FinalRun


async def enqueue(final: FinalRun, document_id: uuid.UUID) -> None:
    """Start the design capture of a converted base CV: nothing yet (D25b)."""
    del final, document_id
