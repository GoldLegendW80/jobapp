"""All registered Apply routes cross the real session, CSRF and database owner boundary."""

import json
import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.apply.models import Application, SiteProfileRun
from app.browser.later_handoff import PREFIX
from app.browser.router import get_leases
from app.identity.deps import iter_route_contexts
from tests.apply.a17_support import JPEG, subject
from tests.apply.test_a10b_later import Store
from tests.apply.test_start import USER_A, USER_B, a, api, app, b, engine, factory, migrated_url
from tests.documents.conftest import load
from tests.identity.api_helpers import Api

__all__ = ["a", "api", "app", "b", "engine", "factory", "migrated_url"]


@dataclass(frozen=True)
class Case:
    method: str
    path: str
    body: dict | None = None
    foreign_status: int = 404
    # Collections/settings act only on the signed-in person, so have no foreign object to404.
    exception: str = ""


CASES = (
    Case("POST", "/api/postings/{posting_id}/applications", {"mode": "review"}),
    Case("GET", "/api/applications", foreign_status=200, exception="owner-filtered collection"),
    Case("GET", "/api/applications/{application_id}"),
    Case("GET", "/api/applications/{application_id}/approval"),
    Case("GET", "/api/applications/{application_id}/screenshots/{shot_id}"),
    Case("POST", "/api/applications/{application_id}/approve", {"answers_hash": "a" * 64}),
    Case(
        "PUT", "/api/applications/{application_id}/answers/{field_key:path}", {"value": "Example"}
    ),
    Case("POST", "/api/applications/{application_id}/reject"),
    Case("POST", "/api/applications/{application_id}/cancel"),
    Case("POST", "/api/applications/{application_id}/submitted"),
    Case("POST", "/api/applications/{application_id}/retry"),
    Case("GET", "/api/apply/settings", foreign_status=200, exception="authenticated settings"),
    Case("PUT", "/api/apply/settings", {"paused": True}, 200, "authenticated settings"),
    Case("GET", "/api/apply/caps", foreign_status=200, exception="authenticated caps"),
    Case("PUT", "/api/apply/caps", {"items": [], "revision": "a" * 64}, 409, "own stale revision"),
    Case("GET", "/api/apply/answer-bank", foreign_status=200, exception="authenticated bank"),
    Case(
        "PUT",
        "/api/apply/answer-bank",
        {"question_key": "missing", "answer": "Example"},
        404,
        "own missing bank entry",
    ),
    Case("POST", "/api/postings/{posting_id}/like"),
    Case("GET", "/api/apply/queue", foreign_status=200, exception="owner-filtered queue"),
    Case("GET", "/api/apply/queue/count", foreign_status=200, exception="owner-filtered count"),
    Case("POST", "/api/apply/queue/{application_id}/reject", {"skip_posting": False}),
    Case("POST", "/api/applications/{application_id}/rehearsal", {"answer": "yes"}),
    Case("GET", "/api/apply/summary", foreign_status=200, exception="owner-filtered summary"),
    Case("GET", "/api/applications/{application_id}/manual"),
    Case("POST", "/api/applications/{application_id}/manual/open"),
    Case("POST", "/api/applications/{application_id}/manual/actions", {"action": "attach_cv"}),
    Case("POST", "/api/applications/{application_id}/manual/record", {"seen": True}),
    Case("POST", "/api/applications/{application_id}/manual/close"),
    Case(
        "POST",
        "/api/site-profiles",
        {"url": "https://portal.example.org/profile"},
        409,
        "own live run on host",
    ),
    Case("GET", "/api/site-profiles", foreign_status=200, exception="owner-filtered collection"),
    Case("GET", "/api/site-profiles/{run_id}"),
    Case(
        "PUT",
        "/api/site-profiles/{run_id}/sections/{section}/fields/{key}",
        {"value": "Example", "replace": True},
    ),
    Case("POST", "/api/site-profiles/{run_id}/confirm-autosave"),
    Case("POST", "/api/site-profiles/{run_id}/cancel"),
    Case("GET", "/api/site-profiles/{run_id}/sections/{section}/screenshots/{shot_id}"),
    Case(
        "POST", "/api/site-profiles/{run_id}/sections/{section}/approve", {"values_hash": "a" * 64}
    ),
    Case("POST", "/api/site-profiles/{run_id}/sections/{section}/skip"),
    Case("GET", "/api/applications/{application_id}/email"),
    Case("GET", "/api/mail/drafts/{draft_id}"),
    Case(
        "POST",
        "/api/mail/drafts/{draft_id}/send",
        {"content_hash": "a" * 64, "confirmed_new_recipients": ["e2e-jobs@example.org"]},
    ),
    Case("POST", "/api/mail/sends/{send_id}/resolve", {"action": "sent"}),
    Case("POST", "/api/mail/sends/{send_id}/reconcile"),
    Case(
        "POST", "/api/browser/handoff/later", {"module": "application", "ref": "{application_id}"}
    ),
    Case(
        "POST",
        "/api/browser/handoff/later/done",
        {"module": "application", "ref": "{application_id}"},
    ),
    Case(
        "POST",
        "/api/browser/handoff/later/cancel",
        {"module": "application", "ref": "{application_id}"},
    ),
)

# Supplement the Apply registry with its linked document editor and bytes.
DOCUMENT_CASES = (
    Case("GET", "/api/documents/{document_id}"),
    Case("PUT", "/api/documents/{document_id}/draft", load("written/cv_typical.json")),
    Case("GET", "/api/documents/{document_id}/versions/{number}"),
    Case("GET", "/api/documents/{document_id}/versions/{number}/pdf"),
    Case("GET", "/api/documents/{document_id}/versions/{number}/page1.png"),
)


def registered(app):
    return {
        (method, context.path): context.original_route
        for context in iter_route_contexts(app.routes)
        if getattr(getattr(context.original_route, "endpoint", None), "__module__", "").startswith(
            "app.apply."
        )
        or getattr(getattr(context.original_route, "endpoint", None), "__module__", "")
        in {"app.mail.send_router", "app.browser.later_router"}
        for method in context.methods or ()
    }


def test_registered_routes_have_explicit_isolation_cases(app):
    expected = {(case.method, case.path) for case in CASES}
    assert len(expected) == len(CASES), "duplicate proof case"
    assert registered(app).keys() == expected, "a registered route needs an explicit privacy case"
    for case in CASES:
        if not any(
            part in case.path
            for part in (
                "{application_id}",
                "{posting_id}",
                "{run_id}",
                "{draft_id}",
                "{send_id}",
                "handoff/later",
            )
        ):
            assert case.exception, case


@pytest.fixture
def subjects(factory, a, b, app):
    with factory() as db:
        owner, other = subject(db, a), subject(db, b)
    # Only the external lease transport is fake; both authentication and owner lookups are real.
    store = Store()
    app.dependency_overrides[get_leases] = lambda: store
    return owner, other, store


def parameters(case, owner):
    ids = owner.ids()
    if case.path.startswith("/api/site-profiles/"):
        ids["shot_id"] = str(owner.profile_shot)
    path = case.path.replace("{field_key:path}", "{field_key}").format(**ids)
    body = {
        key: value.format(**ids) if isinstance(value, str) else value
        for key, value in (case.body or {}).items()
    }
    return path, body if case.body is not None else None


def private(response, code):
    assert response.status_code == code, response.text
    assert response.headers.get("cache-control") == "no-store", (code, response.headers)


@pytest.mark.parametrize(
    "case", [*CASES, *DOCUMENT_CASES], ids=lambda case: f"{case.method} {case.path}"
)
def test_registered_route_refuses_signed_out_foreign_and_csrf(api, app, subjects, case):
    owner, _, _ = subjects
    token_a, token_b = api.login_token(USER_A), api.login_token(USER_B)
    path, body = parameters(case, owner)
    kwargs = {"json": body, "headers": {"If-Match": "1"}}
    route = registered(app).get((case.method, case.path))
    if route is not None and route.body_field is not None:
        # A404 produced before parsing a bad body would conceal missing request coverage.
        _, errors = route.body_field.validate(body, {}, loc=("body",))
        assert not errors, errors
    private(api.request(case.method, path, **kwargs), 401)
    if case.method != "GET":
        private(api.request(case.method, path, token=token_a, csrf=False, **kwargs), 403)
    foreign = api.request(case.method, path, token=token_b, **kwargs)
    private(foreign, case.foreign_status)
    for value in (
        owner.application_id,
        owner.run_id,
        owner.draft_id,
        owner.send_id,
        owner.document_id,
    ):
        assert str(value) not in foreign.text
    if not case.exception:
        missing = {
            key: str(uuid.uuid4()) if key.endswith("_id") else value
            for key, value in owner.ids().items()
        }
        absent_path = case.path.replace("{field_key:path}", "{field_key}").format(**missing)
        absent_body = (
            {"module": "application", "ref": str(uuid.uuid4())} if "handoff/later" in path else body
        )
        absent = api.request(
            case.method, absent_path, token=token_b, json=absent_body, headers={"If-Match": "1"}
        )
        private(absent, 404)
        assert absent.json() == foreign.json()


def test_query_collection_and_bytes_bind_the_exact_owned_parent(api, factory, subjects):
    owner, other, _ = subjects
    token_a, token_b = api.login_token(USER_A), api.login_token(USER_B)
    for ref in (owner.posting_id, uuid.uuid4()):
        response = api.get(f"/api/applications?posting_id={ref}", token=token_b)
        private(response, 200)
        assert response.json() == {"items": []}
    paths = (
        f"/api/applications/{owner.application_id}/screenshots/{owner.shot_id}",
        f"/api/site-profiles/{owner.run_id}/sections/experience/screenshots/{owner.profile_shot}",
    )
    for path in paths:
        response = api.get(path, token=token_a)
        private(response, 200)
        assert response.content == JPEG
        assert response.headers["content-type"] == "image/jpeg"
    for path in (
        f"/api/applications/{owner.application_id}/screenshots/{other.shot_id}",
        f"/api/site-profiles/{owner.run_id}/sections/experience/screenshots/{other.profile_shot}",
        f"/api/site-profiles/{owner.run_id}/sections/missing/screenshots/{owner.profile_shot}",
    ):
        private(api.get(path, token=token_a), 404)
    pdf = api.get(f"/api/documents/{owner.document_id}/versions/1/pdf", token=token_a)
    private(pdf, 200)
    assert pdf.content.startswith(b"%PDF-")
    for path in (
        f"/api/applications/{owner.application_id}",
        f"/api/applications/{owner.application_id}/approval",
        f"/api/site-profiles/{owner.run_id}",
        f"/api/documents/{owner.document_id}",
    ):
        private(api.get(path, token=token_a), 200)
    email = api.get(f"/api/applications/{owner.application_id}/email", token=token_a)
    private(email, 200)
    draft = api.get(f"/api/mail/drafts/{owner.draft_id}", token=token_a)
    private(draft, 200)
    assert email.json() == draft.json()
    assert draft.json()["id"] == str(owner.draft_id)
    assert draft.json()["send_id"] == str(owner.send_id)
    with factory() as db:
        assert (
            db.scalar(sa.select(Application.user_id).where(Application.id == owner.application_id))
            == owner.user
        )
        assert (
            db.scalar(sa.select(SiteProfileRun.user_id).where(SiteProfileRun.id == owner.run_id))
            == owner.user
        )


@pytest.mark.parametrize("module", ["application", "site_profile"])
def test_later_callback_cannot_resume_another_persons_real_subject(
    api, app, factory, subjects, monkeypatch, module
):
    owner, other, store = subjects
    token_a, token_b = api.login_token(USER_A), api.login_token(USER_B)
    queued = []

    async def enqueue(*args, **kwargs):
        queued.append((args, kwargs))

    monkeypatch.setattr(app.state.queue, "enqueue", enqueue)
    ref = owner.application_id if module == "application" else owner.run_id
    body = {"module": module, "ref": str(ref)}
    private(api.post("/api/browser/handoff/later", token=token_a, json=body), 201)
    for suffix in ("", "/done", "/cancel"):
        private(api.post("/api/browser/handoff/later" + suffix, token=token_b, json=body), 404)
    # Even copied lease metadata cannot replace the callback's fresh database owner check.
    store.values[PREFIX + str(other.user)] = json.dumps(
        {"module": module, "ref": str(ref), "lease": store.lease.lease_id, "kind": "session"}
    )
    for suffix in ("/done", "/cancel"):
        private(api.post("/api/browser/handoff/later" + suffix, token=token_b, json=body), 404)
    assert not queued
    assert not store.released
    with factory() as db:
        model = Application if module == "application" else SiteProfileRun
        assert db.get(model, ref).needs == "signin"
    for _ in range(2):
        private(api.post("/api/browser/handoff/later/done", token=token_a, json=body), 204)
    assert len(queued) == 1
    assert queued[0][0][1] == str(ref)


def test_validation_and_method_errors_are_private(api, subjects):
    token = api.login_token(USER_A)
    for method, path, body, status in (
        ("POST", "/api/site-profiles", {"url": True}, 422),
        ("GET", "/api/site-profiles/not-a-uuid", None, 422),
        ("PUT", "/api/apply/settings", {"paused": "yes"}, 422),
        ("DELETE", "/api/apply/settings", None, 405),
    ):
        private(api.request(method, path, token=token, json=body), status)


def test_unhandled_private_failure_uses_the_same_cache_policy(app, subjects):
    # An exception leaves the middleware stack: the application's500 handler owns this header.
    @app.get("/api/apply/privacy-failure")
    def failed():
        raise RuntimeError("invented privacy test failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        probe = Api(client)
        token = probe.login_token(USER_A)
        response = probe.get("/api/apply/privacy-failure", token=token)
    private(response, 500)
    assert response.json() == {"detail": "internal server error"}
