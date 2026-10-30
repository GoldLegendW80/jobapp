"""Agents commands for the operator, run inside the api container:

    python -m app.agents.cli rotate
    python -m app.agents.cli start triage|chat <job-id> --role <role> --credential-kind <kind>

rotate re-encrypts with CREDENTIALS_KEY every model credential, every Gmail refresh token
(mail M4) and every job feed key (Adzuna's, app/jobs/feeds/keys.py) written by another key (the
one in CREDENTIALS_KEY_PREVIOUS during a rotation), in one transaction, and prints how many rows
changed. Afterwards CREDENTIALS_KEY_PREVIOUS can be removed from .env.

Exit codes: 0 done, 2 configuration error (no CREDENTIALS_KEY), 1 a row could not be decrypted
(written by a key this server does not hold: nothing was changed) or a database error.
Nothing is printed or logged about a secret.

start begins one run with no web session, through the functions the routes call
(app/agents/operator.py): a triage of the job, or a chat message on the job's thread. It reads
one JSON object on stdin, {"owner": <the account's e-mail>, "message": <the chat text>}, so
neither appears in a process list, and refuses unless that account has the role and a credential
of the kind named, checked in the transaction that writes. Its last line on stdout (after the JSON
log lines) is one JSON object: `http`, the status the endpoint would answer, then ids and counts
(`thread_id` too for a chat refused after its thread was opened), or the refusal's `detail`, or
the `error_class` of an unexpected error (never its message, which could quote the input). Never
the e-mail, never the message. Exit codes: 0 queued (202), 1 refused or failed, 2 invalid arguments.
"""

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from typing import TextIO

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials, operator
from app.agents.crypto import DecryptError
from app.agents.models import CredentialKind
from app.agents.schemas import MESSAGE_MAX, MessageIn
from app.config import ConfigError, get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity_service
from app.identity.models import Role
from app.jobs.feeds import keys as feed_keys
from app.logging_config import configure_logging
from app.mail import accounts as mail_accounts
from app.main import credentials_keyring

# The JSON on stdin of `start`: an e-mail and a message, far below this.
START_INPUT_MAX = 65_536
NO_ACCOUNT = "No account with that email."
TOO_LONG = f"text: at most {MESSAGE_MAX} characters."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.agents.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rotate", help="re-encrypt every model credential with CREDENTIALS_KEY")
    start_ = commands.add_parser("start", help="start one run of an account, with no web session")
    start_.add_argument("kind", choices=["triage", "chat"])
    start_.add_argument("job_id", type=uuid.UUID)
    start_.add_argument("--role", required=True, type=Role, help="the role the account must have")
    start_.add_argument(
        "--credential-kind",
        required=True,
        type=CredentialKind,
        help="the kind of credential the account must have",
    )
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
            changed += feed_keys.rotate_all(session, keyring)
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


def _read_start(stdin: TextIO) -> tuple[str, str] | None:
    raw = stdin.read(START_INPUT_MAX + 1)
    if len(raw) > START_INPUT_MAX:
        return None
    try:
        given = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(given, dict):
        return None
    owner, message = given.get("owner"), given.get("message", "")
    if not isinstance(owner, str) or not isinstance(message, str):
        return None
    return owner, message


def _begin(
    sessions: sessionmaker[Session],
    kind: str,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    text: str,
    only: operator.Only,
    told: dict[str, object],
) -> uuid.UUID:
    """The run created and committed, not yet queued. What to print about it goes in `told` as
    soon as it is known, so a chat refused after its thread was opened still names the thread."""
    settings = get_settings()
    now = identity_service.utcnow
    if kind == "triage":
        agent, data = operator.prepare("triage", job_id, {}, settings.app_env)
        return operator.create_run(sessions, user_id, agent, job_id, data, now(), only).id
    try:
        body = MessageIn(text=text)
    except ValidationError:
        raise operator.Refused(422, TOO_LONG if text.strip() else operator.EMPTY) from None
    cleaned = operator.clean_text(body.text)
    thread, created = operator.open_thread(sessions, user_id, job_id, now(), only)
    told.update(thread_id=str(thread.id), thread_created=created)
    sent = operator.send_message(sessions, user_id, thread.id, cleaned, now(), only)
    told["message_id"] = sent.message.id
    return sent.run.id


async def _enqueue(sessions: sessionmaker[Session], run_id: uuid.UUID) -> bool:
    queue = queues.Queue(get_settings().redis_url.get_secret_value())
    try:
        return await operator.enqueue(queue, sessions, run_id, identity_service.utcnow)
    finally:
        await queue.close()


def start(kind: str, job_id: uuid.UUID, only: operator.Only, stdin: TextIO) -> int:
    out: dict[str, object] = {}
    given = _read_start(stdin)
    if given is None:
        print(json.dumps({"http": 400, "detail": "stdin: one JSON object with owner and message"}))
        return 2
    owner, text = given
    settings = get_settings()
    engine = build_engine(settings)
    sessions = build_sessionmaker(engine)
    try:
        operator.require_configured(credentials_keyring(settings))
        try:
            email = identity_service.normalize_email(owner)
        except identity_service.InvalidEmailError:
            raise operator.Refused(404, NO_ACCOUNT) from None
        with sessions() as db:
            user = identity_service.get_user_by_email(db, email)
        if user is None:
            raise operator.Refused(404, NO_ACCOUNT)
        run_id = _begin(sessions, kind, user.id, job_id, text, only, out)
        out.update(run_id=str(run_id))
        out.update(enqueued=asyncio.run(_enqueue(sessions, run_id)), http=202)
    except HTTPException as exc:
        out.update(http=exc.status_code, detail=str(exc.detail))
        if isinstance(exc, operator.Busy):
            out.update(active_run_id=str(exc.run_id) if exc.run_id else None)
    except Exception as exc:  # the class name only: a message could quote the input or a statement
        out.update(http=500, error_class=type(exc).__name__)
    finally:
        engine.dispose()
    print(json.dumps(out))
    return 0 if out.get("http") == 202 else 1


def main(argv: Sequence[str] | None = None, stdin: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "rotate":
            return rotate()
        if args.command == "start":
            only = operator.Only(role=args.role, credential_kind=args.credential_kind)
            return start(args.kind, args.job_id, only, stdin or sys.stdin)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2  # pragma: no cover, argparse rejects unknown commands


if __name__ == "__main__":  # pragma: no cover, the tests call main()
    configure_logging(get_settings().log_level)
    sys.exit(main())
