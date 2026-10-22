"""A17: prune temporary screenshots; submission and saved-section evidence stays with its owner.

The caller owns the transaction and supplies the clock. Only step/error images strictly older
than the configured retention are deleted. Answers, events, before/after images and document
versions are untouched; their existing foreign keys cascade when their posting/account leaves.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.models import ApplicationScreenshot, ShotKind, SiteProfileShot, SiteShotKind


@dataclass(frozen=True)
class Pruned:
    applications: int
    site_profiles: int


def prune(db: Session, now: datetime, *, keep_days: int) -> Pruned:
    if type(keep_days) is not int or not 1 <= keep_days <= 365:
        raise ValueError("Screenshot retention must be between 1 and 365 days.")
    cutoff = now - timedelta(days=keep_days)
    applications = db.execute(
        sa.delete(ApplicationScreenshot).where(
            ApplicationScreenshot.kind.in_((ShotKind.step, ShotKind.error)),
            ApplicationScreenshot.created_at < cutoff,
        )
    ).rowcount
    site_profiles = db.execute(
        sa.delete(SiteProfileShot).where(
            SiteProfileShot.kind == SiteShotKind.step,
            SiteProfileShot.created_at < cutoff,
        )
    ).rowcount
    return Pruned(applications, site_profiles)
