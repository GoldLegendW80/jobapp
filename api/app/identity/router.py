"""/api/auth: sign in, sign out, who am I, your own name and password, and the invite or reset
link a person opens.

The handlers are plain `def`: FastAPI runs them on its threadpool (`run_in_threadpool`), so the
argon2 work (about 50 ms per hash or verification) and the blocking database calls never stall
the event loop. Each handler owns one transaction and commits it itself.

Every failure of a link answers the same 404 body, and every failed sign in the same 401 body,
whatever the reason. Sign in and the current password of a password change are throttled
(`throttle`): over the limit they answer 429 with Retry-After before any argon2 work. The
argon2 work itself runs in a bounded number of slots (`security.password_slots`, issue #108):
a request that finds none free within the wait bound answers 503 with Retry-After, and its
reserved attempt is forgotten. Nothing here logs a password, a token or a cookie.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps, security, service, throttle
from app.identity.models import User
from app.identity.schemas import (
    AcceptInviteIn,
    ErrorOut,
    InviteOut,
    LoginIn,
    NameIn,
    PasswordChangeIn,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = "Incorrect email or password."
LINK_INVALID = "This link is not valid. It may have expired or been used already."

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
CurrentSession = Annotated[service.ActiveSession, Depends(deps.current_session)]

# Errors of the account routes name their field first, like the 422 of a malformed body
# ("new_password: String should have at most 1024 characters"), so the page can put each
# message under the right input: "<field>: <message>".
CURRENT_PASSWORD_WRONG = "current_password: The current password is incorrect."  # noqa: S105, a message

_401 = {401: {"model": ErrorOut, "description": "Not signed in, or the credentials are wrong"}}
_404 = {404: {"model": ErrorOut, "description": "The link is not valid"}}
_422 = {422: {"model": ErrorOut, "description": "The input breaks a rule"}}
_429 = {429: {"model": ErrorOut, "description": "Too many failed attempts; see Retry-After"}}

_503 = {503: {"model": ErrorOut, "description": "Busy checking passwords; see Retry-After"}}

SERVER_BUSY = "The server is busy. Try again in a moment."
LOGIN_THROTTLED = "Too many sign in attempts."
PASSWORD_THROTTLED = "Too many attempts with a wrong current password."  # noqa: S105, a message


def _too_many(exc: throttle.ThrottledError, what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"{what} {throttle.wait_phrase(exc.retry_after)}",
        headers={"Retry-After": str(exc.retry_after)},
    )


def _busy(exc: security.HashBusyError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=SERVER_BUSY,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _busy_attempt(db: Session, attempt: int, exc: security.HashBusyError) -> HTTPException:
    """No slot to verify the password of a reserved attempt: the attempt is forgotten (it
    tested nothing) and the answer is the 503."""
    db.rollback()
    throttle.forget(db, attempt)
    db.commit()
    return _busy(exc)


def _reserve_attempt(request: Request, db: Session, email: str, what: str) -> int:
    """Record a password attempt before verifying it (committed at once), or raise the 429."""
    try:
        attempt = throttle.reserve(
            db,
            email,
            deps.client_ip(request),
            deps.clock(request),
            getattr(request.app.state, "login_purger", None),
        )
    except throttle.ThrottledError as exc:
        db.rollback()
        raise _too_many(exc, what) from None
    db.commit()
    return attempt


def _start_session(request: Request, db: Session, user: User) -> None:
    """Open a session for `user`, commit, and send its cookie. A session the browser already
    held is ended first, so signing in again never leaves an orphan behind."""
    old = request.cookies.get(deps.COOKIE_NAME)
    if old:
        service.delete_session_by_token(db, old)
    issued = service.create_session(
        db,
        user,
        now=deps.clock(request),
        user_agent=request.headers.get("user-agent"),
        ip=deps.client_ip(request),
    )
    db.commit()
    deps.issue_cookie(request, issued.token)


@router.post("/login", response_model=UserOut, responses={**_401, **_429, **_503})
def login(body: LoginIn, request: Request, db: Db) -> User:
    """Sign in. Every attempt is recorded (unknown and disabled accounts too) and checked
    against the limits first: 5 failures per email (reset by a success) and 20 per client
    address in 15 minutes, else 429 with Retry-After in seconds."""
    attempt = _reserve_attempt(request, db, throttle.attempt_email(body.email), LOGIN_THROTTLED)
    # The slot is taken here, after the cheap refusals (body size, shape, throttle) and before
    # the account is looked up: whether the email has an account changes neither the wait nor
    # the 503, and no database connection is checked out while waiting.
    try:
        with security.password_slots.hold():
            user = service.authenticate(db, body.email, body.password)
    except security.HashBusyError as exc:
        raise _busy_attempt(db, attempt, exc) from None
    if user is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOGIN_FAILED)
    throttle.mark_success(db, attempt)
    user.last_login_at = deps.clock(request)
    _start_session(request, db, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(request: Request, db: Db) -> Response:
    """End this browser's session only. Without a session it still answers 204."""
    token = request.cookies.get(deps.COOKIE_NAME)
    if token:
        service.delete_session_by_token(db, token)
        db.commit()
    deps.clear_cookie(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, responses=_401)
def me(user: CurrentUser) -> User:
    return user


def _session_ended(request: Request, db: Session) -> HTTPException:
    """The session ended (or the account was disabled) while the request ran: drop the cookie
    and answer like any request without a session."""
    db.rollback()
    deps.clear_cookie(request)
    return deps.unauthorized()


@router.patch(
    "/me",
    response_model=UserOut,
    responses={**_401, 422: {"model": ErrorOut, "description": "name: the rule it breaks"}},
)
def rename_me(body: NameIn, request: Request, db: Db, active: CurrentSession) -> User:
    """Change your own display name: trimmed, NFC, whitespace runs collapsed, 1 to 100
    characters, no control characters (the invite page's rules and messages)."""
    try:
        user = service.rename_user(
            db, active.user.id, active.row.id, body.name, now=deps.clock(request)
        )
    except service.AcceptError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"name: {exc}"
        ) from None
    except service.SessionEndedError:
        raise _session_ended(request, db) from None
    db.commit()
    return user


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        **_401,
        400: {"model": ErrorOut, "description": "current_password: incorrect"},
        422: {"model": ErrorOut, "description": "new_password: the rule it breaks"},
        **_429,
        **_503,
    },
)
def change_password(
    body: PasswordChangeIn, request: Request, db: Db, active: CurrentSession
) -> Response:
    """Change your password. A wrong current password is a 400 (not 401: the session is fine
    and stays signed in), the new one must pass the invite rules and differ from the current
    one (422). On success every other session of the account ends; this one stays. 204.

    A wrong current password counts as a failed sign in of the account's email (and of the
    caller's address): after 5 in 15 minutes the route answers 429 before verifying anything,
    and so does the sign in of that email. A verified current password resets the count, even
    when the new password is then refused."""
    attempt = _reserve_attempt(request, db, active.user.email, PASSWORD_THROTTLED)
    try:
        # One slot for the verification and the new hash; nothing reads the database between
        # the commit of the attempt and the slot, so the wait holds no connection.
        with security.password_slots.hold():
            service.change_password(
                db,
                active.user.id,
                active.row.id,
                body.current_password,
                body.new_password,
                now=deps.clock(request),
            )
    except security.HashBusyError as exc:
        raise _busy_attempt(db, attempt, exc) from None
    except service.WrongPasswordError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CURRENT_PASSWORD_WRONG
        ) from None
    except security.PasswordRuleError as exc:
        db.rollback()
        throttle.mark_success(db, attempt)  # the current password was right
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"new_password: {exc}"
        ) from None
    except service.SessionEndedError:
        raise _session_ended(request, db) from None
    throttle.mark_success(db, attempt)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invites/{token}", response_model=InviteOut, responses=_404)
def check_invite(token: str, request: Request, db: Db) -> InviteOut:
    invite = service.lookup_invite(db, token, deps.clock(request))
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LINK_INVALID)
    return InviteOut(email=invite.email, kind=invite.kind, expires_at=invite.expires_at)


@router.post(
    "/invites/{token}/accept",
    response_model=UserOut,
    responses={**_404, **_422, **_503, 409: {"model": ErrorOut, "description": "Account exists"}},
)
def accept_invite(token: str, body: AcceptInviteIn, request: Request, db: Db) -> User:
    """Set a name and password from an invite, or a new password from a reset link, then sign
    in. A reset ends every earlier session of that user."""
    # A link that is not valid is refused before any slot is asked for, so a flood of made up
    # tokens costs no hash and takes no place in the queue; the rollback hands the connection
    # back before the wait.
    if service.lookup_invite(db, token, deps.clock(request)) is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LINK_INVALID)
    db.rollback()
    try:
        with security.password_slots.hold():
            user = service.accept_invite(
                db, token, body.password, name=body.name, now=deps.clock(request)
            )
    except security.HashBusyError as exc:
        db.rollback()
        raise _busy(exc) from None
    except service.InvalidLinkError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LINK_INVALID) from None
    except service.AccountExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except (service.AcceptError, security.PasswordRuleError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    _start_session(request, db, user)
    return user
