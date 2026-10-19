"""Documents commands for the operator.

    python -m app.documents.cli import-legacy <email> [--dry-run]

import-legacy turns the command line's `cv.json` and `letter.json` of that account's postings,
attached by the jobs import (`./deploy.sh jobs import <email>` first), into the postings' CV and
letter: version 1 with origin legacy and the template classic; a sent posting's PDF kept and
frozen byte for byte (legacy.py). In one transaction, and idempotent: a second run finds every
document made and changes nothing. `--dry-run` does the same work, prints the same counts and
rolls everything back. It runs inside the api container:

    docker compose exec -T api python -m app.documents.cli import-legacy <email> --dry-run

`./deploy.sh documents import-legacy <email> [--dry-run]` runs it on the Jetson.

Output: counts only (never a document's text, a file name of the person, nor the email). Exit
codes: 0 done, 2 invalid arguments, 1 no such account, a lock not free in time or a database
error (nothing imported).
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.documents import legacy
from app.identity import service as identity_service
from app.jobs.service import JobBusyError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.documents.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser(
        "import-legacy", help="import the command line's CVs and letters of the account's postings"
    )
    imp.add_argument("email", help="the account whose postings hold the legacy files")
    imp.add_argument(
        "--dry-run", action="store_true", help="count what would be imported, write nothing"
    )
    return parser


def _error(message: str) -> None:
    print(message, file=sys.stderr)


def report(summary: legacy.Summary, dry_run: bool) -> str:
    """The counts, in the words a person reads."""
    s = summary
    head = "Dry run, nothing was written. Would import" if dry_run else "Imported"
    return "\n".join(
        (
            f"{head} {s.cvs} CVs and {s.letters} letters from {s.postings} postings with "
            "legacy files.",
            f"Versions: {s.versions}, of which {s.frozen} frozen as sent with their PDF.",
            f"Drafts with fields left out (legacy_invalid): {s.invalid}.",
            f"JSON without its PDF: {s.json_without_pdf}. PDF without its JSON, not imported: "
            f"{s.pdf_without_json}.",
            f"Already there, left as they are: {s.existing}.",
            f"Roles moved to notes: {s.roles}. Employer keys not in the profile: "
            f"{s.unknown_employers}.",
        )
    )


def run_import_legacy(email: str, dry_run: bool) -> int:
    try:
        email = identity_service.normalize_email(email)
    except identity_service.InvalidEmailError:
        _error("Invalid email address. Nothing was imported.")
        return 2
    settings = get_settings()
    engine = build_engine(settings)
    try:
        with build_sessionmaker(engine)() as session, session.begin():
            user = identity_service.get_user_by_email(session, email)
            if user is None:
                _error("No account with that email. Nothing was imported.")
                return 1
            summary = legacy.import_legacy(session, user.id, identity_service.utcnow())
            if dry_run:
                session.rollback()
    except JobBusyError:
        _error("The account's postings or documents are being changed. Nothing was imported.")
        return 1
    except SQLAlchemyError as exc:
        _error(f"Database error, nothing was imported: {type(exc).__name__}")
        return 1
    finally:
        engine.dispose()
    print(report(summary, dry_run))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "import-legacy":
            return run_import_legacy(args.email, args.dry_run)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests run it as a subprocess
    sys.exit(main())
