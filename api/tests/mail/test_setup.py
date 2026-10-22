"""First-setup file ownership and Redis isolation, using temporary invented secrets only."""

import hashlib
import importlib.util
import os
import stat
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("mail_setup", ROOT / "ops/mail-setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

FILES = {
    ".env.mailsend": (0o600, 42),
    ".env.redis": (0o600, 42),
    ".secrets/gmailmcp-redis-password": (0o600, 10001),
    ".secrets/redis-default-password": (0o600, 999),
    ".secrets/redis.acl": (0o444, 0),
}


@pytest.fixture
def operator_setup(tmp_path, monkeypatch):
    owners = []
    tokens = Mock(side_effect=["test-sign", "test-default", "test-provider"])
    monkeypatch.setattr(
        "sys.argv", ["mail-setup", "--directory", str(tmp_path), "--owner-uid", "42"]
    )
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr(setup.os, "fchown", lambda fd, uid, gid: owners.append((uid, gid)))
    monkeypatch.setattr(setup.secrets, "token_urlsafe", tokens)
    return owners, tokens


@pytest.mark.parametrize("umask", [0o000, 0o022, 0o027, 0o077])
def test_new_file_and_directory_permissions_ignore_umask(umask, tmp_path, operator_setup, capsys):
    previous = os.umask(umask)
    try:
        setup.main()
    finally:
        os.umask(previous)

    owners, _ = operator_setup
    assert owners == [(uid, -1) for _, uid in FILES.values()]
    assert stat.S_IMODE((tmp_path / ".secrets").stat().st_mode) == 0o755
    for name, (mode, _) in FILES.items():
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == mode
    assert (tmp_path / ".env.mailsend").read_text() == "MAIL_SEND_KEY=test-sign\n"
    assert (tmp_path / ".secrets/gmailmcp-redis-password").read_text() == "test-provider\n"
    assert (tmp_path / ".secrets/redis-default-password").read_text() == "test-default\n"
    output = capsys.readouterr()
    for value in ("test-sign", "test-default", "test-provider"):
        assert value not in output.out + output.err


@pytest.mark.parametrize("mode", [0o700, 0o750, 0o710, 0o701, 0o770, 0o777])
def test_incompatible_existing_directory_is_refused_before_writing(
    mode, tmp_path, operator_setup, capsys
):
    directory = tmp_path / ".secrets"
    directory.mkdir()
    marker = directory / "unrelated"
    marker.write_text("invented-existing-value")
    directory.chmod(mode)

    with pytest.raises(SystemExit, match="2"):
        setup.main()

    owners, tokens = operator_setup
    assert not owners
    tokens.assert_not_called()
    assert stat.S_IMODE(directory.stat().st_mode) == mode
    assert marker.read_text() == "invented-existing-value"
    assert not any((tmp_path / name).exists() for name in FILES)
    output = capsys.readouterr()
    assert ".secrets" in output.err
    assert "invented-existing-value" not in output.out + output.err


@pytest.mark.parametrize("mode", [0o711, 0o755])
def test_compatible_existing_directory_keeps_its_permissions(mode, tmp_path, operator_setup):
    directory = tmp_path / ".secrets"
    directory.mkdir()
    directory.chmod(mode)

    setup.main()

    assert stat.S_IMODE(directory.stat().st_mode) == mode
    assert all((tmp_path / name).is_file() for name in FILES)


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_existing_secret_directory_must_be_a_real_directory(kind, tmp_path, operator_setup):
    directory = tmp_path / ".secrets"
    if kind == "file":
        directory.write_text("invented-existing-value")
    else:
        target = tmp_path / "other"
        target.mkdir()
        directory.symlink_to(target, target_is_directory=True)

    with pytest.raises(SystemExit, match="2"):
        setup.main()

    owners, tokens = operator_setup
    assert not owners
    tokens.assert_not_called()
    assert not (tmp_path / ".env.mailsend").exists()
    if kind == "file":
        assert directory.read_text() == "invented-existing-value"
    else:
        assert directory.is_symlink()
        assert not list(target.iterdir())


@pytest.mark.parametrize("name", FILES)
def test_existing_setup_file_is_unchanged(name, tmp_path, operator_setup, capsys):
    path = tmp_path / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("invented-existing-value")
    path.chmod(0o400)

    with pytest.raises(SystemExit, match="2"):
        setup.main()

    owners, tokens = operator_setup
    assert not owners
    tokens.assert_not_called()
    assert path.read_text() == "invented-existing-value"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert not any((tmp_path / other).exists() for other in FILES if other != name)
    output = capsys.readouterr()
    assert "invented-existing-value" not in output.out + output.err


def test_ownership_error_closes_descriptor_and_removes_unwritten_file(
    tmp_path, operator_setup, monkeypatch, capsys
):
    descriptors = []

    def fail_ownership(fd, uid, gid):
        descriptors.append(fd)
        raise OSError("invented ownership failure")

    monkeypatch.setattr(setup.os, "fchown", fail_ownership)

    with pytest.raises(OSError, match="invented ownership failure"):
        setup.main()

    try:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptors[0])
    finally:
        with suppress(OSError):
            os.close(descriptors[0])
    assert not any((tmp_path / name).exists() for name in FILES)
    output = capsys.readouterr()
    assert "test-sign" not in output.out + output.err


class SetupTest(unittest.TestCase):
    def test_new_files_have_distinct_capabilities_and_correct_runtime_owners(self):
        with tempfile.TemporaryDirectory() as folder:
            owners = []
            with (
                patch("sys.argv", ["mail-setup", "--directory", folder, "--owner-uid", "42"]),
                patch.object(setup.os, "geteuid", return_value=0),
                patch.object(
                    setup.os, "fchown", side_effect=lambda fd, uid, gid: owners.append(uid)
                ),
                patch.object(
                    setup.secrets,
                    "token_urlsafe",
                    side_effect=["test-sign", "test-default", "test-provider"],
                ),
            ):
                setup.main()
            root = Path(folder)
            assert owners == [42, 42, 10001, 999, 0]
            acl = (root / ".secrets/redis.acl").read_text()
            assert hashlib.sha256(b"test-default").hexdigest() in acl
            assert hashlib.sha256(b"test-provider").hexdigest() in acl
            assert "~mailmcp:send:* -@all +set" in acl
            assert "test-provider" not in acl
            assert (root / ".env.mailsend").stat().st_mode & 0o777 == 0o600
            assert (root / ".secrets/redis.acl").stat().st_mode & 0o777 == 0o444
            before = (root / ".env.mailsend").read_bytes()
            with (
                patch("sys.argv", ["mail-setup", "--directory", folder, "--owner-uid", "42"]),
                patch.object(setup.os, "geteuid", return_value=0),
                pytest.raises(SystemExit, match="2"),
            ):
                setup.main()
            assert (root / ".env.mailsend").read_bytes() == before


if __name__ == "__main__":
    unittest.main()
