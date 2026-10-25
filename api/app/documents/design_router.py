"""The person's explicit retry of an uploaded CV's design capture."""

import logging
import uuid

import anyio
from fastapi import APIRouter, Depends, Request, status

from app import queue as queues
from app.documents import design_capture, generate, store
from app.documents.base_router import GenerationOut
from app.documents.router import NOT_FOUND, CurrentUser, Db, _error, _private
from app.identity import deps

logger = logging.getLogger("app.documents.design")

NOT_UPLOADED = "This CV was not converted from an uploaded file."
RUNNING = "Another change to this document is still in progress."

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


@router.post(
    "/api/documents/{document_id}/design/capture",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def capture(document_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> GenerationOut:
    try:
        generation, run_id = design_capture.request(
            db,
            user.id,
            document_id,
            at=deps.clock(request),
            settings=request.app.state.settings,
        )
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except design_capture.CaptureRefusedError:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, NOT_UPLOADED) from None
    except store.GenerationOpenError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, RUNNING) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, store.BUSY) from None
    out = GenerationOut(
        id=generation.id,
        status=generation.status,
        wait_reason=generation.wait_reason,
        error=generation.error,
    )
    if run_id is not None:
        try:
            anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run_id)
        except queues.QueueUnavailableError:
            logger.warning("documents.design.run_not_queued", extra={"run_id": str(run_id)})
    return out
