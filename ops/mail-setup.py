#!/usr/bin/env python3
"""Operator-only first setup. Creates new secrets, never reads or prints an existing secret."""

import argparse
import hashlib
import os
import secrets
import stat
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--owner-uid", type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run this first setup with sudo")
    root = args.directory.resolve()
    paths = [
        root / ".env.mailsend",
        root / ".env.redis",
        *(
            root / ".secrets" / name
            for name in (
                "gmailmcp-redis-password",
                "redis-default-password",
                "redis.acl",
            )
        ),
    ]
    if any(path.exists() for path in paths):
        parser.error("setup files already exist; no credential was changed")
    directory = root / ".secrets"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        created = False
    else:
        created = True
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        parser.error(".secrets must be a real directory; no credential was changed")
    try:
        if created:
            os.fchmod(descriptor, 0o755)
        else:
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            if mode & 0o111 != 0o111 or mode & 0o022:
                parser.error(
                    "existing .secrets permissions must allow traversal by all users "
                    "and forbid group or other writes; no credential was changed"
                )
    finally:
        os.close(descriptor)
    signing, default, provider = (secrets.token_urlsafe(48) for _ in range(3))
    values = [
        (f"MAIL_SEND_KEY={signing}\n", args.owner_uid, 0o600),
        (f"REDIS_URL=redis://default:{default}@redis:6379/0\n", args.owner_uid, 0o600),
        (provider + "\n", 10001, 0o600),
        (default + "\n", 999, 0o600),
        (
            "user default on #"
            + hashlib.sha256(default.encode()).hexdigest()
            + " ~* &* +@all\n"
            + "user gmailmcp on #"
            + hashlib.sha256(provider.encode()).hexdigest()
            + " ~mailmcp:send:* -@all +set\n",
            0,
            0o444,
        ),
    ]
    for path, (value, uid, mode) in zip(paths, values, strict=True):
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.fchown(descriptor, uid, -1)
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", closefd=False) as stream:
                stream.write(value)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
    print(
        "Created API-only send authority and isolated Redis credentials. No secret was printed."
    )


if __name__ == "__main__":
    main()
