"""Apply A17: temporary-image retention, deletion and the code-only click boundary."""

import ast
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import service
from app.apply.models import (
    Application,
    ApplicationScreenshot,
    Mode,
    ShotKind,
    SiteKind,
    SiteProfileRun,
    SiteProfileShot,
    SiteShotKind,
)
from app.jobs.models import Job
from tests.apply.conftest import AT, Person

JPEG = b"\xff\xd8\xffe2e-retention-canary"


def images(db: Session, person: Person, stub: str) -> tuple[set, set]:
    made = service.start(
        db, person.user_id, person.posting_id, Mode.review, False, now=AT, stub_base=stub
    )
    run = SiteProfileRun(
        user_id=person.user_id,
        host="portal.example.org",
        url="https://portal.example.org/profile",
        site_kind=SiteKind.generic,
        created_at=AT,
        updated_at=AT,
    )
    db.add(run)
    db.flush()
    kept, expired = set(), set()
    for age in (timedelta(days=31), timedelta(days=30), timedelta(days=29)):
        for kind in ShotKind:
            row = ApplicationScreenshot(
                application_id=made.application.id,
                user_id=person.user_id,
                kind=kind,
                jpeg=JPEG,
                sha256=hashlib.sha256(JPEG).digest(),
                created_at=AT - age,
            )
            db.add(row)
            db.flush()
            target = expired if age.days > 30 and kind in (ShotKind.step, ShotKind.error) else kept
            target.add(row.id)
        for kind in SiteShotKind:
            row = SiteProfileShot(
                run_id=run.id,
                user_id=person.user_id,
                section="experience",
                kind=kind,
                jpeg=JPEG,
                sha256=hashlib.sha256(JPEG).digest(),
                created_at=AT - age,
            )
            db.add(row)
            db.flush()
            (expired if age.days > 30 and kind is SiteShotKind.step else kept).add(row.id)
    db.commit()
    return kept, expired


def test_prune_keeps_before_after_and_the_exact_retention_boundary(
    db: Session, person_a: Person, person_b: Person, forms_base_url: str
) -> None:
    from app.apply import privacy

    kept_a, expired_a = images(db, person_a, forms_base_url)
    kept_b, expired_b = images(db, person_b, forms_base_url)
    result = privacy.prune(db, AT, keep_days=30)
    db.commit()
    assert (result.applications, result.site_profiles) == (4, 2)
    left = set(db.scalars(sa.select(ApplicationScreenshot.id)))
    left.update(db.scalars(sa.select(SiteProfileShot.id)))
    assert left == kept_a | kept_b
    assert left.isdisjoint(expired_a | expired_b)
    assert privacy.prune(db, AT, keep_days=30) == privacy.Pruned(0, 0)


@pytest.mark.parametrize("keep_days", [0, -1, 366, True])
def test_invalid_retention_never_deletes(db: Session, keep_days: int) -> None:
    from app.apply import privacy

    with pytest.raises(ValueError, match="retention"):
        privacy.prune(db, AT, keep_days=keep_days)


def test_posting_deletion_cascades_applications_and_images_but_keeps_site_profiles(
    db: Session, person_a: Person, person_b: Person, forms_base_url: str
) -> None:
    images(db, person_a, forms_base_url)
    images(db, person_b, forms_base_url)
    db.execute(sa.delete(Job).where(Job.id == person_a.posting_id))
    db.commit()
    assert db.scalar(sa.select(sa.func.count()).select_from(Application)) == 1
    assert set(db.scalars(sa.select(Application.user_id))) == {person_b.user_id}
    assert set(db.scalars(sa.select(ApplicationScreenshot.user_id))) == {person_b.user_id}
    assert set(db.scalars(sa.select(SiteProfileShot.user_id))) == {
        person_a.user_id,
        person_b.user_id,
    }


def test_only_application_and_profile_gates_import_the_privileged_click() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "apply"
    callers = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "app.browser.tools"
                and any(name.name == "gate_click" for name in node.names)
            ):
                callers.add(path.name)
    assert callers <= {"gate.py", "profile_gate.py"}
    assert "gate.py" in callers
