"""The check of a saved Claude subscription token: one Claude Code run in the worker (owner,
2026-11-07, replacing the A1 choice; SPEC-agents.md, Credentials). Only the worker image holds the
CLI, so POST /api/me/model-credential/verify queues `JOB` (`queue_check`) and the worker runs
`execute`: one turn of MODEL, no tool, with the environment and scratch directory of an agent run.

Where a check stands lives in Redis only, one key per user (`Store`): the credential's
`updated_at` it belongs to, its state (checking, then verified, invalid, busy or unavailable) and
a fixed sentence. Never the secret, never a text of the model or of the CLI. The API reads it
back as `CredentialOut.check` (`current`).
"""

import asyncio
import contextlib
import inspect
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError

from app import queue as queues
from app.agents import credentials
from app.agents.crypto import DecryptError
from app.agents.models import CredentialKind
from app.agents.schemas import CheckOut, CheckState
from app.logging_config import hidden

if TYPE_CHECKING:
    from app.agents.runner import RunContext

logger = logging.getLogger(__name__)

JOB = "check_model_token"
KEY = "jobapp:agents:token-check:"
TTL_S = 24 * 3600
# arq's bound of the job; the Claude Code run inside it ends first, so the job still records why.
JOB_TIMEOUT_S = 90
RUN_TIMEOUT_S = 60
# How long a check counts as on its way: queued behind the agent runs, then running.
QUEUED_FOR_S = 15 * 60
RUNNING_FOR_S = JOB_TIMEOUT_S + 30
MODEL = "claude-haiku-5-5"
PROMPT = "Answer with the single word OK."
# The recorded stream the fake backend replays for the check (app/agents/fake_streams/).
SCRIPT = "token_check"
KIND = CredentialKind.claude_subscription

CHECKING = "Checking the token with Claude Code."
VERIFIED = "Claude Code answered with this token."
INVALID = (
    "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one."
)
BUSY = "Anthropic is busy or rate limiting this token. Try again later."
NO_USAGE = "This subscription has reached its usage limit. Try again once it resets."
UNAVAILABLE = "Anthropic could not be reached. Try again later."

# Replaces the value only if it is still the one read ('' for no key); an empty new value
# removes it. ARGV: the value read, the new value, the TTL.
_SWAP = """
local current = redis.call('GET', KEYS[1]) or ''
if current ~= ARGV[1] then return 0 end
if ARGV[2] == '' then
  redis.call('DEL', KEYS[1])
else
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
end
return 1
"""
_SWAP_TRIES = 5


class StoreUnavailableError(Exception):
    """Redis could not be reached or refused the command."""


def stamp(updated_at: datetime) -> str:
    return updated_at.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class Check:
    """The Redis value. `attempt` and `until` (epoch seconds) only while checking."""

    updated_at: str
    state: CheckState
    message: str
    attempt: str | None = None
    until: float | None = None

    def dumps(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def loads(cls, raw: str) -> "Check | None":
        try:
            data = json.loads(raw)
            return cls(
                updated_at=str(data["updated_at"]),
                state=data["state"],
                message=str(data["message"]),
                attempt=data.get("attempt"),
                until=data.get("until"),
            )
        except (ValueError, KeyError, TypeError):
            return None

    def on_its_way(self, now_s: float) -> bool:
        return self.state == "checking" and self.until is not None and now_s < self.until


def current(
    check: Check | None, updated_at: datetime, now_s: float | None = None
) -> CheckOut | None:
    """What the API answers for the credential saved at `updated_at`: None for another credential's
    check, and for a check lost on its way (its worker died, its job was dropped)."""
    now_s = time.time() if now_s is None else now_s
    if check is None or check.updated_at != stamp(updated_at):
        return None
    if check.state == "checking" and not check.on_its_way(now_s):
        return None
    return CheckOut(state=check.state, message=check.message)


def _text(raw: Any) -> str:
    if raw is None:
        return ""
    return raw.decode() if isinstance(raw, bytes) else str(raw)


class Store:
    """`pool` returns the Redis client: the api's lazily made one (app.queue.Queue.pool), or the
    worker's arq pool."""

    def __init__(self, pool: Callable[[], Any]) -> None:
        self._pool = pool

    async def _client(self) -> Any:
        found = self._pool()
        return await found if inspect.isawaitable(found) else found

    @staticmethod
    def key(user_id: uuid.UUID) -> str:
        return f"{KEY}{user_id}"

    async def read(self, user_id: uuid.UUID) -> Check | None:
        try:
            raw = _text(await (await self._client()).get(self.key(user_id)))
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        return Check.loads(raw) if raw else None

    async def _swap(self, user_id: uuid.UUID, change: Callable[[Check | None], str | None]) -> bool:
        """`change` answers the new value for the one read ('' removes it, None leaves it);
        written only if nobody wrote in between, else read again. True when written."""
        key = self.key(user_id)
        try:
            client = await self._client()
            for _ in range(_SWAP_TRIES):
                raw = _text(await client.get(key))
                fresh = change(Check.loads(raw) if raw else None)
                if fresh is None:
                    return False
                if await client.eval(_SWAP, 1, key, raw, fresh, TTL_S):
                    return True
        except (RedisError, OSError, TimeoutError):
            raise StoreUnavailableError from None
        return False

    async def start(self, user_id: uuid.UUID, updated_at: datetime, now_s: float) -> str | None:
        """A new attempt id, the check now `checking`; None when one is on its way already."""
        attempt = uuid.uuid4().hex
        at = stamp(updated_at)

        def change(found: Check | None) -> str | None:
            if found is not None and found.updated_at == at and found.on_its_way(now_s):
                return None
            return Check(at, "checking", CHECKING, attempt, now_s + QUEUED_FOR_S).dumps()

        return attempt if await self._swap(user_id, change) else None

    async def running(self, user_id: uuid.UUID, attempt: str, now_s: float) -> bool:
        """The attempt has started: on its way for RUNNING_FOR_S more. False when it is no longer
        the user's current one."""

        def change(found: Check | None) -> str | None:
            if found is None or found.attempt != attempt:
                return None
            return replace(found, until=now_s + RUNNING_FOR_S).dumps()

        return await self._swap(user_id, change)

    async def finish(
        self, user_id: uuid.UUID, attempt: str, state: CheckState, message: str
    ) -> bool:
        def change(found: Check | None) -> str | None:
            if found is None or found.attempt != attempt:
                return None
            return Check(found.updated_at, state, message).dumps()

        return await self._swap(user_id, change)

    async def drop(self, user_id: uuid.UUID, attempt: str) -> bool:
        def change(found: Check | None) -> str | None:
            return "" if found is not None and found.attempt == attempt else None

        return await self._swap(user_id, change)


async def queue_check(
    store: Store,
    queue: queues.Queue,
    user_id: uuid.UUID,
    updated_at: datetime,
    now_s: float | None = None,
) -> bool:
    """Queue the check of the token saved at `updated_at` on the agents queue; False when one is
    queued or running already. Raises StoreUnavailableError or queues.QueueUnavailableError."""
    now_s = time.time() if now_s is None else now_s
    attempt = await store.start(user_id, updated_at, now_s)
    if attempt is None:
        return False
    try:
        await queue.enqueue(
            JOB,
            str(user_id),
            stamp(updated_at),
            attempt,
            queue=queues.AGENTS,
            job_id=queues.token_check_job_id(attempt),
        )
    except queues.QueueUnavailableError:
        with contextlib.suppress(StoreUnavailableError):
            await store.drop(user_id, attempt)
        raise
    return True


def outcome_of(result: Any, errors: Collection[str]) -> tuple[CheckState, str]:
    """The state a Claude Code run gives the token, by the rule of runner.outcome_of: no credit,
    the credential refused, Anthropic busy, anything else. `result` is the run's ResultMessage
    (None when it never came), `errors` the marks of its assistant messages."""
    if result is None:
        return "unavailable", UNAVAILABLE
    # A turn limit means the model answered: the token works.
    if not result.is_error or result.subtype == "error_max_turns":
        return "verified", VERIFIED
    status = result.api_error_status
    if "billing_error" in errors:
        return "busy", NO_USAGE
    if "authentication_failed" in errors or status in (401, 403):
        return "invalid", INVALID
    if "rate_limit" in errors or status in (429, 503, 529):
        return "busy", BUSY
    return "unavailable", UNAVAILABLE


def _reveal(context: "RunContext", user_id: uuid.UUID, checked_at: datetime) -> str | None:
    """The token as it was when the check was queued, or None: replaced, removed, unreadable."""
    if context.keyring is None:
        return None
    with context.sessions() as db:
        try:
            revealed = credentials.reveal(db, context.keyring, user_id)
        except DecryptError:
            db.rollback()
            return None
        if (
            revealed is None
            or revealed.row.kind is not KIND
            or revealed.row.updated_at != checked_at
            or not credentials.has_shape(revealed.secret)
        ):
            db.rollback()
            return None
        db.commit()  # keeps a re-encryption with the current key
        return revealed.secret


def _record(
    context: "RunContext", user_id: uuid.UUID, checked_at: datetime, state: CheckState
) -> bool:
    """Write the outcome on the row exactly as checked; False when it was saved again or removed
    meanwhile (nothing written)."""
    with context.sessions() as db:
        if state in ("verified", "invalid"):
            stamp_at = context.clock() if state == "verified" else None
            if not credentials.set_verified_if_unchanged(db, user_id, KIND, checked_at, stamp_at):
                db.rollback()
                return False
            db.commit()
            return True
        row = credentials.get(db, user_id)
        return row is not None and row.kind is KIND and row.updated_at == checked_at


def _drop_line(_line: str) -> None:
    """Claude Code's stderr: read, so it never reaches the worker's own, and never kept."""


async def _ask(
    context: "RunContext", user_id: uuid.UUID, secret: str, timeout_s: float
) -> tuple[CheckState, str]:
    # Worker only: the Claude Agent SDK is not in the api image.
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        RateLimitEvent,
        ResultMessage,
    )

    from app.agents import runner, sdk_options
    from app.agents import tools as app_tools
    from app.agents.backend import BackendRequest

    async def denied(_name: str) -> None:
        return None

    gate = runner.make_gate(frozenset(), denied)
    result: Any = None
    errors: set[str] = set()
    scratch = await asyncio.to_thread(runner.make_scratch, context.settings, uuid.uuid4())
    try:
        options = ClaudeAgentOptions(
            **sdk_options.fixed(),
            model=MODEL,
            max_turns=1,
            effort="low",
            cwd=str(scratch / "work"),
            env=runner.build_env(KIND, secret, scratch, context.settings.model_api_base_url),
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[gate])]},  # type: ignore[list-item]
            stderr=_drop_line,
        )
        request = BackendRequest(
            prompt=PROMPT,
            options=options,
            tools=app_tools.build(context.sessions, user_id, ()),
            gate=gate,
            script=SCRIPT,
        )
        backend = context.backend_factory()
        async with asyncio.timeout(timeout_s):
            # aclosing: a timeout stops the Claude Code process before the scratch goes.
            async with contextlib.aclosing(backend.stream(request)) as messages:
                async for message in messages:
                    if isinstance(message, ResultMessage):
                        result = message
                    elif isinstance(message, AssistantMessage) and message.error:
                        errors.add(str(message.error))
                    elif (
                        isinstance(message, RateLimitEvent)
                        and message.rate_limit_info.status == "rejected"
                    ):
                        errors.add("rate_limit")
    except Exception as exc:
        # By type only: the message of an SDK error can quote the CLI's output.
        logger.warning(
            "token check run failed", extra={"user_id": str(user_id), "error": type(exc).__name__}
        )
        return "unavailable", UNAVAILABLE
    finally:
        await asyncio.to_thread(shutil.rmtree, scratch, True)
    return outcome_of(result, errors)


async def execute(
    context: "RunContext",
    store: Store,
    user_id: uuid.UUID,
    checked_at: datetime,
    attempt: str,
    *,
    timeout_s: float = RUN_TIMEOUT_S,
) -> CheckState | None:
    """The job: one Claude Code run with the token saved at `checked_at`; the outcome goes on the
    row (verified_at set or cleared) and into Redis. None when there was nothing to record:
    another attempt took this one's place, or the token was saved again, removed or unreadable."""
    log = {"user_id": str(user_id)}
    if not await store.running(user_id, attempt, time.time()):
        logger.info("token check skipped: no longer the current one", extra=log)
        return None
    secret = await asyncio.to_thread(_reveal, context, user_id, checked_at)
    if secret is None:
        await store.drop(user_id, attempt)
        logger.info("token check skipped: the credential changed", extra=log)
        return None
    # Out of every log line of the process while the check holds it.
    with hidden(secret):
        state, message = await _ask(context, user_id, secret, timeout_s)
    del secret
    if not await asyncio.to_thread(_record, context, user_id, checked_at, state):
        await store.drop(user_id, attempt)
        logger.info("token check not recorded: the credential changed", extra=log)
        return None
    await store.finish(user_id, attempt, state, message)
    logger.info("subscription token checked", extra={**log, "state": state})
    return state
