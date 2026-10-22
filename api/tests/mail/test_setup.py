"""First-setup file ownership and Redis isolation, using temporary invented secrets only."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("mail_setup", ROOT / "ops/mail-setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


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
