"""/api/auth: sign in, sign out, who am I, and the invite or reset link a person opens.

The handlers are plain `def`: FastAPI runs them on its threadpool (`run_in_threadpool`), so the
argon2 work (about 50 ms per hash or verification) and the blocking database calls never stall
the event loop. Each handler owns one transaction and commits it itself.

Every failure of a link answers the same 404 body, and every failed sign in the same 401 body,
whatever the reason. Nothing here logs a password, a token or a cookie.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps, security, service
from app.identity.models import User
from app.identity.schemas import AcceptInviteIn, ErrorOut, InviteOut, LoginIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = "Incorrect email or password."
LINK_INVALID = "This link is not valid. It may have expired or been used already."

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in, or the credentials are wrong"}}
_404 = {404: {"model": ErrorOut, "description": "The link is not valid"}}
_422 = {422: {"model": ErrorOut, "description": "The input breaks a rule"}}


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


@router.post("/login", response_model=UserOut, responses=_401)
def login(body: LoginIn, request: Request, db: Db) -> User:
    user = service.authenticate(db, body.email, body.password)
    if user is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=LOGIN_FAILED)
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


@router.get("/invites/{token}", response_model=InviteOut, responses=_404)
def check_invite(token: str, request: Request, db: Db) -> InviteOut:
    invite = service.lookup_invite(db, token, deps.clock(request))
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=LINK_INVALID)
    return InviteOut(email=invite.email, kind=invite.kind, expires_at=invite.expires_at)


@router.post(
    "/invites/{token}/accept",
    response_model=UserOut,
    responses={**_404, **_422, 409: {"model": ErrorOut, "description": "Account exists"}},
)
def accept_invite(token: str, body: AcceptInviteIn, request: Request, db: Db) -> User:
    """Set a name and password from an invite, or a new password from a reset link, then sign
    in. A reset ends every earlier session of that user."""
    try:
        user = service.accept_invite(
            db, token, body.password, name=body.name, now=deps.clock(request)
        )
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
