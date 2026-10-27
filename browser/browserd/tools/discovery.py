"""Conservative native GET discovery. Labels alone never grant a search submission.

The frozen scope (`Scope`, jobs J18) carries the person's own saved values, one agent searching
a website with all of them: a search box (QUERY names) takes exactly one saved title, the
`location` box exactly one saved place, either of them nothing to clear it; a native filter
takes its one saved value (remote, contract), the `location` select one saved place. Any other
value is refused (exact string match, no folding: an allow list, fail closed). A search request
carries one saved title in one search key, one saved place when places are saved, and every
saved filter (`Scope.criteria`).

A signed in discovery (B17, `Scope.signed_in`) runs in the person's own persisted profile, with
the site's JavaScript on, for a website the person signed in to: the same controls are refused
and every request the page makes is held to `signed_request` (its frames' too, an inline
sandboxed one included: a sweep lease keeps it in the page's process, chromium.switches), GET or
HEAD only, never to an address whose path applies, submits, deletes or pays, nor one whose path
or query applies, submits or signs the person out (`destructive`, the path percent decoded
once), the documents within the frozen search scope.
Declared open points: a delete or payment word in the query only (`?action=delete`), the save
and update words in the query only (`?action=save`), and a query value that is the person's own
search words, whatever its key (a search for "apply" lets `?action=apply` leave), are not
refused.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

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
VALUES_MAX = 100  # saved titles, or saved places, a scope carries
VALUE_MAX = 300  # characters of one saved value
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


def _values(raw: object, *, needed: bool) -> tuple[str, ...] | None:
    """A list of saved values as the api sends it: strings with a non blank character, no
    control character, at most VALUE_MAX characters each and VALUES_MAX in all, none twice."""
    if not isinstance(raw, list) or len(raw) > VALUES_MAX or (needed and not raw):
        return None
    for value in raw:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > VALUE_MAX
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
        ):
            return None
    return tuple(raw) if len(set(raw)) == len(raw) else None


@dataclass(frozen=True)
class Scope:
    start: str
    titles: tuple[str, ...]  # what a search box takes, one at a time
    places: tuple[str, ...] = ()  # what the location box or select takes, one at a time
    filters: tuple[tuple[str, str], ...] = ()  # one saved value per native filter
    signed_in: bool = False

    @classmethod
    def parse(cls, raw: object) -> "Scope | None":
        keys = {"start", "titles", "places", "filters"}
        if not isinstance(raw, dict) or set(raw) - {"signed_in"} != keys:
            return None
        start, filters = raw["start"], raw["filters"]
        titles = _values(raw["titles"], needed=True)
        places = _values(raw["places"], needed=False)
        signed_in = raw.get("signed_in", False)
        if (
            type(signed_in) is not bool
            or not isinstance(start, str)
            or not read_url(start, start)
            or titles is None
            or places is None
            or not isinstance(filters, dict)
            or len(filters) > 6
            or any(
                k not in FIELDS - QUERY - {"page", "location"}
                or not isinstance(v, str)
                or len(v) > 300
                for k, v in filters.items()
            )
        ):
            return None
        return cls(start, titles, places, tuple(sorted(filters.items())), signed_in)

    def typable(self, name: object) -> frozenset[str]:
        """What may be typed in a text field of this name: one saved title in a search box, one
        saved place in the location box, nothing to clear either; in any other field, nothing."""
        if name in QUERY:
            return frozenset({*self.titles, ""})
        if name == "location":
            return frozenset({*self.places, ""})
        return frozenset()

    def choices(self, name: object) -> frozenset[str]:
        """What a native select of this name may be set to: one saved place, or the filter's one
        saved value; nothing for any other select."""
        if name == "location":
            return frozenset(self.places)
        value = dict(self.filters).get(name) if isinstance(name, str) else None
        return frozenset() if value is None else frozenset({value})

    def criteria(self, pairs: list[tuple[str, str]], *, complete: bool) -> bool:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)) or sum(k in QUERY for k in keys) > 1:
            return False
        expected = dict(self.filters)
        for key, value in pairs:
            if key in QUERY:
                if value not in self.titles:
                    return False
            elif key == "page":
                if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 1000:
                    return False
            elif key == "location":
                if value not in self.places:
                    return False
            elif key not in expected or value != expected[key]:
                return False
        return not complete or (
            sum(k in QUERY for k in keys) == 1
            and expected.keys() <= set(keys)
            and (not self.places or "location" in keys)
        )


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


# Read inside every glued path segment, query key and query value of a signed in page's request:
# a request that ends the person's own login there (/logout, /api/sign-out, ?action=logoff,
# /se-deconnecter) is refused, so a search never signs the person out.
SESSION_ENDS = ("logout", "logoff", "signout", "deconnex", "deconnect", "disconnect", "abmeld")


def ends_session(url: str) -> bool:
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return True
    texts = [*unquote(parts.path).split("/"), *(text for pair in pairs for text in pair)]
    glued = ["".join(detect.words(text)) for text in texts]
    return any(end in text for text in glued for end in SESSION_ENDS)


def _applies(texts: list[str]) -> bool:
    """Whether these query keys and values apply or submit: an APPLY_PARTS word inside any of
    their words, or a word starting with a submit stem, or a confirm verb with an application
    noun anywhere in the query (detect.says_submit's rule without its caps on names, so a 61st
    field or a value past 500 characters is read too). Each text is read split and glued
    (`easyApply`, `easy_apply`, `EASYAPPLY`)."""
    tokens = []
    for text in texts:
        tokens.extend(detect.words(text))
        tokens.extend(detect.words(text.replace(" ", "").replace("_", "").replace("-", "")))
    return (
        any(t.startswith(detect.SUBMIT_STEMS) for t in tokens)
        or any(part in t for t in tokens for part in APPLY_PARTS)
        or (  # the noun read inside a word too: `confirmapplication`, `finishApplication`
            any(t.startswith(detect.CONFIRM_VERBS) for t in tokens)
            and any(noun in t for t in tokens for noun in detect.APPLICATION_NOUNS)
        )
    )


def own_words(scope: Scope | None) -> set[tuple[str, ...]]:
    """The person's own search words, as folded words (case, accents and separators aside): the
    frozen scope's saved titles, places and filter values."""
    if scope is None:
        return set()
    texts = [*scope.titles, *scope.places, *(value for _, value in scope.filters)]
    return {tuple(words) for words in map(detect.words, texts) if words}


def query_applies(query: str, scope: Scope | None = None) -> bool:
    """Whether a request's query applies or submits (B17), by its keys and values:
    `?action=apply`, `?do=postuler`, `?easyApply=true`, `?op=submitApplication`. Every key is
    read; a value whose folded words are the person's own search words (own_words) is skipped,
    so a search for "apply engineer" or "Sender" is read. Fails closed: a query that cannot be
    split (more than 100 fields) is refused. The save and update words of the path rule, and
    its delete and payment words, are not read here: `sort=updated`, `saved=true` and
    `category=payments` are reads (declared open point: `?action=delete` leaves)."""
    if not query:
        return False
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return True
    own = own_words(scope)
    texts = [key for key, _ in pairs]
    texts.extend(value for _, value in pairs if tuple(detect.words(value)) not in own)
    return _applies(texts)


def destructive(url: str, scope: Scope | None = None) -> bool:
    """An address that applies, submits, deletes, pays or signs the person out by its path
    words (read_url's rule without the account words: a signed in page reads the person's own
    account data), or applies, submits or signs the person out by its query (query_applies,
    which skips the person's own search words of `scope`, and ends_session). The path is read
    as the server reads it, percent decoded once (`/jobs/42/ap%70ly` is `/jobs/42/apply`); a `%`
    still in it after that decode is refused, never guessed (`ap%2570ly`, `100%25.png`)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        return True
    path = unquote(parts.path)
    if "%" in path:
        return True
    segments = path.strip("/").split("/")
    tokens = [word for segment in segments for word in detect.words(segment)]
    return (
        detect.says_submit(segments, profile_edit=True)
        or detect.says_danger(segments)
        or any(part in t for t in tokens for part in APPLY_PARTS)
        or ends_session(url)
        or query_applies(parts.query, scope)
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
    destructive address on any host (a document's too: `/logoff` or `/fr/deconnexion` in a
    frame would sign the person out), and a document (a frame's navigation) only within the
    frozen scope, as read_url holds the anonymous discovery."""
    if method not in {"GET", "HEAD"} or not isinstance(url, str) or len(url) > 8000:
        return False
    if document and not read_url(url, scope.start, scope=scope):
        return False
    return not destructive(url, scope)


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
            # Without a scope only a search box, as before; the location box needs the scope's
            # saved places. With one, the text is a saved value of that field, exactly.
            text = (args or {}).get("text")
            allowed = (
                allowed
                and facts.get("tag") == "input"
                and facts.get("type") in {"text", "search"}
                and (
                    d.get("name") in QUERY
                    if scope is None
                    else isinstance(text, str) and text in scope.typable(d.get("name"))
                )
            )
        elif tool == "browser_select_option":
            allowed = allowed and facts.get("tag") == "select" and d.get("name") in FIELDS - QUERY
            if scope is not None:
                values = (args or {}).get("values")
                allowed = (
                    allowed
                    and isinstance(values, list)
                    and len(values) == 1
                    and isinstance(values[0], str)
                    and values[0] in scope.choices(d.get("name"))
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
