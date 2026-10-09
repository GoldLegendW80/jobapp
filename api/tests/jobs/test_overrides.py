"""Fits set by hand and the text a rescore reads, on Harald-like synthetic data (runs everywhere):

- scoring.engine_text puts the posting.txt header back, so a rescore of an imported row reads
  what the engine read and gives the CSV fit for every row the engine scored;
- the import marks rows whose CSV fit differs from that computed fit as set by hand, counts
  them, stays idempotent, and a rescore keeps them;
- `python -m app.jobs.cli mark-overrides` finds the same rows on data imported before (dry run
  by default, --apply marks them, a second run finds none);
- the undo token module on its own; concurrent edits of one job.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import scoring, service
from app.jobs import undo as undo_tokens
from app.jobs.importer import convert, parse_posting
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.profile.models import Profile
from app.profile.schemas import SkillsSection
from tests.identity.api_helpers import make_user
from tests.jobs.conftest import NOW
from tests.jobs.sources import row, write_data
from tests.jobs.test_cli import run_cli
from tests.jobs.test_scoring import skills as make_skills

ALICE = "alice@example.org"
SKILLS = make_skills()

# Postings shaped like Harald's posting.txt: a header whose words count (the title names an
# agent role, the company is a RAG shop) and a body with or without a requirements part.
BODIES = {
    "acme-agent-engineer": (
        "Acme RAG Labs",
        "Senior Agent Engineer",
        "Build agentic systems.\n\nRequirements\n- Python, FastAPI\n- 3+ years of experience\n",
    ),
    "beta-ml-researcher": (
        "Beta",
        "ML Researcher (PyTorch)",
        "We publish papers. A PhD is required. Python helps.\n",
    ),
    "gamma-fullstack": (
        "Gamma Retrieval",
        "Full-stack developer",
        "React and Python. Some tool use with LLM agents.\n\nAbout us\nWe like RAG.\n",
    ),
    "delta-dotnet": ("Delta", "C# developer", "Développeur .NET, expérience 5 ans.\n"),
}


def posting_file(slug: str) -> str:
    company, title, body = BODIES[slug]
    return (
        f"COMPANY: {company}\nTITLE: {title}\nLOCATION: Paris, France\n"
        f"URL: https://jobs.ashbyhq.com/{slug}/1\nPUBLISHED: 2026-10-01\n\n{body}"
    )


def engine_fit(slug: str) -> int:
    """The fit the engine computes on the whole posting.txt (the port equals the engine: see
    test_scoring_golden.py)."""
    return scoring.score(posting_file(slug), SKILLS, title=BODIES[slug][1]).fit


HAND = {"beta-ml-researcher": 77}  # Harald typed this fit in the CSV


def harald_like(tmp_path: Path) -> Path:
    rows = []
    for slug, (company, title, _) in BODIES.items():
        fit = HAND.get(slug, engine_fit(slug))
        # As engine/jobs.py score_row wrote them: competencies and gaps always computed.
        result = scoring.score(posting_file(slug), SKILLS, title=title)
        rows.append(
            row(
                id=slug,
                company=company,
                title=title,
                url=f"https://jobs.ashbyhq.com/{slug}/1",
                fit=str(fit),
                competencies=", ".join(result.competencies),
                gaps=", ".join(result.gaps),
                status="shortlisted",
            )
        )
    rows.append(row(id="no-posting", company="Zeta", title="X", url="https://z.org/1", fit="64"))
    files = {slug: {"posting.txt": posting_file(slug)} for slug in BODIES}
    return write_data(tmp_path, rows, files)


def give_skills(factory: sessionmaker[Session], user: uuid.UUID, section: SkillsSection) -> None:
    with factory() as db, db.begin():
        db.add(
            Profile(
                user_id=user,
                sections={"skills": section.model_dump(mode="json")},
                revision=1,
                updated_at=NOW,
            )
        )


# --- engine_text --------------------------------------------------------------------------------


def test_engine_text_rebuilds_the_posting_file() -> None:
    for slug in BODIES:
        raw = posting_file(slug)
        parsed = parse_posting(raw)
        rebuilt = scoring.engine_text(parsed.text, parsed.header)
        assert rebuilt == raw.rstrip("\n")
        title = BODIES[slug][1]
        assert scoring.score(rebuilt, SKILLS, title=title) == scoring.score(
            raw, SKILLS, title=title
        )


def test_the_header_changes_the_fit_on_these_fixtures() -> None:
    """Without the header the fits differ: the fixture really tests the header."""
    differs = 0
    for slug in BODIES:
        parsed = parse_posting(posting_file(slug))
        title = BODIES[slug][1]
        differs += scoring.score(parsed.text, SKILLS, title=title).fit != engine_fit(slug)
    assert differs >= 2


@pytest.mark.parametrize("header", [None, {}, "COMPANY: x", ["a"], 3])
def test_engine_text_without_a_usable_header(header: Any) -> None:
    assert scoring.engine_text("body", header) == "body"


def test_engine_text_keeps_header_order_and_unicode() -> None:
    header = {"TITLE": "Ingénieur IA", "COMPANY": "Société", "EMPTY": ""}
    assert scoring.engine_text("Texte", header) == (
        "TITLE: Ingénieur IA\nCOMPANY: Société\nEMPTY: \n\nTexte"
    )


# --- import marks hand-set fits, rescore keeps them ---------------------------------------------


def test_import_marks_hand_set_fits_and_rescore_reproduces_the_others(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    user = make_user(factory, email=ALICE, password_hash="h").id
    give_skills(factory, user, SKILLS)
    document = convert(harald_like(tmp_path))
    with factory() as db:
        summary = service.import_jobs(db, user, document.jobs, NOW)
        db.commit()
    assert (summary.created, summary.fits_manual, summary.fits_checked) == (5, 1, True)
    with factory() as db:
        jobs = {j.slug: j for j in db.execute(sa.select(Job).where(Job.user_id == user)).scalars()}
        assert {s for s, j in jobs.items() if j.fit_overridden} == {"beta-ml-researcher"}
        assert jobs["no-posting"].fit_overridden is False  # no text: nothing to compare

    # A rescore of every row: the engine-scored fits come back identical, the hand one stays.
    with factory() as db:
        outcome = service.RescoreOutcome()
        service.rescore_batch(
            db, user, SKILLS, NOW + timedelta(hours=1), ids=None, after=None, limit=100,
            overwrite_manual=False, outcome=outcome,
        )  # fmt: skip
        db.commit()
    assert (outcome.scored, outcome.skipped_manual, outcome.skipped_no_text) == (4, 1, 1)
    with factory() as db:
        fits = {
            j.slug: j.fit for j in db.execute(sa.select(Job).where(Job.user_id == user)).scalars()
        }
    assert fits == {
        **{slug: engine_fit(slug) for slug in BODIES},
        "beta-ml-researcher": 77,
        "no-posting": 64,
    }
    with factory() as db:
        fit_events = db.execute(
            sa.select(sa.func.count()).where(JobEvent.kind == JobEventKind.fit)
        ).scalar_one()
        assert fit_events == 0  # no fit moved

    # A second import changes nothing, the marks included.
    with factory() as db:
        again = service.import_jobs(db, user, document.jobs, NOW + timedelta(hours=2))
        db.commit()
    assert (again.created, again.updated, again.unchanged, again.fits_manual) == (0, 0, 5, 1)


def test_import_without_skills_marks_nothing_then_with_skills_marks_once(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    user = make_user(factory, email=ALICE, password_hash="h").id
    document = convert(harald_like(tmp_path))
    with factory() as db:
        first = service.import_jobs(db, user, document.jobs, NOW)
        db.commit()
    assert (first.fits_manual, first.fits_checked) == (0, False)
    give_skills(factory, user, SKILLS)
    with factory() as db:
        second = service.import_jobs(db, user, document.jobs, NOW + timedelta(minutes=1))
        db.commit()
    assert (second.updated, second.unchanged, second.fits_manual) == (1, 4, 1)
    with factory() as db:
        job = db.execute(sa.select(Job).where(Job.slug == "beta-ml-researcher")).scalar_one()
        assert job.fit_overridden is True
        (event,) = db.execute(
            sa.select(JobEvent).where(
                JobEvent.job_id == job.id, JobEvent.kind == JobEventKind.import_
            )
        ).scalars()
        assert event.data == {"changed": ["fit_overridden"]}


# --- mark-overrides CLI -------------------------------------------------------------------------


def import_unmarked(tmp_path: Path, factory: sessionmaker[Session]) -> uuid.UUID:
    """Rows as the J2 import left them: every fit_overridden false, then the skills arrive."""
    user = make_user(factory, email=ALICE, password_hash="h").id
    with factory() as db:
        service.import_jobs(db, user, convert(harald_like(tmp_path)).jobs, NOW)
        db.commit()
    give_skills(factory, user, SKILLS)
    return user


def test_mark_overrides_dry_run_then_apply_then_nothing(
    tmp_path: Path, migrated_url: str, factory: sessionmaker[Session]
) -> None:
    user = import_unmarked(tmp_path, factory)
    dry = run_cli(migrated_url, "mark-overrides", "--email", ALICE)
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.decode().splitlines() == [
        "Dry run: would mark 1 fits as set by hand (they differ from the computed fit).",
        f"  beta-ml-researcher: fit 77, computed {engine_fit('beta-ml-researcher')}",
        "Nothing was changed. Run again with --apply to mark them.",
    ]
    with factory() as db:
        marked = db.execute(
            sa.select(sa.func.count()).where(Job.user_id == user, Job.fit_overridden)
        ).scalar_one()
        assert marked == 0

    applied = run_cli(migrated_url, "mark-overrides", "--email", " ALICE@example.org ", "--apply")
    assert applied.returncode == 0, applied.stderr
    assert applied.stdout.decode().startswith("Marked 1 fits as set by hand")
    with factory() as db:
        job = db.execute(sa.select(Job).where(Job.slug == "beta-ml-researcher")).scalar_one()
        assert (job.fit, job.fit_overridden) == (77, True)
        events = [
            e.data
            for e in db.execute(sa.select(JobEvent).where(JobEvent.job_id == job.id)).scalars()
            if e.kind is JobEventKind.fit
        ]
        assert events == [{"from": 77, "to": 77, "manual": True, "via": "mark-overrides"}]

    again = run_cli(migrated_url, "mark-overrides", "--email", ALICE, "--apply")
    assert again.returncode == 0
    assert again.stdout.decode().splitlines() == [
        "Marked 0 fits as set by hand (they differ from the computed fit)."
    ]


def test_mark_overrides_refusals(
    tmp_path: Path, migrated_url: str, factory: sessionmaker[Session]
) -> None:
    bad = run_cli(migrated_url, "mark-overrides", "--email", "not an email")
    assert (bad.returncode, b"Invalid email" in bad.stderr) == (2, True)
    nobody = run_cli(migrated_url, "mark-overrides", "--email", ALICE)
    assert (nobody.returncode, b"No account" in nobody.stderr) == (1, True)
    make_user(factory, email=ALICE, password_hash="h")
    no_skills = run_cli(migrated_url, "mark-overrides", "--email", ALICE)
    assert no_skills.returncode == 1
    assert b"no skills" in no_skills.stderr
    assert run_cli(None, "mark-overrides").returncode == 2  # --email is required


# --- undo tokens --------------------------------------------------------------------------------

KEY = undo_tokens.derive_key(b"s" * 40)
USER = uuid.uuid4()
WHEN = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def previous(n: int) -> list[undo_tokens.Previous]:
    return [
        undo_tokens.Previous(
            job_id=uuid.uuid4(),
            status=JobStatus.found if i % 2 else JobStatus.interview,
            applied_on=None if i % 2 else WHEN.date(),
            updated_at=WHEN + timedelta(microseconds=i),
        )
        for i in range(n)
    ]


def test_token_round_trip_of_500_jobs_stays_small() -> None:
    jobs = previous(500)
    token = undo_tokens.sign(KEY, USER, jobs, WHEN)
    assert len(token) < 60_000
    assert undo_tokens.verify(KEY, token, USER, WHEN + timedelta(minutes=10)) == jobs
    with pytest.raises(undo_tokens.ExpiredTokenError):
        undo_tokens.verify(KEY, token, USER, WHEN + timedelta(minutes=10, seconds=1))
    with pytest.raises(undo_tokens.InvalidTokenError):
        undo_tokens.verify(KEY, token, uuid.uuid4(), WHEN)
    with pytest.raises(undo_tokens.InvalidTokenError):
        undo_tokens.verify(undo_tokens.derive_key(b"other" * 8), token, USER, WHEN)
    assert undo_tokens.expires_at(WHEN) == WHEN + undo_tokens.UNDO_WINDOW


def test_signed_garbage_is_still_refused() -> None:
    """A payload signed with the right key but not a token (a key leak, a bug) is refused."""
    import base64
    import hashlib
    import hmac
    import zlib

    for body in (b"not json", b'{"u": 1}', b'{"u": "%s", "x": 1, "j": [[1]]}' % USER.hex.encode()):
        payload = zlib.compress(body)
        signature = hmac.new(KEY, payload, hashlib.sha256).digest()
        token = ".".join(
            base64.urlsafe_b64encode(p).decode().rstrip("=") for p in (payload, signature)
        )
        with pytest.raises(undo_tokens.InvalidTokenError):
            undo_tokens.verify(KEY, token, USER, WHEN)
    with pytest.raises(undo_tokens.InvalidTokenError):
        undo_tokens.verify(KEY, "a" * 200_001, USER, WHEN)


# --- concurrency --------------------------------------------------------------------------------


def test_two_concurrent_edits_of_one_version_let_one_through(
    factory: sessionmaker[Session],
) -> None:
    user = make_user(factory, email=ALICE, password_hash="h").id
    with factory() as db, db.begin():
        job = Job(
            user_id=user, slug="acme-x", company="Acme", title="X", created_at=NOW, updated_at=NOW
        )
        db.add(job)
    version = NOW
    barrier = threading.Barrier(2)
    results: list[str] = []

    def edit(status: JobStatus) -> None:
        with factory() as db:
            barrier.wait()
            locked = service.lock_job(db, user, job.id)
            assert locked is not None
            try:
                service.edit_job(
                    db,
                    locked,
                    service.JobChanges(status=status),
                    NOW,
                    expected_updated_at=version,
                )
                db.commit()
                results.append("ok")
            except service.StaleJobError:
                db.rollback()
                results.append("stale")

    threads = [
        threading.Thread(target=edit, args=(s,)) for s in (JobStatus.skipped, JobStatus.applied)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == ["ok", "stale"]
    with factory() as db:
        count = db.execute(
            sa.select(sa.func.count()).where(JobEvent.kind == JobEventKind.status)
        ).scalar_one()
        assert count == 1


def test_mark_overrides_in_process(
    tmp_path: Path,
    migrated_url: str,
    factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same command through main() (DATABASE_URL is set by the fixture), for coverage of
    the code the subprocess runs."""
    from app.jobs.cli import main

    import_unmarked(tmp_path, factory)
    assert main(["mark-overrides", "--email", ALICE]) == 0
    assert "would mark 1 fits" in capsys.readouterr().out
    assert main(["mark-overrides", "--email", ALICE, "--apply"]) == 0
    assert "Marked 1 fits" in capsys.readouterr().out
    assert main(["mark-overrides", "--email", ALICE, "--apply"]) == 0
    assert "Marked 0 fits" in capsys.readouterr().out
    assert main(["mark-overrides", "--email", "bob@example.org"]) == 1
