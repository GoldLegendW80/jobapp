"""Typed errors of the Google client (mail M1, `app/mail/google.py`).

An error carries a `code` of a closed list, an HTTP `status` when Google answered, a `reason`
(a word of this module's own closed list, `REASONS`) and `retry_after` in whole seconds when
Google gave one. It never carries Google's text, a token, an address or a byte of a mail: its
message is `google/<code>` and nothing else, so it is safe to log, to store and to put in an
arq result as it is.

`STORED_CODES` is the list "Google at the HTTP boundary" of tasks/mail/todo.md fixes for
`mail_accounts.error`; `stored_code(error)` folds every other code into `google_down`.
"""

from __future__ import annotations

# What `mail_accounts.error` may hold (the plan's fixed list).
STORED_CODES = frozenset(
    {"quota", "google_down", "revoked", "scope_missing", "history_expired", "too_large"}
)

# The words a `reason` may be. Google's own reason is mapped to one of them, never copied.
REASONS = frozenset(
    {
        "rate_limit",  # 429, or 403 rateLimitExceeded
        "user_rate_limit",  # 403 userRateLimitExceeded
        "daily_limit",  # 403 dailyLimitExceeded or quotaExceeded: waiting a few seconds is no use
        "local_budget",  # this client's own counter: half of Google's per user rate
        "server_error",  # 5xx
        "timeout",
        "unreachable",  # no connection, or one that broke
        "truncated",  # an answer that stops before its end
        "not_json",
        "shape",  # JSON, but not the object the call returns
        "refused",  # a 4xx this client has no name for
        "invalid_grant",
        "invalid_client",
        "bad_id",  # an id this client refuses to put in an address
        "page_token_repeated",
        "page_cap",
    }
)


class GoogleError(Exception):
    """A call to Google that gave no result."""

    code = "google_down"
    # True when the retry rule of the plan applies (429, 403 with a rate limit reason, 5xx).
    retryable = False

    def __init__(
        self,
        *,
        status: int | None = None,
        reason: str | None = None,
        retry_after: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(f"google/{self.code}")
        self.status = status if isinstance(status, int) else None
        self.reason = reason if reason in REASONS else None
        self.retry_after = retry_after if isinstance(retry_after, int) else None
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:
        return f"google/{self.code}"

    def __reduce__(self) -> tuple[object, ...]:
        # arq pickles what a job raises: the keyword only arguments need this to come back.
        return (_rebuild, (type(self), dict(self.__dict__)))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, status={self.status!r}, "
            f"reason={self.reason!r}, retry_after={self.retry_after!r})"
        )


def _rebuild(kind: type[GoogleError], state: dict[str, object]) -> GoogleError:
    error = kind.__new__(kind)
    Exception.__init__(error, f"google/{kind.code}")
    error.__dict__.update(state)
    return error


class QuotaError(GoogleError):
    """Google's rate limit or quota (429, 403 with a rate limit reason), or this client's own
    budget of quota units per user and per minute (`reason == "local_budget"`)."""

    code = "quota"


class GoogleDownError(GoogleError):
    """Google did not answer usefully: a 5xx, no connection, or an answer nobody can read."""

    code = "google_down"


class GoogleTimeoutError(GoogleDownError):
    """The connect or the read timeout of the call passed."""


class MalformedAnswerError(GoogleDownError):
    """A truncated answer, one that is not JSON, or JSON of another shape. `reason` says which."""


class RevokedError(GoogleError):
    """The grant no longer works: `invalid_grant` on a refresh, or a 401 right after one. The
    caller sets `needs_reconnect`."""

    code = "revoked"


class ScopeMissingError(GoogleError):
    """The user did not grant the scope this call needs."""

    code = "scope_missing"


class HistoryExpiredError(GoogleError):
    """`history.list` answered 404: the start id is too old, a bounded resync is due."""

    code = "history_expired"


class NotFoundError(GoogleError):
    """The message, thread or attachment does not exist (any more)."""

    code = "not_found"


class TooLargeError(GoogleError):
    """The answer is over the cap of its call. `limit` is that cap in bytes."""

    code = "too_large"

    def __init__(self, *, limit: int, status: int | None = None) -> None:
        super().__init__(status=status)
        self.limit = limit


class OAuthError(GoogleError):
    """The token endpoint refused an authorisation code or the client (not a refresh: a refused
    refresh is `RevokedError`)."""

    code = "oauth_refused"


class RealGoogleInTestError(RuntimeError):
    """A real Google address under APP_ENV=test, or a non Google one under production. Raised
    when the client is built, before any call."""


def stored_code(error: BaseException) -> str:
    """The word to store for `error` in `mail_accounts.error`: its code when the plan's list has
    it, else `google_down`."""
    code = getattr(error, "code", None)
    return code if code in STORED_CODES else "google_down"
