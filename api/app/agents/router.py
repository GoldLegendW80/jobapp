"""/api/me/model-credential: the caller's own model credential (SPEC-agents.md, Credentials).

- GET answers {kind, last4, verified_at, updated_at}, or 404 when none is saved.
- PUT {kind, secret} stores it encrypted and unverified (replacing any earlier one): 422 with
  "secret: ..." when the value is not a credential of that kind, 429 over SAVE_LIMIT saves per
  WINDOW_S, 503 when the server has no CREDENTIALS_KEY.
- DELETE removes it: 204, also when there was none.
- POST /verify checks it for real (verify.py): 200 {result, message, credential} when the API
  accepted the key (verified_at set) or for a subscription token (result "unverified": checked
  on the first agent run); 400 when Anthropic refused it (verified_at cleared); 429 with
  Retry-After when Anthropic is busy or rate limiting, or over VERIFY_LIMIT checks per WINDOW_S;
  503 when Anthropic could not be reached; 404 without a credential; 409 when the stored
  ciphertext cannot be read with this server's keys.

The user always comes from the session (default deny, CSRF on every write, like every /api
route). No answer, error or log line carries the secret.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.agents import credentials
from app.agents.crypto import DecryptError, Keyring
from app.agents.ratelimit import RateLimiter
from app.agents.schemas import CredentialIn, CredentialOut, VerifyOut
from app.agents.verify import Outcome, Verifier
from app.db import get_session
from app.identity import deps, throttle
from app.identity.models import User
from app.identity.schemas import ErrorOut

router = APIRouter(prefix="/api/me/model-credential", tags=["agents"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

WINDOW_S = 15 * 60.0
SAVE_LIMIT = 10
VERIFY_LIMIT = 10
BUSY_RETRY_S = 30

NOT_FOUND = "No model credential is saved."
NOT_CONFIGURED = (
    "Model credentials cannot be stored on this server yet: its CREDENTIALS_KEY is not set."
)
UNREADABLE = (
    "The saved credential cannot be read with this server's encryption key. Paste it again."
)
CHANGED = "The credential was replaced while it was checked. Verify it again."
TOO_MANY_SAVES = "Too many credential changes."
TOO_MANY_CHECKS = "Too many checks."
INVALID = (
    "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one."
)
BUSY = "Anthropic is busy or rate limiting this key. Try again later."
UNAVAILABLE = "Anthropic could not be reached. Try again later."
VERIFIED = "Anthropic accepted this key."
UNVERIFIED = (
    "A subscription token cannot be checked from the server without running Claude Code. "
    "It is checked by the first agent run."
)

_404 = {404: {"model": ErrorOut, "description": "No credential saved"}}
_429 = {429: {"model": ErrorOut, "description": "Too many attempts; see Retry-After"}}
_503 = {503: {"model": ErrorOut, "description": "Not configured, or Anthropic unreachable"}}


def keyring_of(request: Request) -> Keyring:
    keyring: Keyring | None = request.app.state.credentials_keyring
    if keyring is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_CONFIGURED)
    return keyring


def _limit(limiter: RateLimiter, user: User, what: str) -> None:
    wait = limiter.take(user.id)
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{what} {throttle.wait_phrase(wait)}",
            headers={"Retry-After": str(wait)},
        )


def _changed_or_gone(db: Session, user: User) -> HTTPException:
    """The credential no longer is the one checked: 404 when removed, 409 when replaced."""
    if credentials.get(db, user.id) is None:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CHANGED)


@router.get("", response_model=CredentialOut, responses=_404)
def get_credential(user: CurrentUser, db: Db) -> CredentialOut:
    row = credentials.get(db, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return CredentialOut.model_validate(row)


@router.put(
    "",
    response_model=CredentialOut,
    responses={
        422: {"model": ErrorOut, "description": "secret: why it is refused"},
        **_429,
        **_503,
    },
)
def put_credential(
    body: CredentialIn, request: Request, user: CurrentUser, db: Db
) -> CredentialOut:
    keyring = keyring_of(request)
    try:
        secret = credentials.clean_secret(body.kind, body.secret.get_secret_value())
    except credentials.FormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"secret: {exc}"
        ) from None
    _limit(request.app.state.credential_saves, user, TOO_MANY_SAVES)
    row = credentials.save(db, keyring, user.id, body.kind, secret, deps.clock(request))
    db.commit()
    return CredentialOut.model_validate(row)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_credential(user: CurrentUser, db: Db) -> Response:
    credentials.delete(db, user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/verify",
    response_model=VerifyOut,
    responses={
        400: {"model": ErrorOut, "description": "Anthropic refused the credential"},
        **_404,
        409: {"model": ErrorOut, "description": "The stored ciphertext cannot be read"},
        **_429,
        **_503,
    },
)
def verify_credential(request: Request, user: CurrentUser, db: Db) -> VerifyOut:
    keyring = keyring_of(request)
    if credentials.get(db, user.id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    _limit(request.app.state.credential_checks, user, TOO_MANY_CHECKS)
    try:
        revealed = credentials.reveal(db, keyring, user.id)
    except DecryptError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=UNREADABLE) from None
    if revealed is None:  # removed in between
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    # The re-encryption of a row written by the previous key is kept whatever the check says.
    db.commit()
    checked_kind, checked_at = revealed.row.kind, revealed.row.updated_at
    verifier: Verifier = request.app.state.model_verifier
    result = verifier.check(checked_kind, revealed.secret)
    # Drops this function's reference only: Python cannot wipe a str from memory.
    del revealed
    if result.outcome in (Outcome.verified, Outcome.invalid):
        # One conditional UPDATE on the row exactly as it was checked: a credential saved or
        # removed while the check ran is never marked with this outcome.
        stamp = deps.clock(request) if result.outcome is Outcome.verified else None
        if not credentials.set_verified_if_unchanged(db, user.id, checked_kind, checked_at, stamp):
            db.rollback()
            raise _changed_or_gone(db, user)
        db.commit()
        if result.outcome is Outcome.invalid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID)
        row = credentials.get(db, user.id)
        if row is None:  # removed right after the update
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
        return VerifyOut(
            result="verified", message=VERIFIED, credential=CredentialOut.model_validate(row)
        )
    if result.outcome is Outcome.unverified:
        row = credentials.get(db, user.id)
        if row is None or row.updated_at != checked_at or row.kind is not checked_kind:
            raise _changed_or_gone(db, user)
        return VerifyOut(
            result="unverified", message=UNVERIFIED, credential=CredentialOut.model_validate(row)
        )
    if result.outcome is Outcome.busy:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=BUSY,
            headers={"Retry-After": str(result.retry_after or BUSY_RETRY_S)},
        )
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE)
