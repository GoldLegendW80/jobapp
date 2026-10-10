"""The real check behind POST /api/me/model-credential/verify.

Anthropic API key: one `GET /v1/models?limit=1` through the official `anthropic` SDK
(`client.models.list`). It costs no tokens, needs no model choice, and answers 401 for a key
that is wrong or revoked. No retry (max_retries=0): the person clicked Verify and waits for the
answer; a busy API is reported as such. Outcomes:
- 2xx: verified;
- 401, 403: invalid (wrong, revoked, or a key without access);
- 429, 529 (overloaded), 503: busy, try again later (Retry-After passed on when sent);
- timeout, connection error, any other status: unavailable.

Claude subscription token (`claude setup-token`): not checked here, by decision (agents A1,
recorded in SPEC-agents.md, Credentials). Claude Code documents one use for that token: the
CLAUDE_CODE_OAUTH_TOKEN variable of the Claude Code CLI, which "can only make model requests".
There is no documented lightweight endpoint to validate it, calling the Messages API with it
directly is not a documented or supported use, and the CLI only arrives in the image with A2.
So the token is stored as unverified, the answer says why, and the first real agent run (A2)
is its verification.

Tests replace the network with `transport` (an httpx2 transport); MODEL_API_BASE_URL (refused
in production) points the SDK at a stub for the end to end journey. Nothing here logs the
secret, and the SDK's own logger is kept at WARNING so its debug output (request options) never
reaches the logs.
"""

import enum
import logging
import math
from dataclasses import dataclass

import anthropic
import httpx2

from app.agents.models import CredentialKind

ANTHROPIC_API = "https://api.anthropic.com"
TIMEOUT_S = 10.0

logger = logging.getLogger(__name__)
# The SDK logs request options at debug level; they carry the X-Api-Key header.
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


class Outcome(enum.StrEnum):
    verified = "verified"
    unverified = "unverified"  # a subscription token: checked on the first agent run
    invalid = "invalid"
    busy = "busy"
    unavailable = "unavailable"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    retry_after: int | None = None


_BUSY = frozenset({429, 503, 529})
_INVALID = frozenset({401, 403})


def _retry_after(response: httpx2.Response | None) -> int | None:
    if response is None:
        return None
    raw = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return max(1, min(3600, math.ceil(seconds)))


@dataclass
class Verifier:
    """One per app. `base_url` is settings.model_api_base_url (a stub, tests only)."""

    base_url: str | None = None
    timeout_s: float = TIMEOUT_S
    transport: httpx2.BaseTransport | None = None

    def check(self, kind: CredentialKind, secret: str) -> Result:
        if kind is CredentialKind.claude_subscription:
            return Result(Outcome.unverified)
        return self._check_api_key(secret)

    def _check_api_key(self, secret: str) -> Result:
        http_client = (
            httpx2.Client(transport=self.transport, timeout=self.timeout_s)
            if self.transport is not None
            else None
        )
        client = anthropic.Anthropic(
            api_key=secret,
            base_url=self.base_url or ANTHROPIC_API,
            timeout=self.timeout_s,
            max_retries=0,
            http_client=http_client,
        )
        try:
            client.models.list(limit=1)
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            logger.info("model credential check refused", extra={"upstream_status": status})
            if status in _INVALID:
                return Result(Outcome.invalid)
            if status in _BUSY:
                return Result(Outcome.busy, _retry_after(exc.response))
            return Result(Outcome.unavailable)
        except anthropic.APIConnectionError as exc:  # timeouts included
            logger.info("model credential check failed", extra={"error": type(exc).__name__})
            return Result(Outcome.unavailable)
        except Exception as exc:
            # Anything else: an answer the SDK cannot read (a proxy's HTML page makes it fail
            # with an AttributeError), a bug. Logged by type only: the message could quote the
            # request.
            logger.warning("model credential check error", extra={"error": type(exc).__name__})
            return Result(Outcome.unavailable)
        finally:
            client.close()
        return Result(Outcome.verified)
