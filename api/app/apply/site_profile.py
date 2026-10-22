"""Person-started external candidate profile fills, separate from all posting workflows.

Only identifiers cross the queue. Values and screenshots live in owned database rows.
The profile-edit browser purpose blocks every Save from ordinary tools. The gate alone saves.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import answers, form_agent, profile_refusals, profile_sites, profile_sources, shots
from app.apply.ats import base, generic
from app.apply.models import (
    SiteFieldSource,
    SiteKind,
    SiteProfileField,
    SiteProfileRun,
    SiteProfileSection,
    SiteProfileShot,
    SiteRunState,
    SiteShotKind,
)
from app.browser import handoff, linkedin_guard
from app.documents import store as documents
from app.documents.models import DocumentVersion
from app.profile import service as profiles

LIVE = (
    SiteRunState.queued,
    SiteRunState.filling,
    SiteRunState.needs_person,
    SiteRunState.ready,
    SiteRunState.saving,
)
AUTOSAVE_SENTENCE = (
    "This site saves as you type, so a section cannot be reviewed before it is written. "
    "Only empty fields will be filled."
)
SENTENCES = {
    "bad_url": "Enter a complete http or https address without credentials.",
    "already_running": "A profile run on this site is already active. Open that run first.",
    "linkedin_off": "Turn on LinkedIn in Account and accept its current sentence "
    "before completing your profile.",
    "linkedin_paused": "LinkedIn is paused for seven days after a security checkpoint.",
    "not_ready": "This profile section is not ready for that action.",
    "stale": "The section changed since you reviewed it. "
    "Review the current values and approve again.",
    "save_missing": "The section has no safely designated Save. "
    "You can skip it or save it yourself.",
    "protected": "This protected field can only be changed by you on the site.",
    "autosave_confirmation": AUTOSAVE_SENTENCE,
}


class RefusedError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            SENTENCES.get(code, "The profile action could not continue. Open the run to review it.")
        )


class NotFoundError(LookupError):
    pass


def lock(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> SiteProfileRun:
    found = db.scalar(
        sa.select(SiteProfileRun)
        .where(SiteProfileRun.id == run_id, SiteProfileRun.user_id == user_id)
        .with_for_update()
    )
    if found is None:
        raise NotFoundError()
    return found


def fields(db: Session, run: SiteProfileRun, section: str) -> list[SiteProfileField]:
    return list(
        db.scalars(
            sa.select(SiteProfileField)
            .where(
                SiteProfileField.run_id == run.id,
                SiteProfileField.user_id == run.user_id,
                SiteProfileField.section == section,
            )
            .order_by(SiteProfileField.field_key)
        )
    )


def sections(db: Session, run: SiteProfileRun) -> list[SiteProfileSection]:
    return list(
        db.scalars(
            sa.select(SiteProfileSection)
            .where(SiteProfileSection.run_id == run.id, SiteProfileSection.user_id == run.user_id)
            .order_by(SiteProfileSection.position)
        )
    )


def section_of(db: Session, run: SiteProfileRun, key: str) -> SiteProfileSection:
    found = db.get(SiteProfileSection, (run.id, key))
    if found is None or found.user_id != run.user_id:
        raise NotFoundError()
    return found


def event(section: SiteProfileSection, kind: str, at: datetime, **data: Any) -> None:
    section.events = [*section.events, {"kind": kind, "at": at.isoformat(), **data}]
    section.updated_at = at


def finish_state(db: Session, run: SiteProfileRun, at: datetime) -> None:
    run.updated_at = at
    if run.state is SiteRunState.cancelled:
        return
    states = [s.state for s in sections(db, run)]
    run.state = (
        SiteRunState.done
        if states and all(s in ("saved", "skipped", "left") for s in states)
        else SiteRunState.ready
    )


def start(
    db: Session,
    user_id: uuid.UUID,
    url: str,
    autosave_confirmed: bool = False,
    *,
    now: datetime,
    stub_base: str | None = None,
) -> SiteProfileRun:
    try:
        parsed = urlsplit(url.strip())
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.port != 0
        )
    except ValueError:
        valid = False
    if not valid or any(c.isspace() or ord(c) < 32 for c in url):
        raise RefusedError("bad_url")
    host = (parsed.hostname or "").lower()
    kind = SiteKind(profile_sites.kind(url, stub_base))
    if kind is SiteKind.linkedin and (code := linkedin_guard.refusal(db, user_id, now)):
        raise RefusedError(code)
    db.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"site-profile:{user_id}"}
    )
    if db.scalar(
        sa.select(SiteProfileRun.id)
        .where(
            SiteProfileRun.user_id == user_id,
            SiteProfileRun.host == host,
            SiteProfileRun.state.in_(LIVE),
        )
        .limit(1)
    ):
        raise RefusedError("already_running")
    run = SiteProfileRun(
        id=uuid.uuid4(),
        user_id=user_id,
        url=url.strip(),
        host=host,
        site_kind=kind,
        state=SiteRunState.queued,
        autosave=False,
        autosave_confirmed=autosave_confirmed,
        cost={},
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()
    return run


def edit(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    section: str,
    key: str,
    changes: dict[str, Any],
    now: datetime,
) -> None:
    run = lock(db, user_id, run_id)
    part = section_of(db, run, section)
    if (
        run.state not in (SiteRunState.ready, SiteRunState.done)
        or part.state not in ("ready", "left")
        or run.autosave
    ):
        raise RefusedError("not_ready")
    row = db.get(SiteProfileField, (run.id, section, key))
    if row is None or row.user_id != user_id:
        raise NotFoundError()
    if profile_refusals.refusal(row.label, row.kind):
        raise RefusedError("protected")
    if "value" in changes:
        value = changes["value"]
        valid_type = (
            isinstance(value, str)
            if row.kind in ("text", "select", "combobox")
            else isinstance(value, (str, bool))
            if row.kind == "yes_no"
            else False
        )
        if (
            not valid_type
            or row.kind == "file"
            or (row.options and value not in row.options)
            or value is None
        ):
            raise RefusedError("protected")
        if isinstance(value, str):
            profile = profiles.get_profile(db, user_id, now)
            context = answers.Context(user_id, profiles.sections_view(profile))
            if not profile_sources.factual(value, context):
                raise RefusedError("factcheck")
        row.value, row.source, row.source_path, row.refusal = (
            value,
            SiteFieldSource.person,
            "person",
            None,
        )
    if changes.get("replace") is not None:
        row.replace = changes["replace"]
    row.approved = False
    part.error_code = None
    event(part, "edited", now, fields=1)
    run.updated_at = now


def address(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> str | None:
    run = db.scalar(
        sa.select(SiteProfileRun).where(
            SiteProfileRun.id == run_id,
            SiteProfileRun.user_id == user_id,
            SiteProfileRun.state == SiteRunState.needs_person,
            SiteProfileRun.needs == "signin",
        )
    )
    return run.url if run else None


def resume(db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> bool:
    run = lock(db, user_id, run_id)
    if run.state is not SiteRunState.needs_person or run.needs != "signin":
        return False
    run.state, run.needs, run.updated_at = SiteRunState.queued, None, now
    return True


def key_of(item: base.Field) -> str:
    return hashlib.sha256(item.key.encode()).hexdigest()[:24]


def source_of(source: str) -> SiteFieldSource:
    root = source.split(".")[0]
    if root == "documents":
        return SiteFieldSource.document
    return (
        SiteFieldSource(root)
        if root in ("identity", "experience", "skills", "answers")
        else SiteFieldSource.left
    )


def should_write(row: SiteProfileField, autosave: bool = False) -> bool:
    return (
        row.value not in (None, "")
        and not row.refusal
        and (row.current_value in (None, "", False) or (row.replace and not autosave))
    )


def store_shot(
    db: Session, run: SiteProfileRun, section: str, kind: SiteShotKind, jpeg: bytes, at: datetime
) -> None:
    db.add(
        SiteProfileShot(
            id=uuid.uuid4(),
            run_id=run.id,
            user_id=run.user_id,
            section=section,
            kind=kind,
            jpeg=jpeg,
            sha256=hashlib.sha256(jpeg).digest(),
            created_at=at,
        )
    )


@dataclass
class Plan:
    run_id: uuid.UUID
    user_id: uuid.UUID
    url: str
    host: str
    linkedin: bool
    site_kind: str
    confirmed: bool
    context: answers.Context
    cv: bytes | None


def prepare(db: Session, run_id: uuid.UUID, now: datetime) -> Plan | None:
    run = db.scalar(sa.select(SiteProfileRun).where(SiteProfileRun.id == run_id).with_for_update())
    if run is None or run.state is not SiteRunState.queued:
        return None
    if run.site_kind is SiteKind.linkedin and not linkedin_guard.allowed(db, run.user_id, now):
        run.state, run.error_code = SiteRunState.failed, "linkedin_off"
        return None
    profile = profiles.get_profile(db, run.user_id, now)
    context = answers.Context(
        run.user_id,
        {
            k: v
            for k, v in profiles.sections_view(profile).items()
            if v and k in ("identity", "experience", "skills", "answers", "facts")
        },
    )
    cv = prepare_cv(db, run.user_id)
    if cv is not None:
        context = answers.Context(
            run.user_id, context.sections, documents=answers.Documents(cv_path="reference-cv.pdf")
        )
    run.state, run.needs, run.error_code, run.updated_at = SiteRunState.filling, None, None, now
    return Plan(
        run.id,
        run.user_id,
        run.url,
        run.host,
        run.site_kind is SiteKind.linkedin,
        run.site_kind.value,
        run.autosave_confirmed,
        context,
        cv,
    )


def prepare_cv(db: Session, user_id: uuid.UUID) -> bytes | None:
    document = documents.default_base(db, user_id)
    if document is None:
        return None
    version = db.scalar(
        sa.select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.user_id == user_id,
            DocumentVersion.pdf.is_not(None),
        )
        .order_by(DocumentVersion.number.desc())
        .limit(1)
    )
    return bytes(version.pdf) if version is not None else None


@dataclass
class ProfileSession(form_agent.FormSession):
    section_label: str = ""
    rows: dict[str, SiteProfileField] | None = None
    autosave: bool = False

    async def resolve_answer(self, ref: str) -> tuple[str, bool]:
        item, problem = await self._field(ref)
        if item is None:
            return problem, True
        answer = profile_sources.resolve(item, self.context, self.section_label)
        return json.dumps(
            {
                "answered": isinstance(answer, answers.Answer),
                "source": answer.source if isinstance(answer, answers.Answer) else answer.reason,
            }
        ), False

    async def fill_field(self, ref: str) -> tuple[str, bool]:
        item, problem = await self._field(ref)
        if item is None:
            return problem, True
        row = (self.rows or {}).get(key_of(item))
        if row is None or not should_write(row, self.autosave):
            return json.dumps({"status": "left"}), False
        if item.key in self.outcomes:
            return json.dumps({"status": "already_filled"}), False
        # Recheck the current site value immediately before typing, especially on autosave.
        if item.value and item.value != row.current_value:
            return "The site changed this value. It is kept for the person.", True
        answer = profile_sources.resolve(item, self.context, self.section_label)
        result = await self.adapter.fill(self.tools, item, answer)
        self.outcomes[item.key] = base.Outcome(item, answer, result)
        return json.dumps({"status": result.status, "code": result.code}), result.status == "failed"

    async def next_step(self, ref: str) -> tuple[str, bool]:
        return "Review this section before moving to another profile page.", True

    async def designate_submit(self, ref: str) -> tuple[str, bool]:
        shot = await self.tools.snapshot()
        section = self.adapter.section(base.parse_snapshot(str(shot.get("snapshot") or "")))
        node, code = profile_sites.save(section, ref) if section else (None, "section_missing")
        if node is None or code or not await generic.marked(self.tools, ref, node.name):
            return "The Save is not safely inside this section.", True
        self.designated = node.name
        return "Save designated. Only the person's section approval can save it.", False


def _in_db(services: Any, action: Any) -> Any:
    from app.apply.run import _in_db as transaction

    return transaction(services.sessions, action)


async def fill(services: Any, run_id: uuid.UUID, agent: Any = None) -> None:
    plan = await asyncio.to_thread(
        _in_db, services, lambda db: prepare(db, run_id, services.clock())
    )
    if plan is None:
        return
    acquired = None
    retained = False

    async def update(
        state: SiteRunState, needs: str | None = None, code: str | None = None
    ) -> None:
        def change(db: Session) -> None:
            run = lock(db, plan.user_id, run_id)
            if run.state is not SiteRunState.cancelled:
                run.state, run.needs, run.error_code, run.updated_at = (
                    state,
                    needs,
                    code,
                    services.clock(),
                )

        await asyncio.to_thread(_in_db, services, change)

    try:
        acquired = await services.leases.acquire(
            str(plan.user_id), "profile_edit", True, hosts=[plan.host]
        )
        if acquired.lease is None or not acquired.token:
            await update(SiteRunState.queued, "browser_busy")
            return
        tools = services.tools_for(str(plan.user_id), acquired.lease.lease_id, acquired.token)
        if not (await tools.navigate(plan.url)).get("ok"):
            await update(SiteRunState.failed, code="navigation_failed")
            return
        shot = await tools.snapshot()
        text = str(shot.get("snapshot") or "")
        if plan.linkedin and any(
            t in base.fold(text)
            for t in (
                "security verification",
                "unusual activity",
                "checkpoint",
                "verification de securite",
            )
        ):

            def checkpoint(db: Session) -> None:
                linkedin_guard.record_checkpoint(
                    db, plan.user_id, services.clock(), origin="profile"
                )

            await asyncio.to_thread(_in_db, services, checkpoint)
            await update(SiteRunState.failed, code="linkedin_paused")
            return
        pending = base.Adapter().pending_step(text)
        if pending == "signin":
            await update(SiteRunState.needs_person, "signin")
            outcome = await handoff.request_signin(
                tools, plan.host, "Sign in to complete your candidate profile."
            )
            if outcome.code != "signed_in":
                if outcome.code == "turn_ended":
                    from app.browser.later_handoff import bind_wait

                    await bind_wait(
                        services.leases,
                        str(plan.user_id),
                        "site_profile",
                        run_id,
                        acquired.lease.lease_id,
                    )
                    retained = True
                return
            await update(SiteRunState.filling)
            shot = await tools.snapshot()
            text = str(shot.get("snapshot") or "")
        elif pending:
            await update(SiteRunState.needs_person, pending)
            return
        parts = await profile_sites.catalogue(tools, plan.site_kind)
        is_autosave = False
        for descriptor in parts:
            opened = await profile_sites.open_section(
                tools, plan.site_kind, descriptor.key, descriptor.label
            )
            if opened is None:
                await update(SiteRunState.failed, code="section_unreadable")
                return
            is_autosave = is_autosave or profile_sites.autosave(opened.node)
            if not await profile_sites.close_section(tools, plan.site_kind, descriptor.key):
                await update(SiteRunState.failed, code="section_could_not_close")
                return
        if is_autosave and not plan.confirmed:
            await update(SiteRunState.needs_person, "autosave_confirmation")
            return
        if not parts or shot.get("cut") or not shot.get("ok"):
            await update(SiteRunState.failed, code="profile_unreadable")
            return
        if plan.cv is not None:
            await tools.put_file("reference-cv.pdf", plan.cv)
        token_ceiling = (
            form_agent.TOKEN_BUDGET if plan.site_kind == "generic" else 23_000 * len(parts)
        )
        used_tokens, used_turns = 0, 0
        for part_position, descriptor in enumerate(parts, 1):
            part = await profile_sites.open_section(
                tools, plan.site_kind, descriptor.key, descriptor.label
            )
            if part is None:
                await update(SiteRunState.failed, code="section_unreadable")
                return

            def persist(
                db: Session, part=part, part_position=part_position
            ) -> list[SiteProfileField]:
                run = lock(db, plan.user_id, run_id)
                if run.state is SiteRunState.cancelled:
                    return []
                run.autosave = is_autosave
                section = db.get(SiteProfileSection, (run_id, part.key))
                if section is not None:
                    return []  # resumptions never rewrite a reviewed section
                save, _ = profile_sites.save(part)
                section = SiteProfileSection(
                    run_id=run_id,
                    user_id=plan.user_id,
                    section=part.key,
                    position=part_position,
                    label=part.label,
                    save_name=save.name if save else "",
                    state="ready",
                    events=[],
                    updated_at=services.clock(),
                )
                db.add(section)
                rows = []
                for item in part.fields:
                    answer = profile_sources.resolve(item, plan.context, part.label)
                    answered = isinstance(answer, answers.Answer)
                    row = SiteProfileField(
                        run_id=run_id,
                        user_id=plan.user_id,
                        section=part.key,
                        field_key=key_of(item),
                        label=item.label,
                        kind=item.kind,
                        value=answer.value if answered else None,
                        current_value=(
                            item.checked if item.kind == "checkbox" else item.value or None
                        ),
                        source=source_of(answer.source) if answered else SiteFieldSource.left,
                        source_path=answer.source if answered else "",
                        required=item.required,
                        options=list(item.options),
                        refusal=None if answered else answer.reason,
                        replace=False,
                        approved=False,
                    )
                    db.add(row)
                    rows.append(row)
                db.flush()
                for row in rows:
                    db.expunge(row)
                return rows

            rows = await asyncio.to_thread(_in_db, services, persist)
            if not rows:
                continue
            if used_tokens >= token_ceiling or used_turns >= form_agent.MAX_TURNS:

                def budget_left(db: Session, key=part.key):
                    run = lock(db, plan.user_id, run_id)
                    section = section_of(db, run, key)
                    section.state, section.error_code = "left", "budget_exhausted"
                    event(section, "left", services.clock(), code="budget_exhausted")

                await asyncio.to_thread(_in_db, services, budget_left)
                await profile_sites.close_section(tools, plan.site_kind, part.key)
                continue
            if is_autosave:
                from app.apply import profile_gate

                jpeg = await shots.take(tools)
                if jpeg is None:
                    await update(SiteRunState.failed, code="screenshot_failed")
                    return

                def approve_autosave(db: Session, part=part, jpeg=jpeg):
                    run = lock(db, plan.user_id, run_id)
                    section = section_of(db, run, part.key)
                    store_shot(db, run, part.key, SiteShotKind.before, jpeg, services.clock())
                    auth = profile_gate.authorise_section(
                        db,
                        plan.user_id,
                        run_id,
                        part.key,
                        profile_sources.value_hash(section, fields(db, run, part.key)),
                        services.clock(),
                        autosave=True,
                    )
                    return auth.id

                auth_id = await asyncio.to_thread(_in_db, services, approve_autosave)
                await profile_gate.save_section(
                    services, run_id, part.key, auth_id, held_tools=tools
                )
                continue
            adapter = profile_sites.SectionAdapter(part.key, part.label, plan.site_kind)
            session = ProfileSession(
                adapter,
                tools,
                plan.context,
                str(plan.user_id),
                token_budget=min(token_ceiling - used_tokens, 23_000)
                if plan.site_kind != "generic"
                else token_ceiling - used_tokens,
                turn_budget=form_agent.MAX_TURNS - used_turns,
                section_label=part.label,
                rows={r.field_key: r for r in rows},
                autosave=is_autosave,
            )
            for item in part.fields:
                found, _ = await adapter.read(tools)
                current = found.find(item.key) if found else None
                if current:
                    await session.fill_field(current.ref)
            if agent:
                ran = await agent(session)
                used_tokens += ran.input_tokens + ran.output_tokens
                used_turns += ran.turns
                if ran.stopped in ("turn_limit", "token_budget"):
                    used_turns = form_agent.MAX_TURNS

                def cost(db: Session, ran=ran, used_turns=used_turns) -> None:
                    run = lock(db, plan.user_id, run_id)
                    run.cost = {
                        "input_tokens": int(run.cost.get("input_tokens", 0)) + ran.input_tokens,
                        "output_tokens": int(run.cost.get("output_tokens", 0)) + ran.output_tokens,
                        "token_budget": token_ceiling,
                        "turns": used_turns,
                        "turn_cap": form_agent.MAX_TURNS,
                    }

                await asyncio.to_thread(_in_db, services, cost)
            latest = await tools.snapshot()
            current_part = adapter.section(base.parse_snapshot(str(latest.get("snapshot") or "")))
            save, _ = (
                profile_sites.save(current_part) if current_part else (None, "section_missing")
            )
            if save and not is_autosave:
                await session.designate_submit(save.ref)
            jpeg = await shots.take(tools)
            if save and not is_autosave:
                jpeg = await generic.highlighted(tools, save.ref, jpeg)

            def ready(db: Session, part=part, session=session, jpeg=jpeg, rows=rows) -> None:
                run = lock(db, plan.user_id, run_id)
                section = section_of(db, run, part.key)
                if run.state is SiteRunState.cancelled:
                    return
                section.save_name = session.designated
                if jpeg:
                    store_shot(db, run, part.key, SiteShotKind.before, jpeg, services.clock())
                section.error_code = (
                    None
                    if jpeg and (session.designated or is_autosave)
                    else "screenshot_or_save_missing"
                )
                for row in fields(db, run, part.key):
                    outcome = next(
                        (o for o in session.outcomes.values() if key_of(o.field) == row.field_key),
                        None,
                    )
                    if outcome and outcome.filled.status not in ("filled", "left"):
                        row.refusal = outcome.filled.code or "unverified"
                event(
                    section,
                    "filled",
                    services.clock(),
                    fields=len(rows),
                    left=sum(r.source is SiteFieldSource.left for r in rows),
                )

            await asyncio.to_thread(_in_db, services, ready)
            if not await profile_sites.close_section(tools, plan.site_kind, part.key):
                await update(SiteRunState.failed, code="section_could_not_close")
                return

        def end(db: Session) -> None:
            run = lock(db, plan.user_id, run_id)
            finish_state(db, run, services.clock())

        await asyncio.to_thread(_in_db, services, end)
    except Exception:
        await update(SiteRunState.failed, code="profile_run_failed")
    finally:
        if acquired and acquired.lease and not retained:
            await services.leases.release(str(plan.user_id), acquired.lease.lease_id)


async def site_profile_run(ctx: dict[str, Any], run_id: str) -> None:
    from app.apply.profile_gate import configure
    from app.worker import agent_context, apply_services

    services = ctx.get("site_profile_services") or configure(
        apply_services(ctx), ctx["settings"], ctx.get("redis")
    )
    agent = ctx.get("site_profile_agent") or form_agent.RuntimeFormAgent(lambda: agent_context(ctx))
    await fill(services, uuid.UUID(run_id), agent)


def job_id(run_id: Any) -> str:
    return f"site-profile:{run_id}"


def reset_interrupted(db: Session, now: datetime) -> int:
    """Called only before the serial apply worker starts accepting jobs.

    A consumed Save may have reached the site. Its section needs explicit review, and
    its authorization remains consumed. This recovery never touches a browser lease.
    """
    runs = list(
        db.scalars(
            sa.select(SiteProfileRun)
            .where(SiteProfileRun.state.in_((SiteRunState.filling, SiteRunState.saving)))
            .with_for_update()
        )
    )
    for run in runs:
        if run.state is SiteRunState.filling:
            run.state, run.needs, run.error_code = SiteRunState.failed, None, "fill_interrupted"
        else:
            run.state, run.needs, run.error_code = SiteRunState.ready, None, "save_unknown"
            for part in sections(db, run):
                if part.state == "saving":
                    part.state, part.error_code = "ready", "save_unknown"
                    event(part, "save_interrupted", now)
        run.updated_at = now
    return len(runs)


async def tick(ctx: dict[str, Any]) -> int:
    """Recover durable queued fills and approved saves, never replay a consumed approval."""
    from app.apply.models import Authorisation
    from app.apply.run import _in_db as transaction
    from app.queue import APPLY
    from app.worker import clock_of, sessions_of

    def due(db: Session):
        fills = list(
            db.scalars(
                sa.select(SiteProfileRun.id).where(SiteProfileRun.state == SiteRunState.queued)
            )
        )
        saves = []
        pending = list(
            db.scalars(sa.select(SiteProfileSection).where(SiteProfileSection.state == "approved"))
        )
        for part in pending:
            run = db.get(SiteProfileRun, part.run_id)
            digest = profile_sources.value_hash(part, fields(db, run, part.section))
            auth = db.scalar(
                sa.select(Authorisation)
                .where(
                    Authorisation.site_run_id == run.id,
                    Authorisation.user_id == run.user_id,
                    Authorisation.answers_hash == digest,
                    Authorisation.used_at.is_(None),
                    Authorisation.expires_at > clock_of(ctx)(),
                )
                .order_by(Authorisation.created_at.desc())
                .limit(1)
            )
            if auth:
                saves.append((run.id, part.section, auth.id))
            else:
                part.state, part.error_code = "ready", "approval_expired"
                event(part, "refused", clock_of(ctx)(), code="approval_expired")
                finish_state(db, run, clock_of(ctx)())
        return fills, saves

    fills, saves = await asyncio.to_thread(transaction, sessions_of(ctx), due)
    for run_id in fills:
        await ctx["redis"].enqueue_job(
            "site_profile_run", str(run_id), _queue_name=APPLY, _job_id=job_id(run_id)
        )
    for run_id, section, auth_id in saves:
        await ctx["redis"].enqueue_job(
            "site_profile_save",
            str(run_id),
            section,
            str(auth_id),
            _queue_name=APPLY,
            _job_id=f"site-profile-save:{auth_id}",
        )
    return len(fills) + len(saves)


def queue_functions(timeout: int):
    from arq.worker import func

    from app.apply.profile_gate import site_profile_save

    return [
        func(
            site_profile_run, name="site_profile_run", timeout=timeout, max_tries=1, keep_result=0
        ),
        func(
            site_profile_save, name="site_profile_save", timeout=timeout, max_tries=1, keep_result=0
        ),
    ]
