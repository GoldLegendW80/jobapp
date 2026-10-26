"""Conservative native GET discovery. Labels alone never grant a search submission.

A signed in discovery (B17, `Scope.signed_in`) runs in the person's own persisted profile, with
the site's JavaScript on, for a website the person signed in to: the same controls are refused
and every request the page makes is held to `signed_request`, GET or HEAD only, never an apply,
submit, delete or payment address, the documents within the frozen search scope.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from browserd import detect

MARKER = "native_get"
TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_wait_for",
        "browser_type",
        "browser_click",
        "browser_select_option",
    }
)
QUERY = frozenset({"q", "query", "search", "keyword", "keywords"})
FIELDS = QUERY | {"location", "remote", "contract", "category", "page", "sort"}
ENDPOINTS = frozenset({"search", "jobs", "careers", "vacancies"})
UNSUPPORTED = "unsupported_discovery"
ACCOUNT_STEMS = (
    "account",
    "application",
    "contact",
    "message",
    "profile",
    "login",
    "logout",
    "signin",
    "signout",
    "signup",
    "register",
    "subscrib",
    "credential",
    "buy",
    "payment",
    "paiement",
)
# Read inside every folded path word, so glued forms refuse too: /easyapply, /QuickApply,
# /oneclickapply, /jobs/42/candidature, /candidater, /jetzt-bewerben, /bewirb, /solliciteer.
# Never a stem of an English job slug: "applied", "supply", "candidate" and "solicitor" stay.
APPLY_PARTS = (
    "apply",
    "postul",
    "candidatur",
    "candidater",
    "candidatez",
    "bewerb",
    "bewirb",
    "sollicit",
)
CSP = "script-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'"


@dataclass(frozen=True)
class Scope:
    start: str
    query: str
    filters: tuple[tuple[str, str], ...]
    signed_in: bool = False

    @classmethod
    def parse(cls, raw: object) -> "Scope | None":
        if not isinstance(raw, dict) or set(raw) - {"signed_in"} != {"start", "query", "filters"}:
            return None
        start, query, filters = raw["start"], raw["query"], raw["filters"]
        signed_in = raw.get("signed_in", False)
        if (
            type(signed_in) is not bool
            or not isinstance(start, str)
            or not read_url(start, start)
            or not isinstance(query, str)
            or not query.strip()
            or len(query) > 2000
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in query)
            or not isinstance(filters, dict)
            or len(filters) > 6
            or any(
                k not in FIELDS - QUERY - {"page"} or not isinstance(v, str) or len(v) > 300
                for k, v in filters.items()
            )
        ):
            return None
        return cls(start, query, tuple(sorted(filters.items())), signed_in)

    def criteria(self, pairs: list[tuple[str, str]], *, complete: bool) -> bool:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)) or sum(k in QUERY for k in keys) > 1:
            return False
        expected = dict(self.filters)
        for key, value in pairs:
            if key in QUERY:
                if value != self.query:
                    return False
            elif key == "page":
                if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 1000:
                    return False
            elif key not in expected or value != expected[key]:
                return False
        return not complete or (sum(k in QUERY for k in keys) == 1 and expected.keys() <= set(keys))


def read_url(url: str, current: str, *, form: bool = False, scope: Scope | None = None) -> bool:
    if not isinstance(url, str) or len(url) > 2000:
        return False
    try:
        parts, here = urlsplit(url), urlsplit(scope.start if scope else current)
        if (
            parts.scheme not in {"http", "https"}
            or parts.username
            or parts.password
            or "%" in parts.path
            or any(ord(c) <= 32 or 127 <= ord(c) <= 159 for c in url)
        ):
            return False
        if (parts.scheme, parts.hostname, parts.port) != (here.scheme, here.hostname, here.port):
            return False
        segments = parts.path.strip("/").split("/")
        tokens = [word for segment in segments for word in detect.words(segment)]
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
        keys = [k for k, _ in pairs]
        destructive = (
            detect.says_submit(segments, profile_edit=True)
            or detect.says_danger(segments)
            or any(t.startswith(ACCOUNT_STEMS) for t in tokens)
            or any(part in t for t in tokens for part in APPLY_PARTS)
        )
        complete = bool(pairs) or (
            url != (scope.start if scope else url) and segments[-1] in ENDPOINTS
        )
        return (
            not parts.fragment
            and not destructive
            and (not form or segments[-1] in ENDPOINTS)
            and (not form or not parts.query)
            and len(keys) == len(set(keys))
            and sum(k in QUERY for k in keys) <= 1
            and all(k in FIELDS for k in keys)
            and (scope is None or form or scope.criteria(pairs, complete=complete))
        )
    except (ValueError, TypeError):
        return False


def destructive(url: str) -> bool:
    """An address that applies, submits, deletes or pays, by its path words (read_url's rule
    without the account words: a signed in page reads the person's own account data)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        return True
    segments = parts.path.strip("/").split("/")
    tokens = [word for segment in segments for word in detect.words(segment)]
    return (
        detect.says_submit(segments, profile_edit=True)
        or detect.says_danger(segments)
        or any(part in t for t in tokens for part in APPLY_PARTS)
    )


SIGNIN_PATHS = ("login", "signin", "logon", "connexion", "seconnecter", "auth", "sso")


def signin_url(url: str) -> bool:
    """A sign in page's address by its path (B17): a signed in discovery sent there has lost the
    person's login, which the api tells as `platform_signin`."""
    try:
        segments = urlsplit(url).path.lower().split("/")
    except (ValueError, AttributeError):
        return False
    folded = ["".join(ch for ch in segment if ch.isalnum()) for segment in segments]
    return any(f.startswith(SIGNIN_PATHS) for f in folded if f)


def signed_request(url: str, method: str, document: bool, scope: Scope) -> bool:
    """Whether a request of a signed in discovery page may leave (B17): GET or HEAD only, never a
    destructive address on any host, and a document (a frame's navigation) only within the
    frozen scope, as read_url holds the anonymous discovery."""
    if method not in {"GET", "HEAD"} or not isinstance(url, str) or len(url) > 8000:
        return False
    if document:
        return read_url(url, scope.start, scope=scope)
    return not destructive(url)


def verdict(
    facts: object, tool: str, current: str, *, scope: Scope | None = None, args: dict | None = None
) -> str | None:
    if not isinstance(facts, dict):
        return UNSUPPORTED
    action = (
        "type"
        if tool == "browser_type"
        else "check"
        if tool == "browser_select_option"
        else "click"
    )
    original = detect.verdict(facts, action=action, profile_edit=True)
    if original and original != "submit":
        return original
    if detect.says_submit(facts.get("names", []), profile_edit=True):
        return "submit"
    d = facts.get("discovery")
    # A signed in page runs its scripts by design (B17): its requests are held instead
    # (signed_request), so the script free proof is the anonymous discovery's alone.
    signed_in = scope is not None and scope.signed_in
    if not isinstance(d, dict) or (d.get("native") is not True and not signed_in):
        return UNSUPPORTED
    if facts.get("selector_hit") or facts.get("selector_error") or facts.get("custom_submit"):
        return "submit"
    if d.get("target") not in {"", "_self"}:
        return UNSUPPORTED
    if tool == "browser_click" and facts.get("tag") == "a":
        allowed = read_url(d.get("href", ""), current, scope=scope)
    else:
        fields = d.get("fields")
        allowed = (
            d.get("method") == "get"
            and read_url(d.get("action", ""), current, form=True, scope=scope)
            and isinstance(fields, list)
            and 0 < len(fields) <= 100
        )
        if allowed:
            allowed = any(f.get("name") in QUERY for f in fields if isinstance(f, dict))
            for f in fields:
                if not isinstance(f, dict) or f.get("name") not in FIELDS:
                    allowed = False
                    break
                if not (
                    (f.get("tag") == "input" and f.get("type") in {"text", "search", "hidden"})
                    or (f.get("tag") == "select" and f.get("type") == "select-one")
                ):
                    allowed = False
                    break
        if tool == "browser_type":
            allowed = (
                allowed
                and facts.get("tag") == "input"
                and facts.get("type") in {"text", "search"}
                and d.get("name") in QUERY
                and (scope is None or (args or {}).get("text") == scope.query)
            )
        elif tool == "browser_select_option":
            allowed = allowed and facts.get("tag") == "select" and d.get("name") in FIELDS - QUERY
            if scope is not None:
                expected = dict(scope.filters).get(d.get("name"))
                allowed = (
                    allowed and expected is not None and (args or {}).get("values") == [expected]
                )
        elif tool == "browser_click":
            allowed = (
                allowed
                and facts.get("tag") in {"button", "input"}
                and facts.get("type") == "submit"
                and not d.get("name")
            )
            if allowed and scope is not None:
                allowed = all(isinstance(f.get("value"), str) for f in fields) and scope.criteria(
                    [(f["name"], f["value"]) for f in fields], complete=True
                )
        else:
            allowed = False
    if allowed and "point" in facts:
        return verdict(facts["point"], tool, current, scope=scope, args=args)
    return None if allowed else UNSUPPORTED
