"""Identity commands for the operator, run inside the api container:

    python -m app.identity.cli create-admin <email> [--base-url https://host]

create-admin prints a one-time setup link. Accepting it (page /invite/<token>) creates the
account with the admin role. Running it again for the same email revokes the previous unused
link. When an active admin with that email already exists, it prints a password reset link
(/reset/<token>, valid 1 hour) instead, so a locked-out admin can always get back in from the
Jetson. When the email belongs to a member or a disabled account, it refuses: changing someone's
role is done from the admin area, never by a link sent to that person.

Exit codes: 0 link printed, 2 invalid arguments or email, 1 refused or database error.
The token goes to stdout only, for the operator; it is never logged.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service
from app.identity.models import InviteKind, Role


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.identity.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-admin", help="print a one-time setup link that makes this email an admin"
    )
    create.add_argument("email")
    create.add_argument(
        "--base-url",
        default="",
        help="public URL of the app (for example https://abc.trycloudflare.com) to print a "
        "full link instead of a path",
    )
    return parser


def create_admin(email: str, base_url: str = "") -> int:
    if base_url and not base_url.startswith(("https://", "http://")):
        print(
            "--base-url must start with https:// or http://. Nothing was created.", file=sys.stderr
        )
        return 2
    try:
        email = service.normalize_email(email)
    except service.InvalidEmailError:
        print("Invalid email address. Nothing was created.", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = build_engine(settings)
    try:
        with build_sessionmaker(engine)() as session, session.begin():
            user = service.get_user_by_email(session, email)
            if user is None:
                issued = service.create_invite(session, email, InviteKind.invite, role=Role.admin)
                headline = f"Setup link for {email}, valid 7 days, single use:"
            elif user.role is Role.admin and user.is_active:
                issued = service.create_invite(session, email, InviteKind.reset)
                headline = (
                    f"{email} is already an active admin. "
                    "Password reset link, valid 1 hour, single use:"
                )
            else:
                print(
                    f"{email} already has an account that is not an active admin. "
                    "Change its role or status from the admin area instead. Nothing was created.",
                    file=sys.stderr,
                )
                return 1
    except SQLAlchemyError as exc:
        print(f"Database error, nothing was created: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(headline)
    if base_url:
        print(f"  {base_url.rstrip('/')}{issued.path}")
    else:
        print(f"  {issued.path}")
        print("Prefix the path with the public URL of the app, for example https://<host>.")
    print("Any earlier unused link of this kind for this email no longer works.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create-admin":
            return create_admin(args.email, args.base_url)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests run it as a subprocess
    sys.exit(main())
