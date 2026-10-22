"""Real create_app request boundaries, owned field edits and screenshot isolation."""

import asyncio

from app.apply import site_profile
from app.profile import service as profiles
from tests.apply.a18_support import Portal, services
from tests.apply.conftest import AT
from tests.apply.test_start import USER_A, USER_B, a, api, app, b, engine, factory, migrated_url
from tests.profile.conftest import full

__all__ = ["a", "api", "app", "b", "engine", "factory", "migrated_url"]


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
    import uuid

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
    assert api.request("PUT", path, token=token, json={"replace": True}).status_code == 200
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
