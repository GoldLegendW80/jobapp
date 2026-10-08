"""Helpers of the identity API tests: a frozen clock, a client wrapper with explicit cookies,
and shortcuts to create users, links and sessions straight in the database."""

from datetime import datetime, timedelta
from http.cookies import Morsel, SimpleCookie
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.identity import security, service
from app.identity.deps import COOKIE_NAME
from app.identity.models import InviteKind, Role, User

PASSWORD = "correct horse battery 42"
CSRF = {"X-Requested-With": "jobapp"}


class Clock:
    """The app's clock, frozen until a test moves it."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


class Api:
    """TestClient wrapper: the cookie jar is never used, each call names its session token, and
    the CSRF header is sent unless `csrf=False`."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        self.client.cookies.clear()
        sent = dict(CSRF) if csrf else {}
        if token is not None:
            sent["Cookie"] = f"{COOKIE_NAME}={token}"
        sent.update(headers or {})
        response = self.client.request(method, path, headers=sent, **kwargs)
        self.client.cookies.clear()
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def login(self, email: str, password: str = PASSWORD, **kwargs: Any) -> httpx.Response:
        return self.post("/api/auth/login", json={"email": email, "password": password}, **kwargs)

    def login_token(self, email: str, password: str = PASSWORD) -> str:
        response = self.login(email, password)
        assert response.status_code == 200, response.text
        morsel = session_cookie(response)
        assert morsel is not None
        return morsel.value

    def me(self, token: str | None, **kwargs: Any) -> httpx.Response:
        return self.get("/api/auth/me", token=token, **kwargs)


def session_cookie(response: httpx.Response) -> Morsel[str] | None:
    """The jobapp_session Set-Cookie of a response, parsed, or None."""
    found = None
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        if COOKIE_NAME in jar:
            assert found is None, "two session cookies in one response"
            found = jar[COOKIE_NAME]
    return found


def make_user(
    factory: sessionmaker[Session],
    email: str = "harald@example.org",
    password: str = PASSWORD,
    role: Role = Role.admin,
    name: str = "Harald",
    active: bool = True,
    password_hash: str | None = None,
) -> User:
    with factory() as db, db.begin():
        user = User(
            email=email,
            name=name,
            password_hash=password_hash or security.hash_password(password),
            role=role,
            is_active=active,
        )
        db.add(user)
    return user


def issue_link(
    factory: sessionmaker[Session],
    email: str,
    kind: InviteKind = InviteKind.invite,
    role: Role = Role.member,
    now: datetime | None = None,
) -> str:
    with factory() as db, db.begin():
        return service.create_invite(db, email, kind, role=role, now=now).token
