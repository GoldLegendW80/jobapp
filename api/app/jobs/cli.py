"""Jobs commands for the operator.

    python -m app.jobs.cli convert --source-dir <data folder> --out <file.json | ->
    python -m app.jobs.cli import --email <email> --file <file.json | ->

convert reads the engine's data folder (jobs.csv and jobs/<id>/, see importer.py) and writes
the jobs import document. It needs no database, so it runs on the Mac:

    uv run --native-tls --directory api python -m app.jobs.cli convert \\
        --source-dir /Users/<me>/Desktop/job/data --out /tmp/jobs.json

import loads a document into the jobs of the account with that email, exactly as POST
/api/jobs/import does (service.import_jobs: idempotent, files of applied rows never replaced),
in one transaction. It runs inside the api container, reading the file from stdin, and takes
documents up to IMPORT_CLI_MAX_BYTES (64 MiB), past Caddy's 10 MiB body limit:

    docker compose exec -T api python -m app.jobs.cli import --email <email> --file - \\
        < /tmp/jobs.json

`./deploy.sh jobs import <email>` does both, the import on the Jetson.

Output: counts and warnings (slugs and file names, never posting texts, notes or file
contents, never the email). Exit codes: 0 done, 2 invalid arguments or file, 1 no such account
or a database error.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity_service
from app.jobs import service
from app.jobs.importer import ConversionError, convert
from app.jobs.schemas import (
    IMPORT_CLI_MAX_BYTES,
    DocumentError,
    describe_validation_error,
    parse_document,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    conv = commands.add_parser("convert", help="convert the engine's data folder into a document")
    conv.add_argument(
        "--source-dir", required=True, type=Path, help="the data folder that holds jobs.csv"
    )
    conv.add_argument("--out", required=True, help="file to write, or - for stdout")
    imp = commands.add_parser("import", help="load a jobs import document into a user's jobs")
    imp.add_argument("--email", required=True, help="the account whose jobs receive the rows")
    imp.add_argument("--file", required=True, help="the document, or - for stdin")
    return parser


def _error(message: str) -> None:
    print(message, file=sys.stderr)


def _warnings(warnings: Sequence[str], stream: TextIO | None = None) -> None:
    for warning in warnings:
        print(f"  warning: {warning}", file=stream or sys.stdout)


def run_convert(source_dir: Path, out: str) -> int:
    if not source_dir.is_dir():
        _error(f"{source_dir} is not a folder. Nothing was written.")
        return 2
    try:
        document = convert(source_dir)
    except ConversionError as exc:
        _error(f"Cannot convert: {exc}. Nothing was written.")
        return 2
    except ValidationError as exc:
        _error(f"A converted row breaks a rule: {describe_validation_error(exc)}.")
        return 2
    text = document.to_json()
    statuses = Counter(job.status.value for job in document.jobs)
    files = sum(len(job.files) for job in document.jobs)
    summary = (
        f"Converted {len(document.jobs)} rows "
        f"({', '.join(f'{name} {count}' for name, count in sorted(statuses.items()))}), "
        f"{sum(1 for job in document.jobs if job.posting_text is not None)} posting texts, "
        f"{files} files, {len(document.warnings)} warnings, "
        f"{len(text.encode('utf-8')) / 1_000_000:.1f} MB."
    )
    if out == "-":
        sys.stdout.write(text)
        _error(summary)  # stdout is the document: the summary goes to stderr
        _warnings(document.warnings, sys.stderr)
        return 0
    Path(out).write_text(text, encoding="utf-8")
    print(f"Wrote {out}. {summary}")
    _warnings(document.warnings)
    return 0


def _read(file: str) -> bytes:
    """At most one byte over the limit, so a huge file is refused without reading it whole."""
    if file == "-":
        return sys.stdin.buffer.read(IMPORT_CLI_MAX_BYTES + 1)
    with open(file, "rb") as handle:
        return handle.read(IMPORT_CLI_MAX_BYTES + 1)


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
        document = parse_document(raw, IMPORT_CLI_MAX_BYTES)
    except DocumentError as exc:
        _error(f"{exc.message} Nothing was imported.")
        return 2
    except ValidationError as exc:
        _error(f"Invalid document, {describe_validation_error(exc)}. Nothing was imported.")
        return 2
    del raw

    settings = get_settings()
    engine = build_engine(settings)
    try:
        with build_sessionmaker(engine)() as session, session.begin():
            user = identity_service.get_user_by_email(session, email)
            if user is None:
                _error("No account with that email. Nothing was imported.")
                return 1
            summary = service.import_jobs(
                session, user.id, document.jobs, identity_service.utcnow()
            )
    except SQLAlchemyError as exc:
        _error(f"Database error, nothing was imported: {type(exc).__name__}")
        return 1
    finally:
        engine.dispose()

    print(
        f"Imported {len(document.jobs)} rows: {summary.created} created, "
        f"{summary.updated} updated, {summary.unchanged} unchanged, {summary.skipped} skipped. "
        f"Files: {summary.files_added} added, {summary.files_replaced} replaced, "
        f"{summary.files_unchanged} unchanged, {summary.files_kept} kept as sent. "
        f"Warnings: {len(document.warnings) + len(summary.warnings)}."
    )
    _warnings([*document.warnings, *summary.warnings])
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
