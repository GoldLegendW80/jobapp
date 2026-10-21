"""`python -m app.profile.cli` as the operator runs it: convert on the Mac (no database), import
inside the container (as a subprocess, file and stdin), and the refusals."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.profile.cli import main
from app.profile.models import Profile, ProfileRevision
from app.profile.photo import UNREADABLE, normalize
from app.profile.schemas import IMPORT_MAX_BYTES, SECTIONS
from tests.identity.api_helpers import make_user
from tests.profile import images
from tests.profile.conftest import ALICE, BOB, full
from tests.profile.test_importer import write_sources

API_DIR = Path(__file__).resolve().parents[2]


def run_cli(url: str | None, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONIOENCODING"] = "utf-8"
    if url is not None:
        env["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "app.profile.cli", *args],
        cwd=API_DIR,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def converted(tmp_path: Path) -> Path:
    write_sources(tmp_path / "job")
    out = tmp_path / "profile.json"
    assert main(["convert", "--source-dir", str(tmp_path / "job"), "--out", str(out)]) == 0
    return out


def history(factory: sessionmaker[Session]) -> tuple[list[int], list[str]]:
    with factory() as db:
        revisions = list(db.execute(sa.select(Profile.revision)).scalars())
        sections = list(
            db.execute(
                sa.select(ProfileRevision.section).order_by(ProfileRevision.revision)
            ).scalars()
        )
    return revisions, sections


# --- convert ------------------------------------------------------------------------------------


def test_convert_writes_the_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_sources(tmp_path / "job")
    converted = tmp_path / "out.json"
    assert main(["convert", "--source-dir", str(tmp_path / "job"), "--out", str(converted)]) == 0
    out = capsys.readouterr().out
    assert out == (
        f"Wrote {converted}: 5 fact blocks, 1 employers, 1 schools, 2 boards, photo included.\n"
    )
    body = json.loads(converted.read_text(encoding="utf-8"))
    assert body["format"] == "jobapp-profile"
    assert list(body["sections"]) == list(SECTIONS)


def test_convert_to_stdout_without_a_database(tmp_path: Path) -> None:
    write_sources(tmp_path)
    result = run_cli(None, "convert", "--source-dir", str(tmp_path), "--out", "-")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["sections"]["identity"]["full_name"] == "Ada Example"


def test_convert_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", "--source-dir", str(tmp_path / "nope"), "--out", "-"]) == 2
    assert "is not a folder" in capsys.readouterr().err
    write_sources(tmp_path, facts=None)
    out = tmp_path / "x.json"
    assert main(["convert", "--source-dir", str(tmp_path), "--out", str(out)]) == 2
    assert "Cannot convert" in capsys.readouterr().err
    assert not out.exists()
    write_sources(tmp_path, boards=json.dumps({"title_re": "(a+)+", "boards": []}))
    assert main(["convert", "--source-dir", str(tmp_path), "--out", str(out)]) == 2
    assert "breaks a rule: sections.search.title_include.terms" in capsys.readouterr().err
    assert not out.exists()


# --- import -------------------------------------------------------------------------------------


def test_import_from_a_file(
    migrated_url: str, factory: sessionmaker[Session], converted: Path
) -> None:
    make_user(factory, email=ALICE)
    result = run_cli(
        migrated_url, "import", "--email", " Alice@Example.ORG ", "--file", str(converted)
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout.decode()
    assert stdout == (
        f"Imported the profile of {ALICE}: revision 7, 100% complete, photo replaced.\n"
    )
    assert history(factory) == ([len(SECTIONS)], list(SECTIONS))
    # Nothing of the profile itself is printed.
    assert "Ada" not in stdout
    for dash in (" - ", "—", "–"):
        assert dash not in stdout


def test_import_from_stdin_and_twice(
    migrated_url: str, factory: sessionmaker[Session], converted: Path
) -> None:
    make_user(factory, email=ALICE)
    raw = converted.read_bytes()
    for _ in range(2):
        result = run_cli(migrated_url, "import", "--email", ALICE, "--file", "-", stdin=raw)
        assert result.returncode == 0, result.stderr
    assert history(factory) == ([2 * len(SECTIONS)], list(SECTIONS) * 2)


def test_import_lists_what_is_missing(
    migrated_url: str,
    factory: sessionmaker[Session],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_user(factory, email=ALICE)
    write_sources(tmp_path / "job", boards=None, skills=None, photo=None)
    out = tmp_path / "p.json"
    assert main(["convert", "--source-dir", str(tmp_path / "job"), "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["import", "--email", ALICE, "--file", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("photo unchanged.")
    assert lines[1:] == [
        "  skills: 0%, missing years_experience, have",
        "  search: 40%, missing title_include, location, boards",
    ]


def test_import_touches_only_that_account(
    migrated_url: str, factory: sessionmaker[Session], converted: Path
) -> None:
    make_user(factory, email=ALICE)
    make_user(factory, email=BOB)
    assert main(["import", "--email", BOB, "--file", str(converted)]) == 0
    with factory() as db:
        owners = db.execute(
            sa.text("SELECT u.email FROM profiles p JOIN users u ON u.id = p.user_id")
        ).scalars()
        assert list(owners) == [BOB]


@pytest.mark.parametrize(
    ("args", "content", "code", "message"),
    [
        (["--email", "nobody@example.org"], None, 1, "No account with the email"),
        (["--email", "not-an-email"], None, 2, "Invalid email address"),
        (["--email", ALICE], b"{", 2, "The import file is not valid JSON."),
        (["--email", ALICE], b'{"format": "x"}', 2, "Unknown file format"),
        (
            ["--email", ALICE],
            json.dumps({"format": "jobapp-profile", "version": 9}).encode(),
            2,
            "Unsupported export version",
        ),
        (["--email", ALICE], b" " * (IMPORT_MAX_BYTES + 5), 2, "larger than"),
    ],
)
def test_import_refusals(
    migrated_url: str,
    factory: sessionmaker[Session],
    converted: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    content: bytes | None,
    code: int,
    message: str,
) -> None:
    make_user(factory, email=ALICE)
    if content is not None:
        converted.write_bytes(content)
    assert main(["import", *args, "--file", str(converted)]) == code
    err = capsys.readouterr().err
    assert message in err
    assert "Nothing was imported" in err
    assert history(factory) == ([], [])


def test_import_invalid_section_names_the_path(
    migrated_url: str,
    factory: sessionmaker[Session],
    converted: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_user(factory, email=ALICE)
    body = json.loads(converted.read_text(encoding="utf-8"))
    body["sections"]["identity"]["email"] = "broken"
    converted.write_text(json.dumps(body), encoding="utf-8")
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 2
    assert capsys.readouterr().err == (
        "Invalid profile, sections.identity.email: must be a valid email address. "
        "Nothing was imported.\n"
    )


def test_import_missing_file(
    migrated_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["import", "--email", ALICE, "--file", str(tmp_path / "none.json")]) == 2
    assert "Cannot read" in capsys.readouterr().err


def test_import_without_database_configuration(
    converted: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_import_database_down(
    converted: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_S", "2")
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 1
    assert "Database error, nothing was imported" in capsys.readouterr().err


def test_import_stores_the_photo_re_encoded(
    migrated_url: str, factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """A file written by hand (not by convert) with a photo full of metadata: the CLI stores it
    through the same pipeline as an upload."""
    make_user(factory, email=ALICE)
    source = images.jpeg_with_gps(orientation=6)
    body = {
        "format": "jobapp-profile",
        "version": 1,
        "exported_at": "2026-10-08T12:00:00Z",
        "sections": {name: full(name) for name in SECTIONS},
        "photo": {"mime": "image/jpeg", "base64": base64.b64encode(source).decode()},
    }
    file = tmp_path / "profile.json"
    file.write_text(json.dumps(body), encoding="utf-8")
    assert main(["import", "--email", ALICE, "--file", str(file)]) == 0
    with factory() as db:
        stored, mime = db.execute(sa.select(Profile.photo, Profile.photo_mime)).one()
    assert mime == "image/jpeg"
    assert stored == normalize(source).data
    assert images.metadata(stored) == {}
    assert images.opened(stored).size == (30, 40)


def test_import_refuses_a_photo_that_does_not_decode(
    migrated_url: str,
    factory: sessionmaker[Session],
    converted: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_user(factory, email=ALICE)
    body = json.loads(converted.read_text(encoding="utf-8"))
    truncated = images.png(300, 300)[:-60]
    body["photo"] = {"mime": "image/png", "base64": base64.b64encode(truncated).decode()}
    converted.write_text(json.dumps(body), encoding="utf-8")
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 2
    assert capsys.readouterr().err == (
        f"Invalid profile, photo: {UNREADABLE} Nothing was imported.\n"
    )
    assert history(factory) == ([], [])
