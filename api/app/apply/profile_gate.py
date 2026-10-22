"""One-use, user-bound section Save gate. No model runs on this path.

The audit and consumed authorization commit before the sole external Save click. A crash
leaves an explicit uncertain section requiring a new review, never an automatic second click.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import (
    admin,
    profile_leases,
    profile_refusals,
    profile_sites,
    profile_sources,
    shots,
    site_profile,
)
from app.apply.answers import Answer
from app.apply.ats import base, generic
from app.apply.caps_models import ApplySettings
from app.apply.models import Authorisation, AuthorisationKind, SiteKind, SiteRunState, SiteShotKind
from app.browser import linkedin_guard
from app.browser.tools import gate_click

TTL = timedelta(hours=24)


def authorise_section(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    section: str,
    values_hash: bytes,
    now: datetime,
    *,
    autosave: bool = False,
) -> Authorisation:
    run = site_profile.lock(db, user_id, run_id)
    part = site_profile.section_of(db, run, section)
    rows = site_profile.fields(db, run, section)
    if (
        run.state not in (SiteRunState.ready, SiteRunState.filling)
        or part.state != "ready"
        or (run.autosave and not autosave)
    ):
        raise site_profile.RefusedError("not_ready")
    if values_hash != profile_sources.value_hash(part, rows):
        raise site_profile.RefusedError("stale")
    if part.error_code or (not autosave and not part.save_name):
        raise site_profile.RefusedError("save_missing")
    if run.site_kind is SiteKind.linkedin and not linkedin_guard.allowed(db, user_id, now):
        raise site_profile.RefusedError("linkedin_off")
    if autosave and not run.autosave_confirmed:
        raise site_profile.RefusedError("autosave_confirmation")
    made = Authorisation(
        id=uuid.uuid4(),
        user_id=user_id,
        site_run_id=run_id,
        kind=AuthorisationKind.profile_section,
        answers_hash=values_hash,
        created_at=now,
        expires_at=now + TTL,
    )
    db.add(made)
    part.state = "approved"
    for row in rows:
        row.approved = site_profile.should_write(row, run.autosave)
    site_profile.event(
        part, "authorised", now, authorisation_id=str(made.id), fields=sum(r.approved for r in rows)
    )
    db.flush()
    return made


def switch_code(db: Session, user_id: uuid.UUID, enabled: bool, paused: bool) -> str | None:
    if not enabled:
        return "disabled"
    if paused:
        return "paused"
    settings = db.get(ApplySettings, user_id)
    return "paused_by_you" if settings and settings.paused else None


def consume(
    db: Session,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    section: str,
    auth_id: uuid.UUID,
    now: datetime,
    enabled: bool,
    paused: bool,
) -> tuple[Any, Any, list[Any]]:
    run = site_profile.lock(db, user_id, run_id)
    part = site_profile.section_of(db, run, section)
    rows = site_profile.fields(db, run, section)
    auth = db.scalar(
        sa.select(Authorisation)
        .where(
            Authorisation.id == auth_id,
            Authorisation.user_id == user_id,
            Authorisation.site_run_id == run_id,
        )
        .with_for_update()
    )
    code = switch_code(db, user_id, enabled, paused)
    if code is None and (
        auth is None
        or auth.kind is not AuthorisationKind.profile_section
        or auth.used_at is not None
        or auth.expires_at <= now
    ):
        code = "invalid_authorisation"
    if code is None and (part.state != "approved" or run.state is SiteRunState.cancelled):
        code = "not_ready"
    if code is None and auth.answers_hash != profile_sources.value_hash(part, rows):
        code = "stale"
    if (
        code is None
        and run.site_kind is SiteKind.linkedin
        and not linkedin_guard.allowed(db, user_id, now)
    ):
        code = "linkedin_off"
    if code:
        site_profile.event(part, "refused", now, code=code)
        part.state, part.error_code = "ready", code
        run.state = SiteRunState.ready if run.state is not SiteRunState.cancelled else run.state
        return run, part, []
    auth.used_at = now
    part.state, run.state = "saving", SiteRunState.saving
    site_profile.event(
        part,
        "save_decision",
        now,
        authorisation_id=str(auth.id),
        fields=sum(r.approved for r in rows),
    )
    return run, part, rows


async def _switches(services: Any) -> tuple[bool, bool]:
    checker = getattr(services, "profile_switches", None)
    return await checker() if checker else (False, True)


def configure(services: Any, settings: Any, redis: Any) -> Any:
    async def switches() -> tuple[bool, bool]:
        return bool(settings.apply_submit_enabled), await admin.paused(redis)

    services.profile_switches = switches
    return services


async def save_section(
    services: Any, run_id: uuid.UUID, section: str, auth_id: uuid.UUID, *, held_tools: Any = None
) -> None:
    def load(db: Session):
        run = db.get(site_profile.SiteProfileRun, run_id)
        if run is None:
            return None
        part = site_profile.section_of(db, run, section)
        rows = site_profile.fields(db, run, section)
        if part.state != "approved":
            return None
        payload = (
            run.user_id,
            run.url,
            run.host,
            run.site_kind.value,
            run.autosave,
            part.label,
            part.save_name,
            [
                (r.field_key, r.label, r.kind, r.value, r.current_value, r.source_path, r.approved)
                for r in rows
            ],
        )
        return payload

    plan = await asyncio.to_thread(site_profile._in_db, services, load)
    if plan is None:
        return
    user_id, url, host, site_kind, autosave, label, save_name, rows = plan
    acquired = None

    async def failed(code: str, changed: list[str] | None = None) -> None:
        def step(db: Session):
            run = site_profile.lock(db, user_id, run_id)
            part = site_profile.section_of(db, run, section)
            if run.state is SiteRunState.cancelled:
                return
            part.state, part.error_code = "ready", code
            site_profile.event(
                part, "readback_failed", services.clock(), code=code, fields=len(changed or [])
            )
            for row in site_profile.fields(db, run, section):
                if changed and row.field_key in changed:
                    row.refusal = "value_differs"
            site_profile.finish_state(db, run, services.clock())

        await asyncio.to_thread(site_profile._in_db, services, step)

    try:
        enabled, paused = await _switches(services)

        def allowed(db: Session):
            return switch_code(db, user_id, enabled, paused)

        if code := await asyncio.to_thread(site_profile._in_db, services, allowed):
            await failed(code)
            return
        if held_tools is None:
            acquired = await services.leases.acquire(
                str(user_id), "profile_edit", True, hosts=[host]
            )
            if acquired.lease is None or not acquired.token or not acquired.capability:
                return  # the tick retries this section, with no authorization consumed
            await profile_leases.bind(services.leases, user_id, run_id, acquired.lease.lease_id)
            tools = services.tools_for(str(user_id), acquired.lease.lease_id, acquired.token)
            if not (await tools.navigate(url)).get("ok"):
                await failed("navigation_failed")
                return
        else:
            tools = held_tools
        if site_kind == "linkedin" and site_profile.checkpoint_page(await tools.snapshot(), url):

            def checkpoint(db: Session):
                linkedin_guard.record_checkpoint(db, user_id, services.clock(), origin="profile")

            await asyncio.to_thread(site_profile._in_db, services, checkpoint)
            await failed("linkedin_paused")
            return
        if await profile_sites.open_section(tools, site_kind, section, label) is None:
            await failed("section_unreadable")
            return
        adapter = profile_sites.SectionAdapter(section, label, site_kind)
        initial, _ = await adapter.read(tools)
        if (
            initial is None
            or initial.pending
            or {site_profile.key_of(f) for f in initial.fields} != {r[0] for r in rows}
        ):
            await failed("form_changed")
            return
        if any(f.kind == "checkbox" and f.checked for f in initial.fields):
            await failed("legal_commitment")
            return
        if any(
            term in base.fold(" ".join(initial.root.words()))
            for term in (
                "credit card",
                "payment required",
                "pay to continue",
                "carte bancaire",
                "paiement requis",
            )
        ):
            await failed("payment_requested")
            return
        for key, _field_label, kind, value, current, source, approved in rows:
            if not approved:
                continue
            found, code = await adapter.read(tools)
            item = (
                next((f for f in found.fields if site_profile.key_of(f) == key), None)
                if found
                else None
            )
            if item is None or found.pending or profile_refusals.refusal(item.label, item.kind):
                await failed("form_changed", [key])
                return
            if (item.value or None) != current:
                await failed("site_value_changed", [key])
                return
            if autosave:
                if item.value:
                    await failed("autosave_existing_value", [key])
                    return
            else:
                if kind == "file":

                    def cv(db: Session, source=source):
                        plan = site_profile.prepare_cv(db, user_id, source)
                        return plan

                    pdf = await asyncio.to_thread(site_profile._in_db, services, cv)
                    if pdf is None:
                        await failed("reference_cv_missing")
                        return
                    await tools.put_file("reference-cv.pdf", pdf)
                result = await adapter.fill(
                    tools,
                    item,
                    Answer(value, "documents.cv" if kind == "file" else source or "person"),
                )
                if result.status != "filled":
                    await failed("form_changed", [key])
                    return
        found, _ = await adapter.read(tools)
        if found is None or found.pending or found.root is None:
            await failed("form_changed")
            return
        current_part = profile_sites.Section(section, label, found.root, found.fields)
        target, code = profile_sites.save(current_part)
        if not autosave and (
            target is None
            or target.name != save_name
            or not await generic.marked(tools, target.ref, target.name)
        ):
            await failed("save_missing")
            return
        if not autosave:
            changed = [
                key
                for key, _, _, value, _, _, approved in rows
                if approved
                and not any(
                    site_profile.key_of(f) == key and f.value == str(value) for f in found.fields
                )
            ]
            if changed:
                await failed("value_differs", changed)
                return
        enabled, paused = await _switches(services)

        def decision(db: Session):
            _run, part, _selected = consume(
                db, user_id, run_id, section, auth_id, services.clock(), enabled, paused
            )
            return part.state == "saving"

        if not await asyncio.to_thread(site_profile._in_db, services, decision):
            return
        # Recheck immediately after the committed audit, before the one external act.
        enabled, paused = await _switches(services)
        if not enabled or paused:
            await failed("paused")
            return

        def still_allowed(db: Session):
            run = site_profile.lock(db, user_id, run_id)
            return (
                run.state is not SiteRunState.cancelled
                and switch_code(db, user_id, enabled, paused) is None
                and (
                    run.site_kind is not SiteKind.linkedin
                    or linkedin_guard.allowed(db, user_id, services.clock())
                )
            )

        if not await asyncio.to_thread(site_profile._in_db, services, still_allowed):
            await failed("paused")
            return
        if autosave:
            for key, _field_label, kind, value, _current, _source, approved in rows:
                if not approved:
                    continue
                enabled, paused = await _switches(services)
                if (
                    not enabled
                    or paused
                    or not await asyncio.to_thread(site_profile._in_db, services, still_allowed)
                ):
                    await failed("paused")
                    return
                found, _ = await adapter.read(tools)
                item = (
                    next((f for f in found.fields if site_profile.key_of(f) == key), None)
                    if found
                    else None
                )
                if item is None or item.value or found.pending:
                    await failed("autosave_existing_value", [key])
                    return
                if kind != "text" or not hasattr(tools, "type_empty"):
                    await failed("autosave_unsupported", [key])
                    return
                result = await tools.type_empty(item.ref, str(value), item.label)
                if not result.get("ok"):
                    await failed("value_differs", [key])
                    return
        if not autosave:
            result = await gate_click(tools, target.ref, acquired.capability, target.name)
            if not result.get("ok"):
                await failed("save_refused")
                return
        await tools.wait_for(time=1.0)
        if not (await tools.navigate(url)).get("ok"):
            await failed("save_unknown")
            return
        if await profile_sites.open_section(tools, site_kind, section, label) is None:
            await failed("readback_section_missing")
            return
        found, _ = await adapter.read(tools)
        changed = [
            key
            for key, _, _, value, _, _, approved in rows
            if approved
            and not (
                found
                and any(
                    site_profile.key_of(f) == key and f.value == str(value) for f in found.fields
                )
            )
        ]
        jpeg = await shots.take(tools)
        if changed or jpeg is None:
            await failed("value_differs" if changed else "screenshot_failed", changed)
            return

        def saved(db: Session):
            run = site_profile.lock(db, user_id, run_id)
            part = site_profile.section_of(db, run, section)
            site_profile.store_shot(db, run, section, SiteShotKind.after, jpeg, services.clock())
            part.state, part.error_code = "saved", None
            site_profile.event(part, "saved", services.clock(), fields=sum(r[-1] for r in rows))
            site_profile.finish_state(db, run, services.clock())

        await asyncio.to_thread(site_profile._in_db, services, saved)
    except Exception:
        await failed("save_unknown")
    finally:
        if acquired and acquired.lease:
            await services.leases.release(str(user_id), acquired.lease.lease_id)
            await profile_leases.clear(services.leases, user_id, run_id)


async def site_profile_save(ctx: dict[str, Any], run_id: str, section: str, auth_id: str) -> None:
    from app.worker import apply_services

    services = ctx.get("site_profile_services") or configure(
        apply_services(ctx), ctx["settings"], ctx.get("redis")
    )
    await save_section(services, uuid.UUID(run_id), section, uuid.UUID(auth_id))
