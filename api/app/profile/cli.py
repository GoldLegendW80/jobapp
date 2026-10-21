"""Profile commands for the operator.

    python -m app.profile.cli convert --source-dir <job folder> --out <file.json | ->
    python -m app.profile.cli import --email <email> --file <file.json | ->

convert reads the engine's files below the job folder (profile/facts.md, profile/skills.json,
engine/boards.json, engine/assemble.py, engine/photo.b64; see importer.py) and writes the profile
export document. It needs no database, so it runs on the Mac:

    uv run --native-tls --directory api python -m app.profile.cli convert \\
        --source-dir /Users/<me>/Desktop/job --out /tmp/profile.json

(`--directory api` makes api/ the working directory: give absolute paths, or paths relative
to api/.)

import loads an export document (from convert, or downloaded from GET /api/profile/export) into
the profile of the account with that email, exactly as POST /api/profile/import does: every
section validated, all seven replaced in one transaction with one revision each, the photo too when
the file has one (re-encoded without metadata, see photo.py). It runs inside the api container,
reading the file from stdin:

    docker compose exec -T api python -m app.profile.cli import --email <email> --file - \\
        < /tmp/profile.json

Output: one summary line (revision, completeness) and the required fields still missing per
section; never the profile contents. Exit codes: 0 done, 2 invalid arguments or file, 1 no such
account or a database error.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity_service
from app.profile import photo as photos
from app.profile import service
from app.profile.importer import ConversionError, SourcePaths, convert
from app.profile.schemas import (
    IMPORT_MAX_BYTES,
    DocumentError,
    completeness,
    describe_validation_error,
    parse_document,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.profile.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    conv = commands.add_parser(
        "convert", help="convert the engine's profile files into a profile export document"
    )
    conv.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="the job folder that holds profile/ and engine/",
    )
    conv.add_argument("--out", required=True, help="file to write, or - for stdout")
    imp = commands.add_parser("import", help="replace a user's profile with an export document")
    imp.add_argument("--email", required=True, help="the account whose profile is replaced")
    imp.add_argument("--file", required=True, help="the export document, or - for stdin")
    return parser


def _error(message: str) -> None:
    print(message, file=sys.stderr)


def run_convert(source_dir: Path, out: str) -> int:
    if not source_dir.is_dir():
        _error(f"{source_dir} is not a folder. Nothing was written.")
        return 2
    try:
        document = convert(SourcePaths.under(source_dir))
    except ConversionError as exc:
        _error(f"Cannot convert: {exc}. Nothing was written.")
        return 2
    except ValidationError as exc:
        _error(f"The converted profile breaks a rule: {describe_validation_error(exc)}.")
        return 2
    text = document.to_json()
    if out == "-":
        sys.stdout.write(text)
        return 0
    Path(out).write_text(text, encoding="utf-8")
    sections = document.sections
    print(
        f"Wrote {out}: {len(sections.facts.blocks)} fact blocks, "
        f"{len(sections.experience.employers)} employers, "
        f"{len(sections.experience.education)} schools, "
        f"{len(sections.search.boards)} boards, "
        f"photo {'included' if document.photo else 'absent'}."
    )
    return 0


def _read(file: str) -> bytes:
    """At most one byte over the limit, so a huge file is refused without reading it whole."""
    if file == "-":
        return sys.stdin.buffer.read(IMPORT_MAX_BYTES + 1)
    with open(file, "rb") as handle:
        return handle.read(IMPORT_MAX_BYTES + 1)


def run_import(email: str, file: str) -> int:
    try:
        email = identity_service.normalize_email(email)
    except identity_service.InvalidEmailError:
        _error("Invalid email address. Nothing was imported.")
        return 2
    try:
        raw = _read(file)
    except OSError as exc:
        _error(f"Cannot read {file}: {exc.strerror or exc}. Nothing was imported.")
        return 2
    try:
        document = parse_document(raw)
    except DocumentError as exc:
        _error(f"{exc.message} Nothing was imported.")
        return 2
    except ValidationError as exc:
        _error(f"Invalid profile, {describe_validation_error(exc)}. Nothing was imported.")
        return 2
    photo = None
    if document.photo is not None:
        try:
            photo = photos.normalize(document.photo.image())
        except photos.PhotoError as exc:
            _error(f"Invalid profile, photo: {exc.message} Nothing was imported.")
            return 2

    settings = get_settings()
    engine = build_engine(settings)
    try:
        with build_sessionmaker(engine)() as session, session.begin():
            user = identity_service.get_user_by_email(session, email)
            if user is None:
                _error(f"No account with the email {email}. Nothing was imported.")
                return 1
            profile = service.import_profile(
                session,
                user.id,
                document.stored_sections(),
                photo,
                None,  # the operator's import is applied after any save in progress
                identity_service.utcnow(),
            )
            revision = profile.revision
            score = completeness(dict(profile.sections))
    except SQLAlchemyError as exc:
        _error(f"Database error, nothing was imported: {type(exc).__name__}")
        return 1
    finally:
        engine.dispose()

    print(
        f"Imported the profile of {email}: revision {revision}, "
        f"{score.overall}% complete, photo {'replaced' if photo else 'unchanged'}."
    )
    for name, section in score.sections.items():
        if section.missing:
            print(f"  {name}: {section.percent}%, missing {', '.join(section.missing)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "convert":
            return run_convert(args.source_dir, args.out)
        if args.command == "import":
            return run_import(args.email, args.file)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests run it as a subprocess
    sys.exit(main())
