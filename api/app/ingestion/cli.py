"""Ingestion commands for the operator, run inside the api container:

    python -m app.ingestion.cli close-run <run-id> [--dry-run]

close-run closes an ingestion run that cannot move (its `advance` keeps failing, or a route
failed after recording a request) the way the cron does after `engine.ADVANCE_FAILURES` failures
in a row: `failed` with the `engine.STUCK` sentence (or `cancelled` when the user's Cancel was
asked), its planned steps cancelled, the agent run of its running step asked to stop, the steps
that ended `done` kept for the user's next run. It prints the status the run ends on. One
transaction, under the run's row lock. A run that has already ended is left as it is, so running
the command again changes nothing. --dry-run prints what the close would do and changes nothing.
It prints ids, statuses and counts only (never a document, an item or an e-mail address) and logs
`ingestion_run_closed_by_hand`. `./deploy.sh ingestion close-run <run-id> [--dry-run]` runs it on
the Jetson.

Exit codes: 0 closed, already ended, or a dry run; 1 no such run or a database error; 2 invalid
arguments or configuration.
"""

import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity_service
from app.ingestion import engine
from app.logging_config import configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.ingestion.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    close = commands.add_parser("close-run", help="close an ingestion run that cannot move")
    close.add_argument("run_id", type=uuid.UUID)
    close.add_argument(
        "--dry-run", action="store_true", help="print what it would do, change nothing"
    )
    return parser


def _steps(count: int) -> str:
    return f"{count} planned step{'' if count == 1 else 's'}"


def close_run(run_id: uuid.UUID, dry_run: bool) -> int:
    settings = get_settings()
    db_engine = build_engine(settings)
    try:
        with build_sessionmaker(db_engine)() as session, session.begin():
            found = engine.close_by_hand(session, run_id, identity_service.utcnow(), dry_run)
    except SQLAlchemyError as exc:
        print(f"Database error, nothing was changed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        db_engine.dispose()
    if found is None:
        print(f"No ingestion run {run_id}. Nothing was changed.", file=sys.stderr)
        return 1
    if found.status not in engine.ACTIVE:
        print(f"Run {run_id} has already ended ({found.status.value}). Nothing was changed.")
        return 0
    stop = ""
    if found.agent_run_id is not None:
        stop = f", the agent run {found.agent_run_id} of its running step asked to stop"
    if not found.closed:
        print(
            f"Dry run: run {run_id} is {found.status.value}. Closing it would mark it "
            f"{found.ends.value}, with {_steps(found.planned)} cancelled{stop}. "
            "Nothing was changed."
        )
        return 0
    print(f"Run {run_id} closed: {found.ends.value}, {_steps(found.planned)} cancelled{stop}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "close-run":
            return close_run(args.run_id, args.dry_run)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests call main()
    configure_logging(get_settings().log_level)
    sys.exit(main())
