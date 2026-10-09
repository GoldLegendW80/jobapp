"""Rescoring Harald's real imported rows (read only from ../data and ../profile).

The engine scored each whole posting.txt, header included; the import keeps the header in
legacy["posting_header"] and the rest as posting_text. These tests prove on every real posting
that the text a rescore reads (scoring.engine_text) scores exactly like the original file, that
a rescore of the imported rows (real Postgres, real skills from profile/skills.json) leaves every
CSV fit that came from the engine identical, and that the import marks the others (the fits
Harald typed by hand, or scored on an older text) as set by hand so a rescore keeps them too.

Skipped, with the reason, where those files do not exist (CI, containers). Set
JOBAPP_SOURCE_DIR to the job folder to point at them explicitly.
"""

import csv
import os
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import scoring, service
from app.jobs.importer import convert as convert_jobs
from app.jobs.models import Job, JobEvent, JobEventKind
from app.profile.importer import SourcePaths
from app.profile.importer import convert as convert_profile
from app.profile.models import Profile
from app.profile.schemas import SkillsSection
from tests.identity.api_helpers import make_user
from tests.jobs.conftest import NOW

REQUIRED = ("profile/skills.json", "data/jobs.csv", "engine/jobs.py")


def _find_job_root() -> Path | None:
    explicit = os.environ.get("JOBAPP_SOURCE_DIR")
    candidates = [Path(explicit)] if explicit else list(Path(__file__).resolve().parents)
    for parent in candidates:
        if all((parent / name).is_file() for name in REQUIRED):
            return parent
    return None


ROOT = _find_job_root()
pytestmark = pytest.mark.skipif(
    ROOT is None,
    reason="Harald's data and profile are not on this machine; set JOBAPP_SOURCE_DIR to the job "
    "folder to run the real-data rescore test",
)


@pytest.fixture(scope="module")
def skills() -> SkillsSection:
    assert ROOT is not None
    return convert_profile(SourcePaths.under(ROOT)).sections.skills


@pytest.fixture(scope="module")
def csv_rows() -> dict[str, dict[str, str]]:
    assert ROOT is not None
    with (ROOT / "data" / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
        return {r["id"]: r for r in csv.DictReader(handle)}


def engine_fit(slug: str, title: str, skills: SkillsSection) -> int | None:
    """The fit of the whole posting.txt, as the engine scored it (the port equals the engine,
    test_scoring_golden.py)."""
    assert ROOT is not None
    path = ROOT / "data" / "jobs" / slug / "posting.txt"
    if not path.is_file():
        return None
    return scoring.score(path.read_text(encoding="utf-8"), skills, title=title).fit


def test_engine_text_scores_like_every_real_posting_file(
    skills: SkillsSection, csv_rows: dict[str, dict[str, str]]
) -> None:
    assert ROOT is not None
    compared = 0
    for item in convert_jobs(ROOT / "data").jobs:
        if item.posting_text is None:
            continue
        rebuilt = scoring.engine_text(item.posting_text, item.legacy.get("posting_header"))
        raw = (ROOT / "data" / "jobs" / item.slug / "posting.txt").read_text(encoding="utf-8")
        assert scoring.score(rebuilt, skills, title=item.title) == scoring.score(
            raw, skills, title=item.title
        ), item.slug
        compared += 1
    assert compared >= 300


def test_rescore_of_harald_imported_rows_keeps_every_csv_fit(
    factory: sessionmaker[Session],
    skills: SkillsSection,
    csv_rows: dict[str, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ROOT is not None
    user = make_user(factory, email="harald-like@example.org", password_hash="h").id
    with factory() as db, db.begin():
        db.add(
            Profile(
                user_id=user,
                sections={"skills": skills.model_dump(mode="json")},
                revision=1,
                updated_at=NOW,
            )
        )
    document = convert_jobs(ROOT / "data")
    with factory() as db:
        summary = service.import_jobs(db, user, document.jobs, NOW)
        db.commit()

    from_engine = {
        slug
        for slug, r in csv_rows.items()
        if r["fit"] and engine_fit(slug, r["title"], skills) == int(r["fit"])
    }
    by_hand = {
        slug
        for slug, r in csv_rows.items()
        if r["fit"] and engine_fit(slug, r["title"], skills) not in (None, int(r["fit"]))
    }
    assert len(from_engine) >= 300
    assert summary.fits_manual == len(by_hand)
    with factory() as db:
        marked = set(
            db.execute(sa.select(Job.slug).where(Job.user_id == user, Job.fit_overridden)).scalars()
        )
    assert marked == by_hand

    with factory() as db:
        outcome = service.RescoreOutcome()
        while service.rescore_batch(
            db, user, skills, NOW + timedelta(hours=1), ids=None, after=outcome.last_id,
            limit=100, overwrite_manual=False, outcome=outcome,
        ):  # fmt: skip
            db.commit()
    assert outcome.skipped_manual == len(by_hand)
    assert outcome.scored == len(from_engine) + len(by_hand)
    with factory() as db:
        fits = dict(db.execute(sa.select(Job.slug, Job.fit).where(Job.user_id == user)).all())
        moved = db.execute(
            sa.select(sa.func.count())
            .select_from(JobEvent)
            .where(JobEvent.user_id == user, JobEvent.kind == JobEventKind.fit)
        ).scalar_one()
    for slug, r in csv_rows.items():
        assert fits[slug] == (int(r["fit"]) if r["fit"] else None), slug
    assert moved == 0
    with capsys.disabled():
        print(
            f"\n  rescore of the real rows: {len(from_engine)} engine fits reproduced, "
            f"{len(by_hand)} fits set by hand kept, {outcome.skipped_no_text} rows without text"
        )
