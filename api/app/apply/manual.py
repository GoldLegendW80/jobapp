"""Apply myself (apply A9; SPEC-apply, "Modes, and who clicks Submit"): the person fills and
sends the form in the live view; the app never clicks Submit in this mode.

The session is the job `apply_manual(application_id)` of the `jobapp:apply` queue, which holds
the lease's token for the session's whole length (one session at a time, like a fill):

1. The plan of A5's `run.prepare(..., manual=True)`: never apply only warned at the start (the
   page says "Your rules exclude this role."), the documents of `documents.current`, and no CV is
   no refusal (the person may still apply). Their versions are written on the application
   (`document_versions`): they are what is placed on the lease, and what is frozen when the
   person records the application.
2. The lease, purpose `apply_manual` (browser's tier 0, 1,800 s of active time, the idle timeout
   of BROWSER_IDLE_S, its debugging port kept): behind another lease the application stays
   `queued` with `needs` = `browser_queue`, and the panel asks again (`open`) to keep its place.
3. `queued` to `filling`, the PDFs placed through browser B5's `PUT /leases/{id}/files/{name}`,
   the form opened. From then on the person holds the input (browserd's live view, A9).
4. The loop: the panel's commands (a Redis list, at most COMMANDS_MAX waiting) and a watch of the
   page every WATCH_S that is not activity for the idle timer (`passive`): the adapter's
   confirmation (or the generic sentences of `ats.base.confirmed`), written as a `confirmation`
   event the first time it shows, a file chooser the person opened, and the seconds left before
   the idle timeout (the panel warns at IDLE_WARN_S).
   - "Attach my CV", "Attach my letter": a file chooser the person opened is answered with that
     document; else the file input whose label the resolver reads as that document receives it;
     none or two of them: nothing is set and the panel asks the person to click the right one
     (the next chooser is answered). Code only, never a model; never a dialog over a folder.
   - "Fill my details": the input goes to the app (browserd's `app_turn`), the adapter types the
     identity fields only (resolver answers whose source is `identity.*`) into fields that are
     still empty, then the input goes back to the person, whatever happened. A Take over during
     the fill gives the person the input at once: browserd refuses the app's next key
     (`person_input`) and the fill stops (`taken`): no further field is typed by the app.
5. The end, on every path (the lease ended: idle, its maximum, the window closed; the job
   cancelled; a worker restart, `reset_interrupted`): an application still `filling` whose page
   showed the site's confirmation is recorded `submitted` by `confirmation` (SPEC-apply's modes
   table: the app records it when it sees the confirmation). Without one it goes back to `queued`
   with `needs` = `session_ended`, still holding the posting: the person opens the form again,
   says "I submitted it" or closes it. An application the person recorded or closed meanwhile is
   left as it is.

`record` is the person's "Record as applied" (offered when the watch saw a confirmation) and "I
submitted it": the one move to `submitted` that skips `submitting` and the gate, for `manual`
applications only (`states.MANUAL_SENT_FROM`); the posting to `applied` through `set_status`
and the placed versions frozen (documents D14), in one transaction under the gate's locks. The
app cannot know whether the person uploaded the placed versions or nothing at all: the event
says `by` (confirmation or person) and how many versions were frozen; the run report says so.
`cancel` (the person's Close) records it too once a confirmation was seen.

Nothing of a value, a page or a token goes into a log, an event, the job's arguments or the
status key: codes, counts and booleans only.
"""

import asyncio
import contextlib
import json
import logging
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import gate, guardrails, run, states
from app.apply.answers import Answer, resolve
from app.apply.ats import adapter_for
from app.apply.ats import base as ats_base
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.jobs import service as jobs_service
from app.jobs.models import JobStatus

logger = logging.getLogger(__name__)

PURPOSE = "apply_manual"
KEY = "jobapp:apply:manual:"
COMMANDS = ("attach_cv", "attach_letter", "fill_details")
COMMANDS_MAX = 8
STATUS_TTL_S = 120
POLL_S = 1.0  # how long one wait for a command lasts
WATCH_S = 3.0  # the page is read this often
IDLE_WARN_S = 60  # the panel warns this long before the idle timeout (at 4 of 5 minutes)
CLOSE_TRIES = 3  # each try waits the posting's lock timeout at most
END_GRACE_S = 60  # past the lease's maximum, the session ends whatever browserd answers
DOCUMENT_OF = {"attach_cv": ("cv", run.CV_NAME), "attach_letter": ("letter", run.LETTER_NAME)}
OPEN = frozenset({State.queued, State.filling, State.needs_person})
TAKEN = "person_input"  # browserd's refusal once the person took the input back (Take over)
ENDED = "session_ended"  # `needs` of an application whose session ended before a confirmation


def job_id(application_id: object) -> str:
    """One session per application at a time: the panel asking again is the same job."""
    return f"apply-manual:{application_id}"


class Channel(Protocol):
    """The panel's commands to the session, and the session's status to the panel."""

    async def push(self, application_id: uuid.UUID, command: str) -> bool: ...

    async def pop(self, application_id: uuid.UUID, timeout_s: float) -> str | None: ...

    async def put_status(self, application_id: uuid.UUID, status: dict[str, Any]) -> None: ...

    async def status(self, application_id: uuid.UUID) -> dict[str, Any]: ...


class RedisChannel:
    """The channel on the worker's and the api's Redis: a short list and a status key."""

    def __init__(self, redis: Any) -> None:
        self.redis = redis

    async def push(self, application_id: uuid.UUID, command: str) -> bool:
        if command not in COMMANDS:
            raise ValueError(command)
        key = f"{KEY}{application_id}:commands"
        if await self.redis.llen(key) >= COMMANDS_MAX:
            return False
        await self.redis.rpush(key, command)
        await self.redis.expire(key, STATUS_TTL_S)
        return True

    async def pop(self, application_id: uuid.UUID, timeout_s: float) -> str | None:
        found = await self.redis.blpop([f"{KEY}{application_id}:commands"], timeout=timeout_s)
        if not found:
            return None
        raw = found[1]
        command = raw.decode() if isinstance(raw, bytes) else str(raw)
        return command if command in COMMANDS else None

    async def put_status(self, application_id: uuid.UUID, status: dict[str, Any]) -> None:
        await self.redis.set(f"{KEY}{application_id}:status", json.dumps(status), ex=STATUS_TTL_S)

    async def status(self, application_id: uuid.UUID) -> dict[str, Any]:
        raw = await self.redis.get(f"{KEY}{application_id}:status")
        try:
            found = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}


class Browser(Protocol):
    """What the session asks browserd besides the tools (the lease's client)."""

    async def current(self) -> dict[str, Any]: ...

    async def live_input(
        self, lease_id: str, viewer: str | None, giver: str | None = None
    ) -> str: ...


@dataclass
class Services:
    fill: run.Services
    channel: Channel
    browser: Browser
    # The lease's maximum of active time (BROWSER_LEASE_MAX_S's figure for `apply_manual`).
    max_s: int = 1800


@dataclass
class Live:
    """What the loop keeps between two turns (no ORM object, no value of the person's)."""

    application_id: uuid.UUID
    lease_id: str
    placed: dict[str, str] = field(default_factory=dict)  # kind -> file name on the lease
    seen: bool = False  # the page showed a confirmation
    chooser: bool = False  # a file chooser the person opened waits for a document
    idle_left_s: int | None = None
    busy: str | None = None
    last: dict[str, str] = field(default_factory=dict)  # the last command and its outcome

    def status(self, phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "documents": sorted(self.placed),
            "confirmation_seen": self.seen,
            "chooser": self.chooser,
            "idle_left_s": self.idle_left_s,
            "idle_warning": self.idle_left_s is not None and self.idle_left_s <= IDLE_WARN_S,
            "busy": self.busy,
            "last": dict(self.last),
        }


class _Page(ats_base.Adapter):
    """A form no adapter knows: the whole page is read, nothing is asked of its anchors."""

    name = "page"

    def form_url(self, posting_url: str) -> str | None:
        return None

    def anchor_problem(self, found: ats_base.Discovery) -> str:
        return ""


# --- the database steps (run in a thread) ------------------------------------------------------


def begin(db: Session, application_id: uuid.UUID, now: datetime) -> bool:
    found = run._locked(db, application_id)
    if found is None or found[0].state is not State.queued or found[0].mode is not Mode.manual:
        return False
    application = found[0]
    states.move(application, State.filling, now)
    application.needs = None
    application.attempt += 1
    run._event(db, application, "lease", now, purpose=PURPOSE, attempt=application.attempt)
    return True


def placed(db: Session, application_id: uuid.UUID, kinds: list[str], now: datetime) -> None:
    """Only the versions that reached the lease stay on the application (they are frozen)."""
    found = run._locked(db, application_id)
    if found is None:
        return
    application = found[0]
    versions = dict(application.document_versions or {})
    application.document_versions = {k: v for k, v in versions.items() if k in kinds}
    application.updated_at = now
    run._event(db, application, "placed", now, documents=sorted(kinds))


def note(db: Session, application_id: uuid.UUID, action: str, outcome: str, now: datetime) -> None:
    """The event of one command of the panel: its name and the outcome's code, no value."""
    application = db.get(Application, application_id)
    if application is not None:
        code, _, count = outcome.partition(":")
        extra = {"count": int(count)} if count.isdigit() else {}
        run._event(db, application, "manual", now, action=action, outcome=code, **extra)


def saw(db: Session, application_id: uuid.UUID, now: datetime) -> None:
    """The watch saw the site's confirmation: kept, so every end of the session records it."""
    application = db.get(Application, application_id)
    if application is not None and application.state is State.filling:
        run._event(db, application, "confirmation", now, seen=True)


def _confirmed(db: Session, application: Application) -> bool:
    return (
        db.execute(
            sa.select(ApplicationEvent.id)
            .where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.user_id == application.user_id,
                ApplicationEvent.kind == "confirmation",
            )
            .limit(1)
        ).first()
        is not None
    )


def is_open(db: Session, application_id: uuid.UUID) -> bool:
    """A plain read, no lock: the loop asks every second, beside the gate's writes."""
    state = db.execute(
        sa.select(Application.state).where(Application.id == application_id)
    ).scalar_one_or_none()
    return state is State.filling


def _sent(db: Session, application: Application, posting: Any, now: datetime, by: str) -> None:
    frozen = gate.freeze_sent(db, application, now)
    if JobStatus(posting.status) not in guardrails.APPLIED_STATUSES:
        jobs_service.set_status(
            db,
            posting,
            JobStatus.applied,
            now,
            via="apply",
            extra={"application_id": str(application.id)},
        )
    states.move(application, State.submitted, now)
    application.error_code = None
    application.needs = None
    run._event(db, application, "recorded", now, by=by, frozen=frozen)


def close(
    db: Session, application_id: uuid.UUID, code: str, now: datetime, *, seen: bool = False
) -> str:
    """The session's end (module docstring, step 5): an application still `filling` is
    `submitted` when a confirmation was seen (`seen`, or its event), else `queued` with `needs`
    ENDED. Answers the state written."""
    user_id = db.execute(
        sa.select(Application.user_id).where(Application.id == application_id)
    ).scalar_one_or_none()
    if user_id is None:
        return "gone"
    application, posting = gate.lock(db, user_id, application_id)
    if application.state is not State.filling:
        return application.state.value
    run._event(db, application, "ended", now, code=code)
    if seen or _confirmed(db, application):
        _sent(db, application, posting, now, "confirmation")
        return State.submitted.value
    states.move(application, State.queued, now)
    application.needs = ENDED
    return State.queued.value


def reset_interrupted(db: Session, now: datetime) -> int:
    """At the worker's start no session runs: every Apply myself left `filling` by a dead process
    ends as `close` ends it (`interrupted`). Answers a count."""
    ids = (
        db.execute(
            sa.select(Application.id).where(
                Application.state == State.filling, Application.mode == Mode.manual
            )
        )
        .scalars()
        .all()
    )
    return sum(
        close(db, application_id, "interrupted", now) != State.filling.value
        for application_id in ids
    )


def record(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime, *, seen: bool
) -> Application:
    """The person says it is sent: `submitted`, the posting `applied`, the placed versions
    frozen, in the caller's transaction. Raises gate.NotFoundError (another person's included)
    and gate.RefusedError: `not_manual` outside Apply myself, `already_recorded`, `not_open`."""
    application, posting = gate.lock(db, user_id, application_id)
    if application.mode is not Mode.manual:
        raise gate.RefusedError("not_manual")
    if application.state is State.submitted:
        raise gate.RefusedError("already_recorded")
    if application.state not in OPEN:
        raise gate.RefusedError("not_open")
    _sent(db, application, posting, now, "confirmation" if seen else "person")
    return application


def cancel(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime
) -> Application:
    """The person closes the session without recording it: `cancelled` (the session sees it
    and ends its lease), or `submitted` by `confirmation` once the page showed the site's
    confirmation. An application that already ended is answered as it is."""
    application, posting = gate.lock(db, user_id, application_id)
    if application.mode is not Mode.manual:
        raise gate.RefusedError("not_manual")
    if application.state in OPEN and _confirmed(db, application):
        _sent(db, application, posting, now, "confirmation")
    elif application.state in OPEN:
        states.move(application, State.cancelled, now)
        application.error_code = "closed"
        application.needs = None
        run._event(db, application, "ended", now, code="closed")
    return application


# --- the browser steps ---------------------------------------------------------------------------


def _ok(result: dict[str, Any]) -> str:
    return "" if result.get("ok") else str(result.get("error") or "failed")


async def _attach(
    tools: run.RunTools, reader: ats_base.Adapter, plan: run.Plan, s: Live, command: str
) -> str:
    kind, name = DOCUMENT_OF[command]
    if kind not in s.placed:
        return "no_document"
    if s.chooser:
        code = _ok(await tools.call("browser_file_upload", {"paths": [name]}))  # type: ignore[attr-defined]
        s.chooser = False
        return code or "attached"
    found, code = await reader.read(tools)
    if found is None:
        return code or "unreadable"
    hits = []
    for item in found.fields:
        if item.kind != "file":
            continue
        answer = resolve(item.form_field(), plan.context)
        if isinstance(answer, Answer) and answer.source == f"documents.{kind}":
            hits.append(item)
    if len(hits) != 1:
        return "pick"  # none, or two inputs read as the same document: the person clicks one
    return _ok(await tools.upload(hits[0].ref, [name], hits[0].label)) or "attached"


async def _fill_details(
    services: Services,
    tools: run.RunTools,
    reader: ats_base.Adapter,
    plan: run.Plan,
    s: Live,
) -> str:
    await services.browser.live_input(s.lease_id, None)
    try:
        found, code = await reader.read(tools)
        if found is None:
            return code or "unreadable"
        filled = 0
        for item in found.fields:
            if item.value or item.kind not in ("text", "select", "combobox"):
                continue  # what the person typed already stays
            answer = resolve(item.form_field(), plan.context)
            if not isinstance(answer, Answer) or not answer.source.startswith("identity."):
                continue
            result = await reader.fill(tools, item, answer)
            if result.code == TAKEN:
                return f"taken:{filled}"
            filled += result.status in run.FILLED
        return f"filled:{filled}"
    finally:
        await _give_back(services, s)


async def _give_back(services: Services, s: Live) -> None:
    """The input goes back to the person; if browserd cannot be told twice, the session ends
    (its lease released) rather than leave the person without the keyboard."""
    for _ in range(2):
        try:
            await services.browser.live_input(s.lease_id, str(uuid.uuid4()))
            return
        except Exception as exc:
            logger.warning("apply.manual_give_back_failed", extra={"error": type(exc).__name__})
    raise BrowserError("unavailable")


async def _watch(
    services: Services, tools: run.RunTools, reader: ats_base.Adapter, s: Live
) -> None:
    shot = await tools.call("browser_snapshot", {"passive": True})  # type: ignore[attr-defined]
    if shot.get("ok"):
        text = ats_base.page_text(str(shot.get("snapshot") or ""))
        if not s.seen and reader.confirmation(text, str(shot.get("url") or "")):
            s.seen = True
            fill = services.fill
            await asyncio.to_thread(
                run._in_db, fill.sessions, lambda db: saw(db, s.application_id, fill.clock())
            )
        s.chooser = bool(shot.get("chooser"))
    lease = (await services.browser.current()).get("lease") or {}
    if lease.get("lease_id") != s.lease_id:
        raise BrowserError("ended")
    left = lease.get("idle_left_s")
    s.idle_left_s = int(left) if isinstance(left, int | float) else None


async def _loop(
    services: Services,
    tools: run.RunTools,
    reader: ats_base.Adapter,
    plan: run.Plan,
    s: Live,
    deadline: float,
    monotonic: Callable[[], float],
) -> str:
    """The session's commands and watch until the person closes it, records it, or the lease
    ends. Answers the code of the end for `close`."""
    fill = services.fill
    watched = -math.inf
    while monotonic() < deadline:
        command = await services.channel.pop(plan.application_id, POLL_S)
        if command is not None:
            s.busy = command
            await services.channel.put_status(plan.application_id, s.status("open"))
            if command == "fill_details":
                outcome = await _fill_details(services, tools, reader, plan, s)
            else:
                outcome = await _attach(tools, reader, plan, s, command)
            s.busy, s.last = None, {"action": command, "outcome": outcome}
            await asyncio.to_thread(
                run._in_db,
                fill.sessions,
                lambda db, done=(command, outcome): note(
                    db, plan.application_id, *done, fill.clock()
                ),
            )
        still = await asyncio.to_thread(
            run._in_db, fill.sessions, lambda db: is_open(db, plan.application_id)
        )
        if not still:
            return ""
        if monotonic() - watched >= WATCH_S:
            watched = monotonic()
            await _watch(services, tools, reader, s)
        await services.channel.put_status(plan.application_id, s.status("open"))
    return "lease_max"


async def _close(fill: run.Services, application_id: uuid.UUID, code: str, seen: bool) -> str:
    """`close`, asked again past a busy posting (a worker restart ends what is left)."""

    def step(db: Session) -> str:
        return close(db, application_id, code, fill.clock(), seen=seen)

    for _ in range(CLOSE_TRIES - 1):
        with contextlib.suppress(jobs_service.JobBusyError):
            return await asyncio.to_thread(run._in_db, fill.sessions, step)
    return await asyncio.to_thread(run._in_db, fill.sessions, step)


async def session(
    services: Services,
    application_id: uuid.UUID,
    *,
    monotonic: Callable[[], float] | None = None,
) -> str:
    """The job: one Apply myself session, from `queued` to the person's end. Returns a code for
    the log (no value of the person's)."""
    fill = services.fill
    clock = monotonic or asyncio.get_running_loop().time
    started = fill.clock()
    plan = await asyncio.to_thread(
        run._in_db, fill.sessions, lambda db: run.prepare(db, application_id, started, manual=True)
    )
    if isinstance(plan, str):
        return plan
    user = str(plan.user_id)
    adapter = adapter_for(plan.form_url, stub_base=fill.stub_base)
    reader: ats_base.Adapter = adapter or _Page(fill.stub_base)

    async def wait(needs: str, **data: Any) -> None:
        def step(db: Session) -> None:
            found = run._locked(db, application_id)
            if found is not None and found[0].state is State.queued:
                run._wait(db, found[0], needs, fill.clock(), **data)

        await asyncio.to_thread(run._in_db, fill.sessions, step)

    try:
        acquired = await fill.leases.acquire(
            user,
            PURPOSE,
            True,
            hosts=run._hosts(adapter, plan.form_url),
            submit_selectors=run.SUBMIT_SELECTORS,
        )
    except (BrowserError, StoreUnavailableError) as exc:
        code = exc.code if isinstance(exc, BrowserError) else "store_unavailable"
        await wait("browser_unavailable", code=code)
        return "lease_unavailable"
    if acquired.lease is None:
        place = acquired.waiting
        position = place.position if place else None
        await wait("browser_queue", position=position)
        await services.channel.put_status(
            application_id, {"phase": "waiting", "position": position}
        )
        return "waiting_lease"
    lease_id = acquired.lease.lease_id
    if acquired.token is None:
        await fill.leases.release(user, lease_id, "apply_restart")
        return "lease_restarted"
    s = Live(application_id, lease_id)
    code = "ended"
    try:
        began = await asyncio.to_thread(
            run._in_db, fill.sessions, lambda db: begin(db, application_id, fill.clock())
        )
        if not began:
            return "not_queued"
        tools = fill.tools_for(user, lease_id, acquired.token)
        try:
            for kind, name in (("cv", run.CV_NAME), ("letter", run.LETTER_NAME)):
                data = plan.pdfs.get(name)
                if data is not None and (await tools.put_file(name, data)).get("ok"):
                    s.placed[kind] = name
            await asyncio.to_thread(
                run._in_db,
                fill.sessions,
                lambda db: placed(db, application_id, sorted(s.placed), fill.clock()),
            )
            target = (adapter.form_url(plan.form_url) if adapter else None) or plan.form_url
            await tools.navigate(target)
            await services.channel.put_status(application_id, s.status("open"))
            code = await _loop(
                services, tools, reader, plan, s, clock() + services.max_s + END_GRACE_S, clock
            )
        except BrowserError as exc:  # the lease ended: idle, its maximum, the window closed
            code = f"lease_{exc.code}" if exc.code else "lease_ended"
        except Exception as exc:
            logger.error("apply.manual_crashed", extra={"error": type(exc).__name__})
            code = "internal_error"
    except asyncio.CancelledError:
        await asyncio.shield(
            asyncio.to_thread(
                run._in_db,
                fill.sessions,
                lambda db: close(db, application_id, "interrupted", fill.clock(), seen=s.seen),
            )
        )
        raise
    finally:
        try:
            await fill.leases.release(user, lease_id, "apply_manual_done")
        except (BrowserError, StoreUnavailableError):
            logger.warning("apply.release_failed", extra={"application_id": str(application_id)})
    written = await _close(fill, application_id, code, s.seen)
    await services.channel.put_status(application_id, {**s.status("ended"), "state": written})
    logger.info("apply.manual_ended", extra={"application_id": str(application_id)})
    return written
