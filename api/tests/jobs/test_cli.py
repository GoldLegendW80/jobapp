"""`python -m app.jobs.cli` as the operator runs it: convert on the Mac (no database), import
inside the container (as a subprocess, from a file and from stdin, twice), and the refusals."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import cli
from app.jobs.cli import main
from app.jobs.models import Job, JobFile
from tests.identity.api_helpers import make_user
from tests.jobs.sources import PDF, posting, row, write_data

API_DIR = Path(__file__).resolve().parents[2]
ALICE = "alice@example.org"


def run_cli(url: str | None, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONIOENCODING"] = "utf-8"
    if url is not None:
        env["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "app.jobs.cli", *args],
        cwd=API_DIR,
        env=env,
        input=stdin,
        capture_output=True,
        timeout=60,
        check=False,
    )


def sample(root: Path) -> Path:
    rows = [
        row(status="applied", applied_on="2026-10-06", notes="private note"),
        row(id="beta-ml", company="Beta", title="ML", url="https://e.org/b", status="ghosted"),
    ]
    files = {"acme-ai-engineer": {"posting.txt": posting(), "cv.pdf": PDF, "cv.html": "<p/>"}}
    return write_data(root, rows, files)


@pytest.fixture
def converted(tmp_path: Path) -> Path:
    out = tmp_path / "jobs.json"
    assert main(["convert", "--source-dir", str(sample(tmp_path)), "--out", str(out)]) == 0
    return out


def test_convert_to_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "jobs.json"
    assert main(["convert", "--source-dir", str(sample(tmp_path)), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(
        f"Wrote {out}. Converted 2 rows (applied 1, found 1), 1 posting texts, 1 files, "
        "1 warnings, "
    )
    assert "  warning: beta-ml: unknown status, stored as found\n" in printed
    body = json.loads(out.read_text(encoding="utf-8"))
    assert (body["format"], body["version"], len(body["jobs"])) == ("jobapp-jobs", 1, 2)


def test_convert_to_stdout_without_a_database(tmp_path: Path) -> None:
    result = run_cli(None, "convert", "--source-dir", str(sample(tmp_path)), "--out", "-")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)  # stdout is the document alone
    assert [j["slug"] for j in body["jobs"]] == ["acme-ai-engineer", "beta-ml"]
    assert b"Converted 2 rows" in result.stderr


def test_convert_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", "--source-dir", str(tmp_path / "nope"), "--out", "-"]) == 2
    assert "is not a folder" in capsys.readouterr().err
    out = tmp_path / "x.json"
    assert main(["convert", "--source-dir", str(tmp_path), "--out", str(out)]) == 2
    assert "Cannot convert: cannot read jobs.csv" in capsys.readouterr().err
    data = write_data(tmp_path, [row(company="x" * 2000)])
    assert main(["convert", "--source-dir", str(data), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "Cannot convert: row 2 (acme-ai-engineer) breaks a rule: company: String" in err
    assert not out.exists()


def test_import_from_a_file_then_stdin(
    migrated_url: str, factory: sessionmaker[Session], converted: Path
) -> None:
    make_user(factory, email=ALICE, password_hash="h")
    result = run_cli(
        migrated_url, "import", "--email", " Alice@Example.ORG ", "--file", str(converted)
    )
    assert result.returncode == 0, result.stderr
    stdout = result.stdout.decode()
    assert stdout.splitlines()[0] == (
        "Imported 2 rows: 2 created, 0 updated, 0 unchanged, 0 skipped. Files: 1 added, "
        "0 replaced, 0 unchanged, 0 kept as sent. Warnings: 1."
    )
    # Nothing private is printed: no email, no notes, no posting text.
    for secret in (ALICE, "private note", "Build agents"):
        assert secret not in stdout
    for dash in (" - ", "—", "–"):
        assert dash not in stdout
    again = run_cli(
        migrated_url, "import", "--email", ALICE, "--file", "-", stdin=converted.read_bytes()
    )
    assert again.returncode == 0, again.stderr
    assert again.stdout.decode().startswith(
        "Imported 2 rows: 0 created, 0 updated, 2 unchanged, 0 skipped. Files: 0 added, "
        "0 replaced, 1 unchanged, 0 kept as sent."
    )
    with factory() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 2
        assert db.execute(sa.select(JobFile.content)).scalar_one() == PDF


def test_import_refusals(
    migrated_url: str,
    factory: sessionmaker[Session],
    converted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = str(converted)
    assert main(["import", "--email", "not-an-email", "--file", file]) == 2
    assert "Invalid email address" in capsys.readouterr().err
    assert main(["import", "--email", ALICE, "--file", str(tmp_path / "nope.json")]) == 2
    assert "Cannot read" in capsys.readouterr().err
    assert main(["import", "--email", ALICE, "--file", file]) == 1
    err = capsys.readouterr().err
    assert err == "No account with that email. Nothing was imported.\n"
    bad = tmp_path / "bad.json"
    body = json.loads(converted.read_text())
    body["jobs"][0]["files"][0]["sha256"] = "0" * 64
    bad.write_text(json.dumps(body))
    assert main(["import", "--email", ALICE, "--file", str(bad)]) == 2
    assert "Invalid document, jobs.0.files.0: sha256 does not match" in capsys.readouterr().err
    bad.write_text('{"format": "jobapp-profile"}')
    assert main(["import", "--email", ALICE, "--file", str(bad)]) == 2
    assert "Unknown file format" in capsys.readouterr().err
    monkeypatch.setattr(cli, "IMPORT_CLI_MAX_BYTES", 100)
    assert main(["import", "--email", ALICE, "--file", file]) == 2
    assert "larger than 100 bytes" in capsys.readouterr().err
    with factory() as db:
        assert db.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 0


def test_import_database_error(
    converted: Path, factory: sessionmaker[Session], migrated_url: str,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    make_user(factory, email=ALICE, password_hash="h")
    monkeypatch.setenv("DATABASE_URL", migrated_url.replace("jobapp_test", "missing_db"))
    from app.config import get_settings

    get_settings.cache_clear()
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 1
    assert "Database error, nothing was imported: OperationalError" in capsys.readouterr().err


def test_config_error(
    converted: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["import", "--email", ALICE, "--file", str(converted)]) == 2
    assert capsys.readouterr().err
