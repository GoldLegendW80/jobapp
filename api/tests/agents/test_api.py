"""/api/me/model-credential against a real Postgres, with a fake Anthropic API behind verify:
save, read back, replace, remove, format errors, isolation between users, CSRF, anonymous
calls, rate limits, every outcome of the check, rotation on read, a ciphertext moved to another
user, a credential replaced during its check, and the secret never in an answer or a log."""

import json
import uuid
from datetime import timedelta

import httpx
import httpx2
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents import router as agents_router
from app.agents import verify as agents_verify
from app.agents.crypto import Keyring
from app.agents.models import CredentialKind, ModelCredential
from app.agents.ratelimit import RateLimiter
from app.identity.deps import CSRF_REFUSED, UNAUTHORIZED
from app.identity.models import User
from tests.agents.conftest import (
    ALICE,
    API_KEY,
    BOB,
    OAUTH,
    OTHER_KEY,
    FakeAnthropic,
    build_app,
    client_for,
    new_key,
)
from tests.identity.api_helpers import Api, Clock

PATH = "/api/me/model-credential"
VERIFY = f"{PATH}/verify"


def put(api: Api, token: str | None, kind: str = "anthropic_api_key", secret: str = API_KEY, **kw):  # type: ignore[no-untyped-def]
    return api.request("PUT", PATH, token=token, json={"kind": kind, "secret": secret}, **kw)


def user_id(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    with factory() as db:
        return db.scalars(sa.select(User.id).where(User.email == email)).one()


def stored(factory: sessionmaker[Session], email: str) -> ModelCredential | None:
    with factory() as db:
        row = db.get(ModelCredential, user_id(factory, email))
        if row is not None:
            _ = row.secret_encrypted  # load the deferred column before the session closes
        return row


# --- save, read, replace, remove ----------------------------------------------------------------


def test_nothing_saved_is_404(api: Api, alice: str) -> None:
    response = api.get(PATH, token=alice)
    assert response.status_code == 404
    assert response.json() == {"detail": agents_router.NOT_FOUND}


def test_save_then_read_back_without_the_secret(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock, key: str
) -> None:
    response = put(api, alice)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"kind", "last4", "verified_at", "updated_at"}
    assert body["kind"] == "anthropic_api_key"
    assert body["last4"] == API_KEY[-4:]
    assert body["verified_at"] is None
    assert API_KEY not in response.text
    got = api.get(PATH, token=alice)
    assert got.status_code == 200
    assert got.json() == body
    assert API_KEY not in got.text

    row = stored(factory, ALICE)
    assert row is not None
    assert API_KEY.encode() not in row.secret_encrypted
    assert row.key_version == Keyring.from_text(key).version
    assert row.created_at == row.updated_at == clock.now
    # What is stored decrypts to the secret with the server's key, bound to Alice and the kind.
    opened = Keyring.from_text(key).decrypt(
        row.secret_encrypted, row.key_version, row.user_id, row.kind.value
    )
    assert opened.plaintext == API_KEY


def test_surrounding_whitespace_is_trimmed(api: Api, alice: str) -> None:
    response = put(api, alice, secret=f"  {API_KEY}\n")
    assert response.status_code == 200
    assert response.json()["last4"] == API_KEY[-4:]


def test_subscription_token_is_saved(api: Api, alice: str) -> None:
    response = put(api, alice, kind="claude_subscription", secret=OAUTH)
    assert response.status_code == 200
    assert response.json()["kind"] == "claude_subscription"
    assert response.json()["last4"] == OAUTH[-4:]


def test_replace_keeps_one_row_and_resets_verification(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    put(api, alice)
    assert api.post(VERIFY, token=alice).status_code == 200
    first = stored(factory, ALICE)
    assert first is not None
    assert first.verified_at is not None
    clock.advance(minutes=5)
    response = put(api, alice, kind="claude_subscription", secret=OAUTH)
    assert response.status_code == 200
    assert response.json()["verified_at"] is None
    second = stored(factory, ALICE)
    assert second is not None
    assert second.kind is CredentialKind.claude_subscription
    assert second.created_at == first.created_at
    assert second.updated_at == clock.now
    assert second.secret_encrypted != first.secret_encrypted
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(ModelCredential)) == 1


def test_delete_is_idempotent(api: Api, alice: str, factory: sessionmaker[Session]) -> None:
    put(api, alice)
    assert api.request("DELETE", PATH, token=alice).status_code == 204
    assert stored(factory, ALICE) is None
    assert api.get(PATH, token=alice).status_code == 404
    again = api.request("DELETE", PATH, token=alice)
    assert again.status_code == 204
    assert again.content == b""


def test_deleting_the_user_deletes_the_credential(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    put(api, alice)
    with factory() as db, db.begin():
        db.execute(sa.delete(User).where(User.email == ALICE))
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(ModelCredential)) == 0


# --- format -------------------------------------------------------------------------------------

LONG = "sk-ant-api03-" + "a" * 400


@pytest.mark.parametrize(
    ("kind", "secret", "message"),
    [
        ("anthropic_api_key", "", "Paste the token or key."),
        ("anthropic_api_key", "   \n", "Paste the token or key."),
        ("anthropic_api_key", API_KEY[:30] + " " + API_KEY[30:], "Remove the spaces"),
        ("anthropic_api_key", "sk-proj-" + "a" * 60, "not an Anthropic credential"),
        ("anthropic_api_key", "a" * 100, "not an Anthropic credential"),
        ("anthropic_api_key", OAUTH, "This is a Claude subscription token"),
        ("anthropic_api_key", "sk-ant-sid01-" + "a" * 60, "not an Anthropic API key"),
        ("claude_subscription", API_KEY, "This is an Anthropic API key"),
        ("anthropic_api_key", "sk-ant-admin01-" + "a" * 60, "Admin API key"),
        ("claude_subscription", "sk-ant-admin01-" + "a" * 60, "Admin API key"),
        ("anthropic_api_key", "sk-ant-api03-short", "too short"),
        ("anthropic_api_key", LONG, "too long"),
        ("anthropic_api_key", "sk-ant-api03-" + "a" * 40 + "/+=", "Only letters"),
        ("anthropic_api_key", "sk-ant-api03-" + "é" * 50, "Only letters"),
        ("claude_subscription", "sk-ant-oat01-" + "a" * 40 + "\x00", "Only letters"),
    ],
)
def test_format_errors_name_the_field_and_never_echo(
    api: Api, alice: str, factory: sessionmaker[Session], kind: str, secret: str, message: str
) -> None:
    response = put(api, alice, kind=kind, secret=secret)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("secret: ")
    assert message in detail
    if secret.strip():
        assert secret.strip() not in response.text
        assert secret.strip()[-8:] not in response.text
    assert stored(factory, ALICE) is None


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "anthropic_api_key"},
        {"secret": API_KEY},
        {"kind": "openai", "secret": API_KEY},
        {"kind": "anthropic_api_key", "secret": API_KEY, "user_id": str(uuid.uuid4())},
        {"kind": "anthropic_api_key", "secret": 12345},
        {"kind": "anthropic_api_key", "secret": "sk-ant-api03-" + "q" * 5000},
    ],
)
def test_malformed_bodies_are_422_without_echo(
    api: Api, alice: str, body: dict[str, object]
) -> None:
    response = api.request("PUT", PATH, token=alice, json=body)
    assert response.status_code == 422
    assert API_KEY not in response.text
    assert "qqqqqqqq" not in response.text


def test_body_that_is_not_json(api: Api, alice: str) -> None:
    response = api.request(
        "PUT", PATH, token=alice, content=API_KEY, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert API_KEY not in response.text


# --- access ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("GET", PATH), ("PUT", PATH), ("DELETE", PATH), ("POST", VERIFY)]
)
def test_anonymous_is_401(api: Api, method: str, path: str) -> None:
    response = api.request(method, path, json={"kind": "anthropic_api_key", "secret": API_KEY})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


@pytest.mark.parametrize(("method", "path"), [("PUT", PATH), ("DELETE", PATH), ("POST", VERIFY)])
def test_writes_need_the_csrf_header(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    anthropic_api: FakeAnthropic,
    method: str,
    path: str,
) -> None:
    put(api, alice)
    before = stored(factory, ALICE)
    response = api.request(
        method, path, token=alice, csrf=False, json={"kind": "claude_subscription", "secret": OAUTH}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    after = stored(factory, ALICE)
    assert after is not None
    assert before is not None
    assert after.secret_encrypted == before.secret_encrypted
    assert anthropic_api.requests == []


def test_users_never_see_or_touch_each_other(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    put(api, alice)
    assert api.get(PATH, token=bob).status_code == 404
    assert api.post(VERIFY, token=bob).status_code == 404
    assert anthropic_api.requests == []
    assert api.request("DELETE", PATH, token=bob).status_code == 204
    assert stored(factory, ALICE) is not None
    put(api, bob, secret=OTHER_KEY)
    assert api.get(PATH, token=alice).json()["last4"] == API_KEY[-4:]
    assert api.get(PATH, token=bob).json()["last4"] == OTHER_KEY[-4:]
    # Alice's verify sends Alice's key, never Bob's.
    assert api.post(VERIFY, token=alice).status_code == 200
    assert anthropic_api.requests[-1].headers["x-api-key"] == API_KEY
    assert api.request("DELETE", PATH, token=alice).status_code == 204
    assert api.get(PATH, token=bob).status_code == 200


# --- not configured -----------------------------------------------------------------------------


def test_without_a_key_saving_and_checking_answer_503(
    migrated_url: str, clock: Clock, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    from tests.identity.api_helpers import make_user

    make_user(factory, email=ALICE, name="Alice")
    app = build_app(migrated_url, clock, anthropic_api, key=None)
    for api in client_for(app):
        token = api.login_token(ALICE)
        response = put(api, token)
        assert response.status_code == 503
        assert response.json() == {"detail": agents_router.NOT_CONFIGURED}
        assert api.post(VERIFY, token=token).status_code == 503
        assert api.get(PATH, token=token).status_code == 404
        assert api.request("DELETE", PATH, token=token).status_code == 204


# --- rate limits --------------------------------------------------------------------------------


def test_saves_are_limited_per_user(app: FastAPI, api: Api, alice: str, bob: str) -> None:
    now = [0.0]
    app.state.credential_saves = RateLimiter(2, 60.0, clock=lambda: now[0])
    assert put(api, alice).status_code == 200
    assert put(api, alice).status_code == 200
    refused = put(api, alice)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.json()["detail"] == "Too many credential changes. Try again in 1 minute."
    assert put(api, bob).status_code == 200  # another user has their own count
    # A format error is refused before the count: it never uses up an attempt.
    assert put(api, alice, secret="nope").status_code == 422
    now[0] = 60.0
    assert put(api, alice).status_code == 200


def test_checks_are_limited_per_user(
    app: FastAPI, api: Api, alice: str, anthropic_api: FakeAnthropic
) -> None:
    now = [0.0]
    app.state.credential_checks = RateLimiter(1, 900.0, clock=lambda: now[0])
    put(api, alice)
    assert api.post(VERIFY, token=alice).status_code == 200
    refused = api.post(VERIFY, token=alice)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "900"
    assert refused.json()["detail"] == "Too many checks. Try again in 15 minutes."
    assert len(anthropic_api.requests) == 1


def test_rate_limiter_forgets_idle_users() -> None:
    now = [0.0]
    limiter = RateLimiter(1, 10.0, clock=lambda: now[0])
    first, second = uuid.uuid4(), uuid.uuid4()
    assert limiter.take(first) is None
    assert limiter.take(first) == 10
    now[0] = 10.0
    assert limiter.take(second) is None
    assert set(limiter._calls) == {second}


# --- verify -------------------------------------------------------------------------------------


def test_verify_without_credential_is_404(
    api: Api, alice: str, anthropic_api: FakeAnthropic
) -> None:
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 404
    assert anthropic_api.requests == []


def test_verify_accepted_key(
    api: Api, alice: str, anthropic_api: FakeAnthropic, clock: Clock, factory: sessionmaker[Session]
) -> None:
    put(api, alice)
    clock.advance(seconds=30)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "verified"
    assert body["message"] == agents_router.VERIFIED
    assert body["credential"]["last4"] == API_KEY[-4:]
    assert body["credential"]["verified_at"] is not None
    assert API_KEY not in response.text
    row = stored(factory, ALICE)
    assert row is not None
    assert row.verified_at == clock.now
    # The cheapest real call: one model listing with the key in X-Api-Key, nothing else.
    [sent] = anthropic_api.requests
    assert sent.method == "GET"
    assert sent.url.path == "/v1/models"
    assert sent.url.params["limit"] == "1"
    assert sent.url.host == "api.anthropic.com"
    assert sent.headers["x-api-key"] == API_KEY
    assert "authorization" not in sent.headers
    assert api.get(PATH, token=alice).json()["verified_at"] is not None


def test_verify_hides_the_credential_it_decrypted_from_the_logs(
    app: FastAPI, api: Api, alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library under the check that logs the credential (an HTTP client logging a header):
    with the shape turned off, only the registration at the place of decryption hides it."""
    import io
    import logging
    import re

    from app import logging_config

    monkeypatch.setattr(logging_config, "SHAPE", re.compile(r"(?!)"))
    seen: list[str] = []

    class Loud:
        def check(self, kind: CredentialKind, secret: str) -> agents_verify.Result:
            logging.getLogger("httpx").error("HTTP Request x-api-key=%s", secret)
            seen.append(logging_config.clean_text(secret))
            return agents_verify.Result(agents_verify.Outcome.unverified)

    app.state.model_verifier = Loud()
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logging_config.JsonFormatter())
    handler.addFilter(logging_config.SecretFilter())
    logging.getLogger().addHandler(handler)
    try:
        put(api, alice)
        assert api.post(VERIFY, token=alice).status_code == 200
    finally:
        logging.getLogger().removeHandler(handler)
    assert seen == [logging_config.REDACTED]
    assert API_KEY not in out.getvalue()
    assert "x-api-key=[redacted]" in out.getvalue()
    assert logging_config.clean_text(API_KEY) == API_KEY  # and nothing stays hidden after


def test_subscription_token_is_stored_unverified_without_any_call(
    api: Api, alice: str, anthropic_api: FakeAnthropic
) -> None:
    put(api, alice, kind="claude_subscription", secret=OAUTH)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "unverified"
    assert body["message"] == agents_router.UNVERIFIED
    assert body["credential"]["verified_at"] is None
    assert anthropic_api.requests == []


@pytest.mark.parametrize("status", [401, 403])
def test_refused_key_is_400_and_clears_verification(
    api: Api, alice: str, anthropic_api: FakeAnthropic, factory: sessionmaker[Session], status: int
) -> None:
    put(api, alice)
    assert api.post(VERIFY, token=alice).status_code == 200
    anthropic_api.answer(status)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 400
    assert response.json() == {"detail": agents_router.INVALID}
    row = stored(factory, ALICE)
    assert row is not None
    assert row.verified_at is None


@pytest.mark.parametrize(
    ("status", "headers", "retry_after"),
    [
        (429, {"retry-after": "7"}, "7"),
        (429, {"retry-after": "2.2"}, "3"),
        (429, {}, "30"),
        (429, {"retry-after": "soon"}, "30"),
        (429, {"retry-after": "-4"}, "30"),
        (429, {"retry-after": "99999"}, "3600"),
        (529, {}, "30"),
        (503, {}, "30"),
    ],
)
def test_busy_is_429_try_again_later(
    api: Api,
    alice: str,
    anthropic_api: FakeAnthropic,
    factory: sessionmaker[Session],
    status: int,
    headers: dict[str, str],
    retry_after: str,
) -> None:
    put(api, alice)
    anthropic_api.answer(status, headers)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 429
    assert response.json() == {"detail": agents_router.BUSY}
    assert response.headers["Retry-After"] == retry_after
    assert len(anthropic_api.requests) == 1  # never retried
    row = stored(factory, ALICE)
    assert row is not None
    assert row.verified_at is None


@pytest.mark.parametrize("status", [400, 404, 500, 502, 504])
def test_other_upstream_statuses_are_503(
    api: Api, alice: str, anthropic_api: FakeAnthropic, status: int
) -> None:
    put(api, alice)
    anthropic_api.answer(status)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 503
    assert response.json() == {"detail": agents_router.UNAVAILABLE}


@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ConnectError("connection refused"),
        httpx2.ConnectTimeout("connect timeout"),
        httpx2.ReadTimeout("read timeout"),
        httpx2.RemoteProtocolError("peer closed"),
    ],
)
def test_network_failures_are_503(
    api: Api, alice: str, anthropic_api: FakeAnthropic, exc: Exception
) -> None:
    put(api, alice)
    anthropic_api.fail(exc)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 503
    assert response.json() == {"detail": agents_router.UNAVAILABLE}


def test_unreadable_answer_is_503(api: Api, alice: str, anthropic_api: FakeAnthropic) -> None:
    put(api, alice)
    anthropic_api.handler = lambda _: httpx2.Response(200, content=b"<html>proxy</html>")
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 503
    assert response.json() == {"detail": agents_router.UNAVAILABLE}


def test_verify_timeout_is_bounded(app: FastAPI) -> None:
    assert app.state.model_verifier.timeout_s == 10.0


# --- rotation, moved ciphertexts, concurrent replace ---------------------------------------------


def test_verify_re_encrypts_a_row_of_the_previous_key(
    migrated_url: str,
    clock: Clock,
    anthropic_api: FakeAnthropic,
    factory: sessionmaker[Session],
    api: Api,
    alice: str,
    key: str,
) -> None:
    put(api, alice)
    old_version = Keyring.from_text(key).version
    new = new_key()
    rotated = build_app(migrated_url, clock, anthropic_api, key=new, previous=key)
    for second in client_for(rotated):
        token = second.login_token(ALICE)
        assert second.post(VERIFY, token=token).status_code == 200
    row = stored(factory, ALICE)
    assert row is not None
    assert row.key_version == Keyring.from_text(new).version != old_version
    assert anthropic_api.requests[-1].headers["x-api-key"] == API_KEY
    # The previous key can now go: the row reads with the new key alone.
    only_new = build_app(migrated_url, clock, anthropic_api, key=new)
    for third in client_for(only_new):
        token = third.login_token(ALICE)
        assert third.post(VERIFY, token=token).status_code == 200


def test_row_of_an_unknown_key_is_409_and_kept(
    migrated_url: str,
    clock: Clock,
    anthropic_api: FakeAnthropic,
    factory: sessionmaker[Session],
    api: Api,
    alice: str,
) -> None:
    put(api, alice)
    before = stored(factory, ALICE)
    other = build_app(migrated_url, clock, anthropic_api, key=new_key())
    for second in client_for(other):
        token = second.login_token(ALICE)
        response = second.post(VERIFY, token=token)
        assert response.status_code == 409
        assert response.json() == {"detail": agents_router.UNREADABLE}
        assert second.get(PATH, token=token).status_code == 200
    after = stored(factory, ALICE)
    assert before is not None
    assert after is not None
    assert after.secret_encrypted == before.secret_encrypted
    assert anthropic_api.requests == []


def test_ciphertext_copied_to_another_user_does_not_decrypt(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    put(api, alice)
    put(api, bob, secret=OTHER_KEY)
    alice_row = stored(factory, ALICE)
    assert alice_row is not None
    with factory() as db, db.begin():
        db.execute(
            sa.update(ModelCredential)
            .where(ModelCredential.user_id == user_id(factory, BOB))
            .values(secret_encrypted=alice_row.secret_encrypted, key_version=alice_row.key_version)
        )
    response = api.post(VERIFY, token=bob)
    assert response.status_code == 409
    assert anthropic_api.requests == []


def test_ciphertext_relabelled_as_the_other_kind_does_not_decrypt(
    api: Api, alice: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    put(api, alice)
    with factory() as db, db.begin():
        db.execute(sa.update(ModelCredential).values(kind=CredentialKind.claude_subscription))
    assert api.post(VERIFY, token=alice).status_code == 409
    assert anthropic_api.requests == []


def test_tampered_row_is_409(
    api: Api, alice: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    put(api, alice)
    row = stored(factory, ALICE)
    assert row is not None
    flipped = bytes([row.secret_encrypted[0] ^ 1]) + row.secret_encrypted[1:]
    with factory() as db, db.begin():
        db.execute(sa.update(ModelCredential).values(secret_encrypted=flipped))
    assert api.post(VERIFY, token=alice).status_code == 409
    assert anthropic_api.requests == []


def test_replaced_during_the_check_is_409(
    api: Api, alice: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic, clock: Clock
) -> None:
    put(api, alice)
    uid = user_id(factory, ALICE)

    def replace_then_refuse(_: httpx2.Request) -> httpx2.Response:
        with factory() as db, db.begin():
            db.execute(
                sa.update(ModelCredential)
                .where(ModelCredential.user_id == uid)
                .values(updated_at=clock.now + timedelta(seconds=1))
            )
        return httpx2.Response(401, json={})

    anthropic_api.handler = replace_then_refuse
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 409
    assert response.json() == {"detail": agents_router.CHANGED}


@pytest.mark.parametrize("upstream", [200, 401])
def test_save_committed_between_check_and_set_is_409(
    api: Api,
    alice: str,
    factory: sessionmaker[Session],
    anthropic_api: FakeAnthropic,
    clock: Clock,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    upstream: int,
) -> None:
    """A PUT that commits after the outbound check and before its outcome is written: the
    conditional UPDATE matches no row, the new credential keeps verified_at as saved."""
    put(api, alice)
    uid = user_id(factory, ALICE)
    if upstream != 200:
        anthropic_api.answer(upstream)
    original = credentials.set_verified_if_unchanged

    def save_first(*args: object, **kwargs: object) -> bool:
        with factory() as other, other.begin():
            credentials.save(
                other,
                Keyring.from_text(key),
                uid,
                CredentialKind.anthropic_api_key,
                OTHER_KEY,
                clock.now + timedelta(seconds=1),
            )
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(credentials, "set_verified_if_unchanged", save_first)
    response = api.post(VERIFY, token=alice)
    assert response.status_code == 409
    assert response.json() == {"detail": agents_router.CHANGED}
    row = stored(factory, ALICE)
    assert row is not None
    assert row.last4 == OTHER_KEY[-4:]
    assert row.verified_at is None


def test_set_verified_if_unchanged_matches_user_kind_and_time(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    put(api, alice)
    uid = user_id(factory, ALICE)
    kind = CredentialKind.anthropic_api_key
    with factory() as db, db.begin():
        assert not credentials.set_verified_if_unchanged(
            db, uid, CredentialKind.claude_subscription, clock.now, clock.now
        )
        assert not credentials.set_verified_if_unchanged(
            db, uid, kind, clock.now - timedelta(seconds=1), clock.now
        )
        assert not credentials.set_verified_if_unchanged(
            db, uuid.uuid4(), kind, clock.now, clock.now
        )
    row = stored(factory, ALICE)
    assert row is not None
    assert row.verified_at is None
    with factory() as db, db.begin():
        assert credentials.set_verified_if_unchanged(db, uid, kind, clock.now, clock.now)
    row = stored(factory, ALICE)
    assert row is not None
    assert row.verified_at == clock.now


def test_removed_during_the_check_is_404(
    api: Api, alice: str, factory: sessionmaker[Session], anthropic_api: FakeAnthropic
) -> None:
    put(api, alice)

    def remove(_: httpx2.Request) -> httpx2.Response:
        with factory() as db, db.begin():
            db.execute(sa.delete(ModelCredential))
        return httpx2.Response(200, json={"data": []})

    anthropic_api.handler = remove
    assert api.post(VERIFY, token=alice).status_code == 404


# --- the secret never leaks -----------------------------------------------------------------------


def _all_logged(capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> str:
    records = "\n".join(
        f"{r.name} {r.getMessage()} {json.dumps(vars(r), default=str)}" for r in caplog.records
    )
    return capsys.readouterr().out + "\n" + records


def test_secret_is_never_logged(
    app: FastAPI,
    api: Api,
    alice: str,
    anthropic_api: FakeAnthropic,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    import logging

    logging.getLogger().setLevel("DEBUG")  # the JSON handler on stdout too, at its most verbose
    put(api, alice)
    api.post(VERIFY, token=alice)
    for status in (401, 429, 500):
        anthropic_api.answer(status)
        api.post(VERIFY, token=alice)
    anthropic_api.fail(httpx2.ConnectError("refused"))
    api.post(VERIFY, token=alice)
    put(api, alice, secret=API_KEY + " x")  # a format error
    put(api, alice, kind="claude_subscription", secret=API_KEY)  # the wrong kind
    put(api, alice, kind="claude_subscription", secret=OAUTH)
    api.post(VERIFY, token=alice)
    api.request("DELETE", PATH, token=alice)
    logged = _all_logged(capsys, caplog)
    assert "model credential saved" in logged  # the capture works
    for secret in (API_KEY, OAUTH):
        assert secret not in logged
        assert secret[:-4] not in logged
        assert secret[13:40] not in logged


def test_unhandled_error_logs_no_secret(
    app: FastAPI,
    api: Api,
    alice: str,
    anthropic_api: FakeAnthropic,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bug inside the check (not an upstream answer) goes through the app's 500 handler,
    which logs the traceback: it carries no local values, so no secret."""
    put(api, alice)

    class Broken:
        def check(self, kind: CredentialKind, secret: str) -> None:
            raise RuntimeError("boom")

    app.state.model_verifier = Broken()
    with pytest.raises(RuntimeError):
        api.post(VERIFY, token=alice)
    logged = _all_logged(capsys, caplog)
    assert "boom" in logged
    assert API_KEY not in logged


def test_openapi_never_exposes_a_secret_field(api: Api) -> None:
    response: httpx.Response = api.client.get("/openapi.json")
    schemas = response.json()["components"]["schemas"]
    for name in ("CredentialOut", "VerifyOut"):
        assert "secret" not in json.dumps(schemas[name])
    assert schemas["CredentialIn"]["properties"]["secret"]["format"] == "password"
