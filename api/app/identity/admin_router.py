"""/api/admin: what only an admin may do. Invites (I4): list, create, revoke. Users (I5): list,
disable or enable, promote or demote, never leaving the app without an active admin.

Every route runs `require_admin` (router dependency), which answers 404 to a member, so a
member learns nothing about which admin routes exist; without a session the app-wide
default deny answers 401 first. Handlers are plain `def` on the threadpool and own their
transaction, like the auth router.

A new link's token is returned once, in the create answer, and nowhere else: the list never
carries it (the database holds only its hash), and nothing here logs it.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps, service
from app.identity.models import Invite, InviteKind, Role, User
from app.identity.schemas import (
    INVITES_PAGE_DEFAULT,
    INVITES_PAGE_MAX,
    USERS_PAGE_DEFAULT,
    USERS_PAGE_MAX,
    AdminInviteCreated,
    AdminInviteIn,
    AdminInviteOut,
    AdminInvitePage,
    AdminUserOut,
    AdminUserPage,
    AdminUserPatch,
    ErrorOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(deps.require_admin)])

ACCOUNT_EXISTS = "An account with this email already exists. Send a reset link instead."
NO_ACTIVE_ACCOUNT = "No active account uses this email."
ROLE_ON_RESET = "A reset link keeps the account's role: leave the role out."
INVITE_NOT_REVOCABLE = "No active invite has this id. It may have been used, revoked or expired."
USER_NOT_FOUND = "No user has this id."
LAST_ADMIN = service.LAST_ADMIN

Db = Annotated[Session, Depends(get_session)]
Admin = Annotated[User, Depends(deps.require_admin)]

_404 = {404: {"model": ErrorOut, "description": "Not an admin, or nothing to act on"}}


def _out(invite: Invite, creator: str | None, now: datetime) -> AdminInviteOut:
    return AdminInviteOut(
        id=invite.id,
        email=invite.email,
        kind=invite.kind,
        role=invite.role if invite.kind is InviteKind.invite else None,
        status=service.invite_status(invite, now),
        created_by_name=creator,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        used_at=invite.used_at,
        revoked_at=invite.revoked_at,
    )


@router.get("/invites", response_model=AdminInvitePage, responses=_404)
def list_invites(
    request: Request,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=INVITES_PAGE_MAX)] = INVITES_PAGE_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminInvitePage:
    """Every link, newest first, with its status as of now."""
    now = deps.clock(request)
    listing = service.list_invites(db, limit=limit, offset=offset)
    return AdminInvitePage(
        items=[_out(invite, creator, now) for invite, creator in listing.rows],
        total=listing.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/invites",
    response_model=AdminInviteCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_404,
        409: {"model": ErrorOut, "description": "An invite for an existing account"},
        422: {"model": ErrorOut, "description": "Invalid email, or a role on a reset link"},
    },
)
def create_invite(
    body: AdminInviteIn, request: Request, response: Response, db: Db, admin: Admin
) -> AdminInviteCreated:
    """A new invite (7 days) or reset (1 hour) link for an email. Any earlier unused link of the
    same kind for that email stops working. The answer is the only place the link appears."""
    try:
        email = service.normalize_email(body.email)
    except service.InvalidEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    user = service.get_user_by_email(db, email)
    if body.kind is InviteKind.invite:
        if user is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ACCOUNT_EXISTS)
        role = body.role or Role.member
    else:
        if body.role is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=ROLE_ON_RESET
            )
        # Unknown and disabled answer alike: a reset for a disabled account would only make a
        # link that accept refuses anyway.
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_ACTIVE_ACCOUNT)
        role = Role.member  # unused by a reset; the column needs a value
    now = deps.clock(request)
    issued = service.create_invite(db, email, body.kind, created_by=admin.id, role=role, now=now)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return AdminInviteCreated(invite=_out(issued.invite, admin.name, now), path=issued.path)


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_404,
)
def revoke_invite(invite_id: str, request: Request, db: Db) -> Response:
    """Revoke an active link. A used, revoked, expired or unknown one, or an id that is not a
    UUID, answers the same 404, so a second revoke is a 404 too."""
    try:
        parsed = UUID(invite_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INVITE_NOT_REVOCABLE
        ) from None
    if service.revoke_invite(db, parsed, deps.clock(request)) is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVITE_NOT_REVOCABLE)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- users -------------------------------------------------------------------------------------


def _user_out(user: User, sessions: int) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        sessions=sessions,
    )


@router.get("/users", response_model=AdminUserPage, responses=_404)
def list_users(
    request: Request,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=USERS_PAGE_MAX)] = USERS_PAGE_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminUserPage:
    """Every account sorted by name, with its signed in sessions and the count of active admins."""
    listing = service.list_users(db, limit=limit, offset=offset, now=deps.clock(request))
    return AdminUserPage(
        items=[_user_out(user, sessions) for user, sessions in listing.rows],
        total=listing.total,
        limit=limit,
        offset=offset,
        active_admins=listing.active_admins,
    )


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserOut,
    responses={
        **_404,
        409: {"model": ErrorOut, "description": "The last active admin"},
        422: {"model": ErrorOut, "description": "Nothing to change, or a bad value"},
    },
)
def update_user(
    user_id: str, body: AdminUserPatch, request: Request, db: Db, admin: Admin
) -> AdminUserOut:
    """Disable or enable an account and change its role. Disabling ends its sessions at once.
    The last active admin can be neither disabled nor demoted (409). Setting a value it already
    has is a 200 that changes nothing. An unknown id, or one that is not a UUID, is a 404."""
    try:
        parsed = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND) from None
    try:
        user = service.update_user(db, admin.id, parsed, is_active=body.is_active, role=body.role)
    except service.NotAnAdminError:
        db.rollback()  # demoted or disabled meanwhile: from now on a member, so the member 404
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found") from None
    except service.UnknownUserError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND) from None
    except service.LastAdminError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    sessions = service.count_live_sessions(db, user.id, deps.clock(request))
    db.commit()
    return _user_out(user, sessions)
