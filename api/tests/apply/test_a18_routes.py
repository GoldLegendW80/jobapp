"""Real create_app request boundaries, owned field edits and screenshot isolation."""

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import profile_leases, profile_router, site_profile
from app.apply.models import (
    Authorisation,
    SiteProfileRun,
    SiteProfileShot,
    SiteRunState,
    SiteShotKind,
)
from app.profile import service as profiles
from tests.apply.a18_support import Portal, services
from tests.apply.conftest import AT
from tests.apply.test_start import USER_A, USER_B, a, api, app, b, engine, factory, migrated_url
from tests.profile.conftest import full

__all__ = ["a", "api", "app", "b", "engine", "factory", "migrated_url"]


@pytest.mark.parametrize("response", ["detail", "screenshot", "refusal"])
def test_profile_read_releases_its_lock_before_request_cleanup(factory, a, response):
    jpeg = b"\xff\xd8\xff" + bytes(range(64))
    with factory() as db:
        run = site_profile.start(db, a, "https://portal.example/profile", now=AT)
        run_id, shot_id = run.id, uuid.uuid4()
        db.add(
            SiteProfileShot(
                id=shot_id,
                run_id=run_id,
                user_id=a,
                section="1",
                kind=SiteShotKind.before,
                jpeg=jpeg,
                sha256=hashlib.sha256(jpeg).digest(),
                created_at=AT,
            )
        )
        db.commit()
    with factory() as reader:
        user = SimpleNamespace(id=a)
        if response == "detail":
            result = profile_router.get(run_id, user, reader)
            assert result.id == run_id
        elif response == "screenshot":
            result = profile_router.image(run_id, "1", shot_id, user, reader)
            assert result.body == jpeg
        else:
            result = asyncio.run(
                profile_router.confirm_autosave(run_id, SimpleNamespace(), user, reader)
            )
            assert result.status_code == 409
        # FastAPI's session dependency has not closed yet. A simultaneous decision
        # must still acquire the same run immediately, without waiting for cleanup.
        with factory() as writer:
            locked = writer.scalar(
                sa.select(SiteProfileRun)
                .where(SiteProfileRun.id == run_id, SiteProfileRun.user_id == a)
                .with_for_update(nowait=True)
            )
            assert locked.id == run_id


def test_site_profile_request_types_and_ownership(api, app, factory, a, b, monkeypatch):
    async def queued(*args, **kwargs):
        return True

    monkeypatch.setattr(app.state.queue, "enqueue", queued)
    token, other = api.login_token(USER_A), api.login_token(USER_B)
    assert (
        api.post("/api/site-profiles", json={"url": "https://portal.example/profile"}).status_code
        == 401
    )
    assert (
        api.post("/api/site-profiles", token=token, json={"url": "javascript:bad"}).status_code
        == 409
    )
    response = api.post(
        "/api/site-profiles", token=token, json={"url": "https://portal.example/profile"}
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    with factory() as db:
        profile = profiles.get_profile(db, a, AT)
        for name in ("identity", "experience", "skills", "answers"):
            profile = profiles.save_section(db, a, name, full(name), profile.revision, AT)
        db.commit()
    asyncio.run(site_profile.fill(services(factory, Portal()), uuid.UUID(run_id)))
    response = api.get(f"/api/site-profiles/{run_id}", token=token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    part = response.json()["sections"][0]
    name = next(row for row in part["fields"] if row["label"] == "Full name")
    path = f"/api/site-profiles/{run_id}/sections/{part['section']}/fields/{name['field_key']}"
    # Boolean is valid at the request union, but invalid for a text field, never a500.
    assert api.request("PUT", path, token=token, json={"value": True}).status_code == 409
    assert api.request("PUT", path, token=token, json={"value": {}}).status_code == 422
    assert api.request("PUT", path, token=token, json={"value": "x" * 10001}).status_code == 422
    edited = str(name["value"]).upper()
    updated = api.request("PUT", path, token=token, json={"value": edited, "replace": True})
    assert updated.status_code == 200, updated.text
    reread = api.get(f"/api/site-profiles/{run_id}", token=token).json()["sections"][0]
    persisted = next(row for row in reread["fields"] if row["field_key"] == name["field_key"])
    assert persisted["value"] == edited
    assert persisted["replace"] is True
    assert reread["values_hash"] == updated.json()["sections"][0]["values_hash"]
    assert reread["values_hash"] != part["values_hash"]
    assert api.get(f"/api/site-profiles/{run_id}", token=other).status_code == 404
    assert (
        api.request("PUT", path, token=other, json={"value": "Another person"}).status_code == 404
    )
    for operation in ("approve", "skip"):
        assert (
            api.post(
                f"/api/site-profiles/{run_id}/sections/{part['section']}/{operation}",
                token=other,
                json={"values_hash": part["values_hash"]} if operation == "approve" else None,
            ).status_code
            == 404
        )
    shot = part["screenshots"][0]
    assert (
        api.get(
            f"/api/site-profiles/{run_id}/sections/{part['section']}/screenshots/{shot['id']}",
            token=other,
        ).status_code
        == 404
    )


@pytest.mark.parametrize("operation", ["approve", "skip", "confirm-autosave", "cancel"])
def test_site_profile_decisions_persist_across_requests(
    api, app, factory, a, monkeypatch, operation
):
    async def queued(*args, **kwargs):
        return True

    monkeypatch.setattr(app.state.queue, "enqueue", queued)
    monkeypatch.setattr(profile_leases, "cancel_bound", queued)
    token = api.login_token(USER_A)
    made = api.post(
        "/api/site-profiles", token=token, json={"url": "https://portal.example/profile"}
    )
    assert made.status_code == 201, made.text
    run_id = made.json()["id"]
    with factory() as db:
        profile = profiles.get_profile(db, a, AT)
        for name in ("identity", "experience", "skills", "answers"):
            profile = profiles.save_section(db, a, name, full(name), profile.revision, AT)
        db.commit()
    asyncio.run(site_profile.fill(services(factory, Portal()), uuid.UUID(run_id)))
    path = f"/api/site-profiles/{run_id}"
    part = api.get(path, token=token).json()["sections"][0]
    suffix = f"sections/{part['section']}/{operation}"
    if operation == "confirm-autosave":
        with factory() as db:
            run = db.get(SiteProfileRun, uuid.UUID(run_id))
            run.state, run.needs, run.autosave = (
                SiteRunState.needs_person,
                "autosave_confirmation",
                True,
            )
            db.commit()
        suffix = operation
    elif operation == "cancel":
        suffix = operation
    body = {"values_hash": part["values_hash"]} if operation == "approve" else None
    response = api.post(f"{path}/{suffix}", token=token, json=body)
    assert response.status_code == 200, response.text
    persisted = api.get(path, token=token).json()
    if operation == "approve":
        assert persisted["sections"][0]["state"] == "approved"
        with factory() as db:
            auth = db.scalar(sa.select(Authorisation).where(Authorisation.site_run_id == run_id))
            assert bytes(auth.answers_hash).hex() == part["values_hash"]
    elif operation == "skip":
        assert persisted["sections"][0]["state"] == "skipped"
        assert persisted["state"] == "done"
    elif operation == "confirm-autosave":
        assert persisted["autosave_confirmed"] is True
        assert persisted["state"] == "queued"
        assert persisted["needs"] is None
    else:
        assert persisted["state"] == "cancelled"
