"""`python -m app.identity.cli` as the operator runs it: a subprocess against the test database."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.identity import security, service
from app.identity.cli import main
from app.identity.models import Invite, InviteKind, Role, User

API_DIR = Path(__file__).resolve().parents[2]
LINK = re.compile(r"/(invite|reset)/([A-Za-z0-9_-]{43})\b")


def run_cli(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env.update(DATABASE_URL=url, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "app.identity.cli", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def links(output: str) -> list[tuple[str, str]]:
    return LINK.findall(output)


def test_create_admin_happy_path(migrated_url: str, db: Session) -> None:
    result = run_cli(migrated_url, "create-admin", "  Harald@Example.ORG ")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    found = links(result.stdout)
    assert len(found) == 1
    kind, token = found[0]
    assert kind == "invite"
    assert f"  /invite/{token}\n" in result.stdout
    assert "harald@example.org" in result.stdout
    assert "public URL" in result.stdout
    for dash in (" - ", "\u2014", "\u2013"):
        assert dash not in result.stdout
    invite = service.lookup_invite(db, token)
    assert invite is not None
    assert invite.email == "harald@example.org"
    assert invite.kind is InviteKind.invite
    assert invite.role is Role.admin
    assert invite.created_by is None


def test_running_twice_revokes_the_first_link(migrated_url: str, db: Session) -> None:
    first = links(run_cli(migrated_url, "create-admin", "h@example.org").stdout)[0][1]
    second = links(run_cli(migrated_url, "create-admin", "H@EXAMPLE.org").stdout)[0][1]
    assert first != second
    assert service.lookup_invite(db, first) is None
    assert service.lookup_invite(db, second) is not None
    assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 2


def test_base_url_prints_full_link(migrated_url: str) -> None:
    result = run_cli(
        migrated_url, "create-admin", "b@example.org", "--base-url", "https://abc.example.com/"
    )
    assert result.returncode == 0, result.stderr
    assert re.search(r"  https://abc\.example\.com/invite/[A-Za-z0-9_-]{43}\n", result.stdout)
    assert "Prefix" not in result.stdout


def test_bad_base_url(migrated_url: str, db: Session) -> None:
    result = run_cli(migrated_url, "create-admin", "b@example.org", "--base-url", "abc.example")
    assert result.returncode == 2
    assert "--base-url" in result.stderr
    assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 0


@pytest.mark.parametrize("email", ["not-an-email", "", "a@b", "x" * 400 + "@example.org"])
def test_invalid_email_exits_non_zero(migrated_url: str, db: Session, email: str) -> None:
    result = run_cli(migrated_url, "create-admin", email)
    assert result.returncode == 2
    assert "Invalid email address" in result.stderr
    assert result.stdout == ""
    assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 0


def test_existing_active_admin_gets_a_reset_link(migrated_url: str, db: Session) -> None:
    db.add(User(email="boss@example.org", name="Boss", password_hash="h", role=Role.admin))
    db.commit()
    result = run_cli(migrated_url, "create-admin", "Boss@example.org")
    assert result.returncode == 0, result.stderr
    assert "already an active admin" in result.stdout
    kind, token = links(result.stdout)[0]
    assert kind == "reset"
    invite = service.lookup_invite(db, token)
    assert invite is not None
    assert invite.kind is InviteKind.reset
    assert invite.expires_at - invite.created_at == service.RESET_TTL


@pytest.mark.parametrize(("role", "active"), [(Role.member, True), (Role.admin, False)])
def test_existing_member_or_disabled_is_refused(
    migrated_url: str, db: Session, role: Role, active: bool
) -> None:
    db.add(User(email="m@example.org", name="M", password_hash="h", role=role, is_active=active))
    db.commit()
    result = run_cli(migrated_url, "create-admin", "m@example.org")
    assert result.returncode == 1
    assert "not an active admin" in result.stderr
    assert links(result.stdout + result.stderr) == []
    assert db.scalar(sa.select(sa.func.count()).select_from(Invite)) == 0


def test_unreachable_database_is_a_clean_error() -> None:
    result = run_cli(
        "postgresql+psycopg://u:secretpw@127.0.0.1:1/x", "create-admin", "a@example.org"
    )
    assert result.returncode == 1
    assert "Database error" in result.stderr
    assert "Traceback" not in result.stderr
    assert "secretpw" not in result.stderr


def test_missing_database_url_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["create-admin", "a@example.org"]) == 2


def test_usage_errors() -> None:
    assert run_cli("postgresql+psycopg://u:p@h/d").returncode == 2
    assert run_cli("postgresql+psycopg://u:p@h/d", "create-admin").returncode == 2
    assert run_cli("postgresql+psycopg://u:p@h/d", "frobnicate").returncode == 2


def test_in_process_main_for_coverage(
    migrated_url: str, db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["create-admin", "inproc@example.org"]) == 0
    token = links(capsys.readouterr().out)[0][1]
    assert security.is_well_formed_token(token)
    assert service.lookup_invite(db, token) is not None


def test_in_process_branches(
    migrated_url: str, db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same paths as the subprocess tests, in process so coverage sees them."""
    assert main(["create-admin", "nope"]) == 2
    assert main(["create-admin", "a@example.org", "--base-url", "ftp://x"]) == 2
    assert main(["create-admin", "a@example.org", "--base-url", "http://h:8080"]) == 0
    assert "  http://h:8080/invite/" in capsys.readouterr().out
    db.add(User(email="adm@example.org", name="A", password_hash="h", role=Role.admin))
    db.add(User(email="mem@example.org", name="M", password_hash="h"))
    db.commit()
    assert main(["create-admin", "adm@example.org"]) == 0
    assert links(capsys.readouterr().out)[0][0] == "reset"
    assert main(["create-admin", "mem@example.org"]) == 1
    assert "not an active admin" in capsys.readouterr().err


def test_in_process_database_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:secretpw@127.0.0.1:1/x")
    assert main(["create-admin", "a@example.org"]) == 1
    err = capsys.readouterr().err
    assert "Database error" in err
    assert "secretpw" not in err
