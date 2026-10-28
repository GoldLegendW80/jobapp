"""Conservative native GET discovery. Labels alone never grant a search submission.

The frozen scope (`Scope`, jobs J18) carries the person's own saved values, one agent searching
a website with all of them: a search box (QUERY names) takes one saved title, the `location` box
one saved place, either of them nothing to clear it; a native filter takes its one saved value
(remote, contract), the `location` select one saved place. A value is one of the allow list with
its case and accents aside (`saved_as`, owner 2026-10-28: the agent reads the person's words as
the person would, and a website may want "GenAI Engineer" for the saved "genai engineer"): the
same letters in the same order, no control character, nothing else; any other value, a
translation included, is refused (fail closed). A search request carries one search key holding
a saved title, a saved place or nothing in `location` (or no `location` at all: the place box
left empty is always allowed), and every saved filter (`Scope.criteria`); the search page itself,
its address with no query, opens as any page (2026-10-28). One click besides: a cookie banner's
decline button (`decline`), so a banner that covers the page is closed as a person would, never
by accepting.

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
refused. A subresource whose path still holds a `%` after one decode (`/static/img/100%25.png`)
is refused; a document with a `%` in its path already was. A word is read with its case folded
first too (`/jobs/42/AppLy`, detect.match_words), but a glued confirm verb and application noun
written in one case is one word, so the path rule lets `/jobs/42/confirmapplication` leave (the
query rule reads the noun inside a word).
"""

import re
import unicodedata
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

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
# A cookie banner's decline button (2026-10-28, issue 716): the one click a discovery takes
# outside a native search, so a banner that covers the page can be closed as a person would,
# never by accepting. One of its names is one of these phrases, read as folded words (case,
# accents and punctuation aside, detect.words); none of its other names holds an ACCEPT_STEMS
# or ACCOUNT_STEMS word (a sign in, glued too) or OK, yes or oui; it is a button (never a link,
# never a form's submit button, nothing that holds one), and the click lands on it (`decline`).
DECLINES = frozenset(
    tuple(phrase.split())
    for phrase in (
        "no thanks", "no thank you", "reject", "reject all", "reject cookies",
        "reject all cookies", "reject optional cookies", "reject non essential cookies",
        "decline", "decline all", "decline cookies", "decline all cookies",
        "decline optional cookies", "refuse", "refuse all", "refuse cookies", "refuse all cookies",
        "deny", "deny all", "disagree", "i disagree", "only necessary", "only necessary cookies",
        "necessary only", "necessary cookies only", "use necessary cookies only",
        "only essential", "only essential cookies", "essential only", "essential cookies only",
        "continue without accepting", "non merci", "refuser", "tout refuser", "refuser tout",
        "je refuse", "refuser les cookies", "refuser tous les cookies", "refuser et fermer",
        "continuer sans accepter", "cookies necessaires uniquement",
        "uniquement les cookies necessaires", "seulement les cookies necessaires", "ablehnen",
        "alle ablehnen", "nur notwendige", "nur notwendige cookies",
    )
)  # fmt: skip
ACCEPT_STEMS = (
    "accept", "agree", "allow", "autoris", "choose", "choisi", "custom", "personnali",
    "parametr", "setting", "preferen", "akzept", "zustimm", "erlaub", "einstell", "connect",
    "inscri", "anmeld",
)  # fmt: skip
ACCEPT_WORDS = frozenset({"ok", "okay", "yes", "oui", "ja", "accord", "daccord"})


def _control(text: str) -> bool:
    return any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text)


def _base(letter: str) -> str:
    """A letter with its accents and case aside: decomposed, combining marks dropped, case
    folded."""
    decomposed = unicodedata.normalize("NFD", letter)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def same_value(text: str, value: str) -> bool:
    """`text` is the saved `value` with its case and accents aside. Both are read in NFC; a text
    whose NFKC differs from its NFC (a long s, a ligature, a full width form) is refused. Then
    either the case folded forms are equal (STRASSE for Straße), or both have the same length in
    code points and each pair is identical or two letters with the same base letter, case aside
    (Montreal for Montréal). So a mark is never added: one stacked on a letter, on a space,
    before the first letter, or of another script makes the text longer and is refused.
    Declared residue: the Kelvin and Ohm signs are canonical singletons (NFC maps them to K and
    Ω), and a letter may be typed as any one precomposed letter of the same base (è, é or ê for
    e), at most one code point per letter of the saved value, as its case may."""
    typed = unicodedata.normalize("NFC", text)
    if unicodedata.normalize("NFKC", typed) != typed:
        return False
    saved = unicodedata.normalize("NFC", value)
    if typed.casefold() == saved.casefold():
        return True
    return len(typed) == len(saved) and all(
        a == b or (a.isalpha() and b.isalpha() and _base(a) == _base(b))
        for a, b in zip(typed, saved, strict=True)
    )


def saved_as(text: object, saved: tuple[str, ...]) -> str | None:
    """The saved value `text` is, with its case and accents aside (`same_value`); None for
    anything else: not text, longer than VALUE_MAX, a control character, or no such value."""
    if not isinstance(text, str) or not text or len(text) > VALUE_MAX or _control(text):
        return None
    return next((value for value in saved if same_value(text, value)), None)


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
            or _control(value)
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
    # The Indeed platform (owner, 2026-10-28): its start page's first navigation may follow a
    # redirect to one of its own country sites (`country_scope`), once.
    country: bool = False

    @classmethod
    def parse(cls, raw: object) -> "Scope | None":
        keys = {"start", "titles", "places", "filters"}
        if not isinstance(raw, dict) or set(raw) - {"signed_in", "country"} != keys:
            return None
        start, filters = raw["start"], raw["filters"]
        titles = _values(raw["titles"], needed=True)
        places = _values(raw["places"], needed=False)
        signed_in, country = raw.get("signed_in", False), raw.get("country", False)
        if (
            type(signed_in) is not bool
            or type(country) is not bool
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
        return cls(start, titles, places, tuple(sorted(filters.items())), signed_in, country)

    def takes(self, name: object, text: object) -> bool:
        """Whether this text may be typed in a text field of this name: one saved title in a
        search box, one saved place in the location box (`saved_as`), nothing to clear either;
        in any other field, nothing."""
        if name not in QUERY and name != "location":
            return False
        return (
            text == "" or saved_as(text, self.titles if name in QUERY else self.places) is not None
        )

    def chooses(self, name: object, value: object) -> bool:
        """Whether a native select of this name may be set to this value: one saved place, or
        the filter's one saved value (`saved_as`); nothing for any other select."""
        if name == "location":
            return saved_as(value, self.places) is not None
        saved = dict(self.filters).get(name) if isinstance(name, str) else None
        return saved is not None and saved_as(value, (saved,)) is not None

    def criteria(self, pairs: list[tuple[str, str]], *, complete: bool) -> bool:
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)) or sum(k in QUERY for k in keys) > 1:
            return False
        expected = dict(self.filters)
        for key, value in pairs:
            if key in QUERY:  # a search carries a saved title: the box is cleared only to type
                if saved_as(value, self.titles) is None:
                    return False
            elif key == "page":
                if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 1000:
                    return False
            elif key == "location":
                if not self.takes(key, value):
                    return False
            elif key not in expected or saved_as(value, (expected[key],)) is None:
                return False
        # The place box left empty, or no place box at all, is always allowed (owner,
        # 2026-10-28).
        return not complete or (sum(k in QUERY for k in keys) == 1 and expected.keys() <= set(keys))


def read_url(
    url: str, current: str, *, form: bool = False, scope: Scope | None = None, hop: bool = False
) -> bool:
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
        tokens = [word for segment in segments for word in detect.match_words(segment)]
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
        keys = [k for k, _ in pairs]
        destructive = (
            detect.says_submit(segments, profile_edit=True)
            or detect.says_danger(segments)
            or any(t.startswith(ACCOUNT_STEMS) for t in tokens)
            or any(part in t for t in tokens for part in APPLY_PARTS)
        )
        # A search request carries its saved title; the search page itself, its address with
        # no query at all (`/fr/jobs`), is where a person types it (2026-10-28, issue 716). A
        # redirect hop (`hop`) is held to the rule reviewed with the start page's own redirect:
        # an endpoint there is a search, so it carries the saved criteria.
        complete = bool(pairs) or (
            hop and url != (scope.start if scope else url) and segments[-1] in ENDPOINTS
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


# The redirect statuses a browser follows by itself (304 is no redirect: it is never followed).
REDIRECTS = frozenset({301, 302, 303, 307, 308})


def _resolved(url: str, location: object) -> str | None:
    """A redirect's Location resolved against the request's address. None for a missing, blank
    or overlong one, and for one Chromium may read otherwise than Python's urljoin, before it
    is resolved: a backslash (a slash to Chromium, so `/\\elsewhere/en` leaves the host), a
    space or a control character (Chromium strips tab and newline), raw or percent decoded."""
    if not isinstance(location, str) or not location or len(location) > 2000:
        return None
    if any(
        ch == "\\" or ord(ch) <= 32 or 127 <= ord(ch) <= 159
        for text in (location, unquote(location))
        for ch in text
    ):
        return None
    try:
        return urljoin(url, location)
    except ValueError:
        return None


def redirect_target(url: str, location: object, scope: Scope) -> str | None:
    """Where a redirect response of a discovery request goes, when it may be followed (jobs J18:
    a website's start page that answers on a locale path, `/` to `/en`): its Location resolved
    against the request's address (`_resolved`), held to the rule of the navigation it
    replaces, read_url within the frozen scope (same scheme, host and port as the start,
    nothing that applies, submits, reaches an account or signs out, only the saved criteria in
    its query). None for anything else, a missing or blank Location included: the redirect is
    refused."""
    target = _resolved(url, location)
    return target if target and read_url(target, scope.start, scope=scope, hop=True) else None


# Owner, 2026-10-28: "Follow its own country sites. A redirect from www.indeed.com to any
# xx.indeed.com is followed, and the search continues there. Redirects to any other domain stay
# refused." The home host of each platform that has country sites, and the scheme they keep.
# `www.indeed.test:8080` is the fixture server's stand in for the tests, as the api's
# platform_policy.test_start maps a platform there: `.test` is reserved (RFC 2606), no public
# DNS answers it, and the production proxy refuses a private address.
COUNTRY_HOMES = {"www.indeed.com": "https", "www.indeed.test:8080": "http"}
COUNTRY_LABEL = re.compile(r"[a-z]{2}")


def country_site(start: str, url: str) -> bool:
    """Whether `url` is the start page on a country site of the home host `start` is on
    (https://www.indeed.com/ to https://fr.indeed.com/): the home's own scheme, a host of
    exactly two lowercase ASCII letters then the home's domain and port, no user info, the
    start's own path and query, no fragment, as written. The home itself is no country site:
    a redirect on its own host is a redirect_target."""
    if not isinstance(start, str) or not isinstance(url, str):
        return False
    try:
        home, there = urlsplit(start), urlsplit(url)
    except ValueError:
        return False
    scheme = COUNTRY_HOMES.get(home.netloc)
    label, dot, rest = there.netloc.partition(".")
    return (
        scheme is not None
        and home.scheme == there.scheme == scheme
        and bool(dot)
        and rest == home.netloc[4:]
        and COUNTRY_LABEL.fullmatch(label) is not None
        and (there.path, there.query) == (home.path, home.query)
        and not there.fragment
        and url == urlunsplit((scheme, there.netloc, there.path, there.query, ""))
    )


def country_scope(scope: Scope, url: str) -> Scope | None:
    """The frozen scope moved to the country site `url` is on (owner, 2026-10-28), for a
    search of a platform with country sites (`Scope.country`) from its home host: `url` is
    its new start, and every other rule holds there unchanged (read_url within the moved
    scope). The moved scope follows no other country site. None when it may not move."""
    if not scope.country or not country_site(scope.start, url):
        return None
    moved = replace(scope, start=url, country=False)
    return moved if read_url(url, url, scope=moved) else None


def country_redirect(url: str, location: object, scope: Scope) -> Scope | None:
    """The scope a redirect response of the start page's first navigation moves to, when its
    Location (`_resolved`) is a country site of the scope's home (`country_scope`)."""
    target = _resolved(url, location)
    return country_scope(scope, target) if target else None


def location_of(headers: object) -> str | None:
    """The one Location header of a CDP response (names in any case); None when there is none or
    more than one (ambiguous: refused)."""
    if not isinstance(headers, list):
        return None
    found = [
        h.get("value")
        for h in headers
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "location"
    ]
    return found[0] if len(found) == 1 and isinstance(found[0], str) else None


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
    (`easyApply`, `easy_apply`, `EASYAPPLY`), each also with its case folded first (`AppLy`,
    detect.match_words)."""
    tokens = []
    for text in texts:
        tokens.extend(detect.match_words(text))
        tokens.extend(detect.match_words(text.replace(" ", "").replace("_", "").replace("-", "")))
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
    tokens = [word for segment in segments for word in detect.match_words(segment)]
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


def signed_request(
    url: str, method: str, document: bool, scope: Scope, *, hop: bool = False
) -> bool:
    """Whether a request of a signed in discovery page may leave (B17): GET or HEAD only, never a
    destructive address on any host (a document's too: `/logoff` or `/fr/deconnexion` in a
    frame would sign the person out), and a document (a frame's navigation) only within the
    frozen scope, as read_url holds the anonymous discovery."""
    if method not in {"GET", "HEAD"} or not isinstance(url, str) or len(url) > 8000:
        return False
    if document and not read_url(url, scope.start, scope=scope, hop=hop):
        return False
    return not destructive(url, scope)


def _declines(facts: dict) -> bool:
    """Whether these facts are of a cookie banner's decline button (DECLINES)."""
    names = facts.get("names")
    if not isinstance(names, list) or not names or len(names) > 60:
        return False
    said = [tuple(detect.words(n, camel=False)) for n in names if isinstance(n, str)]
    if len(said) != len(names) or not any(words in DECLINES for words in said):
        return False
    for name, words in zip(names, said, strict=True):
        if words in DECLINES:
            continue
        tokens = [*words, *detect.match_words(name), "".join(words)]
        if any(t.startswith((*ACCEPT_STEMS, *ACCOUNT_STEMS)) or t in ACCEPT_WORDS for t in tokens):
            return False
    d = facts.get("discovery")
    tag, kind = facts.get("tag"), facts.get("type")
    button = (
        tag == "button"
        or (tag == "input" and kind in {"button", "submit"})
        or (facts.get("role") == "button" and tag not in {"a", "input", "select", "textarea"})
    )
    return (
        button
        and isinstance(d, dict)
        and not d.get("href")
        and d.get("target") in {"", "_self"}
        and not (facts.get("in_form") and kind in {"submit", "image"})
        and not facts.get("holds_submit")
        and not facts.get("selector_hit")
        and not facts.get("selector_error")
        and not facts.get("custom_submit")
        and not facts.get("editable")
        and not facts.get("credential")
    )


def decline(facts: object) -> bool:
    """A click on a cookie banner's decline button, and only that (DECLINES), at its click point
    too: the element there is the button or within it, and reads as the same decline."""
    if not isinstance(facts, dict) or not _declines(facts):
        return False
    point = facts.get("point")
    return isinstance(point, dict) and point.get("inside") is True and _declines(point)


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
    if tool == "browser_click" and decline(facts):
        point = facts["point"]
        refused = detect.verdict(point, action="click", profile_edit=True)
        if refused and refused != "submit":
            return refused
        return "submit" if detect.says_submit(point.get("names", []), profile_edit=True) else None
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
            # saved places. With one, the text is a saved value of that field (Scope.takes).
            text = (args or {}).get("text")
            allowed = (
                allowed
                and facts.get("tag") == "input"
                and facts.get("type") in {"text", "search"}
                and (d.get("name") in QUERY if scope is None else scope.takes(d.get("name"), text))
            )
        elif tool == "browser_select_option":
            allowed = allowed and facts.get("tag") == "select" and d.get("name") in FIELDS - QUERY
            if scope is not None:
                values = (args or {}).get("values")
                allowed = (
                    allowed
                    and isinstance(values, list)
                    and len(values) == 1
                    and scope.chooses(d.get("name"), values[0])
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
