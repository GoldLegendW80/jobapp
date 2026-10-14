"""The screenshots of an application (SPEC-apply, "Audit trail and run report"): taken through the
lease's tools, kept as JPEG in `application_screenshots`, read back by their owner only.

`take` asks the tool layer for a full page JPEG and falls back to the visible part when the page
is too large for one image; what it returns is checked to be a JPEG of at most `MAX_BYTES` (2 MB,
the spec's ceiling), else nothing is kept and the caller records `screenshot_failed`. A
screenshot holds whatever the form showed, so it is the person's data: the bytes go to the
database and nowhere else (no log, no event, no job result; an event names the row id, the kind
and the size). `get` and `of_application` filter on the person's id as well as the application's,
so another person's id answers None (test_a_screenshot_is_read_by_its_owner_only).

Keeping: "before" and "after" live as long as the application (the table cascades with it); step
and error images go after `APPLY_STEP_SHOTS_KEEP_DAYS`, which A17's purge applies.
"""

import base64
import binascii
import hashlib
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.models import Application, ApplicationScreenshot, ShotKind

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"


class Shooter(Protocol):
    async def screenshot(self, *, full_page: bool = False, kind: str = "png") -> dict[str, Any]: ...


def _jpeg(result: Mapping[str, Any]) -> bytes | None:
    if not result.get("ok") or result.get("type") != "jpeg":
        return None
    try:
        data = base64.b64decode(str(result.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data.startswith(JPEG_MAGIC) or len(data) > MAX_BYTES:
        return None
    return data


async def take(tools: Shooter) -> bytes | None:
    """The page as one JPEG: the full page, else the visible part; None when neither came."""
    found = _jpeg(await tools.screenshot(full_page=True, kind="jpeg"))
    if found is None:
        found = _jpeg(await tools.screenshot(full_page=False, kind="jpeg"))
    return found


def store(
    db: Session, application: Application, kind: ShotKind, jpeg: bytes, now: datetime
) -> ApplicationScreenshot:
    """Adds the row (the caller commits)."""
    shot = ApplicationScreenshot(
        id=uuid.uuid4(),
        application_id=application.id,
        user_id=application.user_id,
        kind=kind,
        jpeg=jpeg,
        sha256=hashlib.sha256(jpeg).digest(),
        created_at=now,
    )
    db.add(shot)
    return shot


def of_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationScreenshot]:
    """The person's screenshots of one application, oldest first, without their bytes."""
    return list(
        db.execute(
            sa.select(ApplicationScreenshot)
            .where(
                ApplicationScreenshot.user_id == user_id,
                ApplicationScreenshot.application_id == application_id,
            )
            .order_by(ApplicationScreenshot.created_at, ApplicationScreenshot.id)
        ).scalars()
    )


def get(db: Session, user_id: uuid.UUID, shot_id: uuid.UUID) -> bytes | None:
    """The JPEG of one of the person's screenshots; None for any other id."""
    found = db.execute(
        sa.select(ApplicationScreenshot.jpeg).where(
            ApplicationScreenshot.user_id == user_id, ApplicationScreenshot.id == shot_id
        )
    ).scalar_one_or_none()
    return bytes(found) if found is not None else None
