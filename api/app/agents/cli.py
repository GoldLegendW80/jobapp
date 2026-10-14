"""Agents commands for the operator, run inside the api container:

    python -m app.agents.cli rotate

rotate re-encrypts with CREDENTIALS_KEY every model credential and every Gmail refresh token
(mail M4) written by another key (the one
in CREDENTIALS_KEY_PREVIOUS during a rotation), in one transaction, and prints how many rows
changed. Afterwards CREDENTIALS_KEY_PREVIOUS can be removed from .env.

Exit codes: 0 done, 2 configuration error (no CREDENTIALS_KEY), 1 a row could not be decrypted
(written by a key this server does not hold: nothing was changed) or a database error.
Nothing is printed or logged about a secret.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.agents import credentials
from app.agents.crypto import DecryptError
from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.mail import accounts as mail_accounts
from app.main import credentials_keyring


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.agents.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rotate", help="re-encrypt every model credential with CREDENTIALS_KEY")
    return parser


def rotate() -> int:
    settings = get_settings()
    keyring = credentials_keyring(settings)
    if keyring is None:
        print("CREDENTIALS_KEY is not set. Nothing was changed.", file=sys.stderr)
        return 2
    engine = build_engine(settings)
    try:
        with build_sessionmaker(engine)() as session, session.begin():
            changed = credentials.rotate_all(session, keyring)
            changed += mail_accounts.rotate_all(session, keyring)
    except DecryptError:
        print(
            "A credential was written by a key this server does not hold (set it as "
            "CREDENTIALS_KEY_PREVIOUS). Nothing was changed.",
            file=sys.stderr,
        )
        return 1
    except SQLAlchemyError as exc:
        print(f"Database error, nothing was changed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"{changed} credential{'' if changed == 1 else 's'} re-encrypted with CREDENTIALS_KEY.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "rotate":
            return rotate()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests call main()
    sys.exit(main())
