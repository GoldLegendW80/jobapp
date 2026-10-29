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

Real job websites (owner, 2026-10-29, B19: "Let it use the search boxes"): a platform's hosts
are a family (`Family`, `on_site`): Indeed's and LinkedIn's domain, its `www.` host and every
host of two lowercase letters under it, Welcome to the Jungle's domain and its `www.` host. Every
rule above that reads the start's host reads its family instead, so a redirect within the family
is followed and one anywhere else stays stopped. Signed in, the agent may also type one saved
title or place into a text box of the page (`search_box`, in a GET search form or in no form,
the box's labels telling which, `Scope.types`), press Enter there, and click a button or link
one of whose names is a search word and whose other names are search or neutral words or one
token holding no action stem (`search_words`); a page of the family opens when it is a
posting (`posting`) or a result list (`listing`) whose search key holds a saved title, and a
redirect hop within the family when it is no apply, account or sign out address (`page_url`);
a page's fetch or XHR may POST to the family or the platform's search service (`posts`). The
submit, apply and danger detectors run first and win, documents stay GET only.
Declared open points (B19): a place key of a result list (`l`, `location`, `geoId`) is not held
to a saved place; a combobox that picks its first suggestion on Enter searches that suggestion;
a POST's body is not read; pagination and filter buttons that are no link are refused; a
posting whose address holds a refused word (`payments-engineer`) is refused; a name of one
token (an id, a name attribute or a one word label) holding no ACTION_STEMS stem is neutral, so
a button whose aria-label is "Search" and whose text is "Hide" is clicked; the search words are
English, French and German only; an empty text with `submit` presses Enter on the emptied box.

Round 1 of B19's review: a text box whose names say it writes to someone (a message, a reply, a
comment, a chat, a note or a post, `composes`) is never a search box, so no typing and no Enter
there; a page of a person or of the person's own lists (`personal`: LinkedIn's profiles, feed,
network, notifications and inbox, Indeed's jobs, profile and review writing, Welcome to the
Jungle's `/<language>/me` and account) never opens by a navigation or a link click, on the
family, signed in or not; a page's POST says no alert, favourite, subscription, like, reaction
or share either (POST_WORDS). Declared open points: a composer whose names hold none of those
words ("Type here", "Aa") is read as any box; "posting" and "writer" are no composer words, and
a glued id holding "post" (`postbox`) is read as a whole word only; a box whose names hold
"writing" or "note" in another sense is refused (fail closed).

Round 2: the one redirect to a person's page that is followed is the saved start's own to
LinkedIn's `/feed/` exactly (`start_feed`: a signed in LinkedIn answers its root there), no
other path and no other source; Algolia's client metadata (`x-algolia-agent`, whose library
list names `react`) is no part of what a POST says (CLIENT_KEYS); `like` and `share` are read
as whole words and `react` only as `reaction`. Declared open points: a POST whose address says
`react` alone or a glued `jobshare` is not read; a start that LinkedIn answers by a redirect to
another address first (`fr.linkedin.com` to `www.linkedin.com`) stops at its second hop.
"""

import re
import unicodedata
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

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
# The languages a website's search page may sit under (`/fr/jobs`, `/en-gb/jobs`, SEARCH_PATH):
# ISO 639-1 codes of languages job websites use, never a word a path of the person's account
# uses (`/me/jobs`, `/my/jobs`: "my" is left out on purpose).
LANGUAGES = frozenset(
    {"ar", "bg", "bn", "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
     "fr", "ga", "gl", "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "ka", "kk", "ko",
     "lt", "lv", "mk", "ms", "mt", "nb", "nl", "nn", "no", "pl", "pt", "ro", "ru", "sk", "sl",
     "sq", "sr", "sv", "sw", "ta", "th", "tr", "uk", "ur", "uz", "vi", "zh"}
)  # fmt: skip
REGION = re.compile(r"[-_][A-Za-z]{2}")
UNSUPPORTED = "unsupported_discovery"
VALUES_MAX = 100  # saved titles, or saved places, a scope carries
VALUE_MAX = 300  # characters of one saved value
ACCOUNT_STEMS = (
    "account",
    "application",
    "contact",
    "messag",  # messages, messaging (B19: never the person's inbox, signed in)
    "inbox",
    "mynetwork",
    "notification",
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
# never by accepting. An allow list (round 2, review of PR 720): every name of the element (its
# text, aria-label, title, value, id and the names within it) is read in NFKC with format
# characters, accents and case aside, and is either one of these phrases or made only of
# NEUTRAL words, and one of them is a phrase; a name holding a letter or a digit outside basic
# Latin (another script, a homoglyph such as a Cyrillic small es) refuses. It is a button (never a
# link, never a form's submit button, nothing that holds one), and the click lands on it and
# reads the same (`decline`).
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
# Words a decline button's other names may hold: what the decline phrases say, what a banner
# calls itself and the ids of common consent banners (Axeptio, Didomi, OneTrust, Cookiebot,
# tarteaucitron). A number is neutral too. Any other word refuses (fail closed).
NEUTRAL = frozenset(
    {"no", "thanks", "non", "merci", "reject", "decline", "refuse", "refuser", "deny", "denied",
     "disagree", "dismiss", "ablehnen", "all", "tout", "close", "fermer", "the", "cookie",
     "cookies", "banner", "bandeau", "notice", "dialog", "popup", "modal", "button", "btn",
     "handler", "axeptio", "didomi", "onetrust", "cybot", "cookiebot", "body", "tarteaucitron"}
)  # fmt: skip


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

    def types(self, names: object, text: object) -> bool:
        """B19, signed in: whether this text may be typed in a text box whose names are these,
        in any box of the page (`search_box`): nothing to clear it, else one saved title in a
        box its labels call a title box, one saved place in a place box, and either one where
        they cannot tell (`box_kind`)."""
        if text == "":
            return True
        kind = box_kind(names)
        saved = (
            self.titles
            if kind == "title"
            else self.places
            if kind == "place"
            else (*self.titles, *self.places)
        )
        return saved_as(text, saved) is not None

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


def search_path(path: str) -> bool:
    """Whether an address's path is a website's own job search page (round 2, review of PR 720):
    `/<endpoint>` or `/<language>/<endpoint>` only, a trailing slash aside, the language one of
    LANGUAGES with a region or not (`/fr/jobs`, `/en-GB/search`), the endpoint one of ENDPOINTS,
    compared as written (case sensitive). Never at any depth: `/settings/jobs`, `/admin/jobs` or
    `/fr/mon-compte/jobs` are not. The api's finder.search_page reads it the same way."""
    segments = path.strip("/").split("/")
    if len(segments) == 1:
        return segments[0] in ENDPOINTS
    language, region = segments[0][:2], segments[0][2:]
    return (
        len(segments) == 2
        and segments[1] in ENDPOINTS
        and language in LANGUAGES
        and (not region or REGION.fullmatch(region) is not None)
    )


def _path_refused(segments: list[str]) -> bool:
    """A path that submits, deletes, pays, applies or reaches the person's account, by its
    words (read_url's rule)."""
    tokens = [word for segment in segments for word in detect.match_words(segment)]
    return (
        detect.says_submit(segments, profile_edit=True)
        or detect.says_danger(segments)
        or any(t.startswith(ACCOUNT_STEMS) for t in tokens)
        or any(part in t for t in tokens for part in APPLY_PARTS)
    )


def read_url(
    url: str,
    current: str,
    *,
    form: bool = False,
    scope: Scope | None = None,
    hop: bool = False,
    source: str | None = None,
) -> bool:
    if not isinstance(url, str) or len(url) > 2000:
        return False
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.username
            or parts.password
            or "%" in parts.path
            or any(ord(c) <= 32 or 127 <= ord(c) <= 159 for c in url)
        ):
            return False
        if not on_site(url, scope.start if scope else current):  # the start's host or family
            return False
        segments = parts.path.strip("/").split("/")
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
        keys = [k for k, _ in pairs]
        # A person's page or the person's own list on a family never opens (B19, round 1).
        family = family_of(scope.start if scope else current)
        feed = hop and start_feed(url, source, scope.start if scope else current)
        destructive = _path_refused(segments) or personal(
            family, parts.hostname, segments, feed=feed
        )
        # A search request carries its saved title; the search page itself, its address with
        # no query at all (`/fr/jobs`), is where a person types it (2026-10-28, issue 716). A
        # redirect hop (`hop`) is held to the rule reviewed with the start page's own redirect:
        # an endpoint there is a search, so it carries the saved criteria.
        # The search page is `/<endpoint>` or `/<language>/<endpoint>` (search_path); an
        # endpoint at any other depth (`/settings/jobs`) is a search, so it carries the criteria.
        complete = bool(pairs) or (
            url != (scope.start if scope else url)
            and segments[-1] in ENDPOINTS
            and (hop or not search_path(parts.path))
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
    if target and read_url(target, scope.start, scope=scope, hop=True, source=url):
        return target
    return None


# Owner, 2026-10-29 (B19, "Let it use the search boxes"): each website's own hosts are allowed.
# A platform's hosts are a family, on https and its default port: its domain, the domain's
# `www.` host and, with `countries`, every host of exactly two lowercase ASCII letters under the
# domain (`fr.indeed.com`, the owner's "any xx.indeed.com" of 2026-10-28). Its `signin` hosts
# (`secure.indeed.com`) are outside it: a page there is the sign in wall (`signin_url`). Its
# `search` domains are its search service: a page's fetch or XHR may POST to a host under one
# (`posts`). Each family has a `.test` stand in on http and port 8080, the fixture server's
# names for the tests, as the api's platform_policy.test_start maps a platform there: `.test` is
# reserved (RFC 2606), no public DNS answers it, and the production proxy refuses a private
# address. The api's finder.FAMILIES is the same table.
COUNTRY_LABEL = re.compile(r"[a-z]{2}")
PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class Family:
    domain: str
    platform: str
    countries: bool = False
    signin: tuple[str, ...] = ()
    search: tuple[str, ...] = ()
    scheme: str = "https"
    port: int = 443

    def member(self, host: str) -> bool:
        if host in (self.domain, "www." + self.domain):
            return True
        label, dot, rest = host.partition(".")
        return (
            self.countries
            and bool(dot)
            and rest == self.domain
            and COUNTRY_LABEL.fullmatch(label) is not None
        )


def _stand_in(family: Family) -> Family:
    def test(domain: str) -> str:
        return domain.rsplit(".", 1)[0] + ".test"

    return replace(
        family,
        domain=test(family.domain),
        search=tuple(map(test, family.search)),
        scheme="http",
        port=8080,
    )


PLATFORM_FAMILIES = (
    Family("indeed.com", "indeed", countries=True, signin=("secure",)),
    Family("linkedin.com", "linkedin", countries=True),
    Family(
        "welcometothejungle.com", "welcome_to_the_jungle", search=("algolia.net", "algolianet.com")
    ),
)
FAMILIES = (*PLATFORM_FAMILIES, *map(_stand_in, PLATFORM_FAMILIES))


def _where(url: object) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) of an address, the port the scheme's default when it has none."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.username or parts.password:
        return None
    port = PORTS.get(parts.scheme) if port is None else port
    return parts.scheme, parts.hostname or "", port


def family_of(url: object) -> Family | None:
    """The family an address is on: its scheme and port, its host a member."""
    where = _where(url)
    if where is None:
        return None
    scheme, host, port = where
    return next(
        (f for f in FAMILIES if (f.scheme, f.port) == (scheme, port) and f.member(host)), None
    )


def on_site(url: object, start: object) -> bool:
    """Whether `url` is on the start's website: the start's family when it is on one, else the
    start's own scheme, host and port."""
    family = family_of(start)
    if family is not None:
        return family_of(url) is family
    there, here = _where(url), _where(start)
    return there is not None and there == here and bool(there[1])


def _search_service(url: object, family: Family) -> bool:
    where = _where(url)
    return (
        where is not None
        and (where[0], where[2]) == (family.scheme, family.port)
        and any(where[1].endswith("." + domain) for domain in family.search)
    )


def _language(segment: str) -> bool:
    language, region = segment[:2], segment[2:]
    return language in LANGUAGES and (not region or REGION.fullmatch(region) is not None)


# B19, round 1: the pages of a person and of the person's own lists on a platform's family never
# open by a navigation or a link click, signed in or not: opening a profile tells its owner
# (LinkedIn's "who viewed your profile"), and the person's feed, network, notifications, inbox,
# saved jobs and reviews are no job search. Each path segment is read percent decoded, folded as
# detect.words folds it and glued (`My-Items` is `myitems`): on LinkedIn and Indeed a segment
# of PERSONAL_SEGMENTS anywhere in the path (`/in/<anyone>`, `/pub/`, `/company/<x>/people`,
# `/mynetwork`, `/notifications`, `/feed`, `/messaging`, `/me`, `/my-items`; `/myjobs`,
# `/profile`, and the review forms `/write` and `/writereview`, whole segments: `/cmp/Writer*` is
# a company), on Indeed its account host `my.indeed.com`, on Welcome to the Jungle a
# PERSONAL_FIRST first segment, after a language or not (`/fr/me`, `/en/account`). One redirect is
# followed (`start_feed`): a signed in LinkedIn answers its root, the saved start, with `/feed/`.
PERSONAL_SEGMENTS = {
    "linkedin": frozenset(
        {"in", "pub", "people", "feed", "me", "myitems", "mynetwork", "notifications",
         "messaging"}
    ),
    "indeed": frozenset({"myjobs", "profile", "write", "writereview"}),
}  # fmt: skip
PERSONAL_FIRST = {"welcome_to_the_jungle": frozenset({"me", "account"})}
PERSONAL_HOSTS = {"indeed": frozenset({"my"})}


def _address(url: str) -> tuple[str, str | None, int | None, str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port, parts.path or "/", parts.query


def start_feed(url: object, source: object, start: object) -> bool:
    """Whether `url` is LinkedIn's own redirect of the start address to its feed: the address
    `source` the redirect came from is the saved start (scheme, host, port, path and query), and
    `url` is exactly `/feed/` of the same scheme, host and port, with no query and no fragment. Not
    `/feed/update/...`, not any other path, not a redirect from another address."""
    family = family_of(start)
    if (
        family is None
        or family.platform != "linkedin"
        or family_of(url) is not family
        or not isinstance(url, str)
        or not isinstance(source, str)
        or not isinstance(start, str)
    ):
        return False
    try:
        target = urlsplit(url)
        return (
            target.path == "/feed/"
            and not target.query
            and not target.fragment
            and _address(source) == _address(start)
            and _address(url)[:3] == _address(start)[:3]
        )
    except ValueError:
        return False


def personal(
    family: Family | None, host: object, segments: list[str], *, feed: bool = False
) -> bool:
    """Whether a page of the family is a person's or the person's own list (above). `segments`
    are the path's, already percent decoded; `feed` is `start_feed`'s answer for the address."""
    if family is None:
        return False
    label = str(host or "").lower().removesuffix("." + family.domain)
    if label in PERSONAL_HOSTS.get(family.platform, ()):
        return True
    glued = ["".join(detect.words(segment, camel=False)) for segment in segments]
    anywhere = PERSONAL_SEGMENTS.get(family.platform, frozenset())
    if feed:
        anywhere = anywhere - {"feed"}
    if any(g in anywhere for g in glued):
        return True
    first = PERSONAL_FIRST.get(family.platform, frozenset())
    heads = glued[:2] if segments and _language(segments[0]) else glued[:1]
    return any(g in first for g in heads)


SLUG = re.compile(r"[A-Za-z0-9_-]{1,200}")
JOB_KEY = re.compile(r"[A-Za-z0-9]{1,40}")


def _linkedin_job(segment: str) -> bool:
    """A LinkedIn posting's id: digits, or a lowercase slug then a hyphen and the digits."""
    head, _, digits = segment.rpartition("-")
    return (
        0 < len(digits) <= 20
        and digits.isascii()
        and digits.isdecimal()
        and len(head) <= 200
        and all(c.isascii() and (c.islower() or c.isdecimal() or c == "-") for c in head)
    )


def posting(family: Family, segments: list[str], pairs: list[tuple[str, str]]) -> bool:
    """A posting's page of the platform, by its path (B19), whatever its tracking query:
    LinkedIn `/jobs/view/<id>`, Indeed `/viewjob?jk=<key>` and `/rc/clk?jk=<key>`, Welcome to the
    Jungle `/<language>/companies/<company>/jobs/<slug>`."""
    if family.platform == "linkedin":
        return (
            len(segments) == 3 and segments[:2] == ["jobs", "view"] and _linkedin_job(segments[2])
        )
    if family.platform == "indeed":
        keys = [value for key, value in pairs if key == "jk"]
        return (
            segments in (["viewjob"], ["rc", "clk"])
            and len(keys) == 1
            and JOB_KEY.fullmatch(keys[0]) is not None
        )
    return (
        family.platform == "welcome_to_the_jungle"
        and len(segments) == 5
        and _language(segments[0])
        and segments[1] == "companies"
        and segments[3] == "jobs"
        and SLUG.fullmatch(segments[2]) is not None
        and SLUG.fullmatch(segments[4]) is not None
    )


def listing(family: Family, path: str) -> bool:
    """A result list of the platform (B19): its search page (`search_path`: `/jobs`,
    `/fr/jobs`), and LinkedIn's `/jobs/search`, `/jobs/search-results` and the "see all" lists
    `/jobs/collections/<name>`."""
    if search_path(path):
        return True
    segments = path.strip("/").split("/")
    return family.platform == "linkedin" and (
        segments in (["jobs", "search"], ["jobs", "search-results"])
        or (
            len(segments) == 3
            and segments[:2] == ["jobs", "collections"]
            and SLUG.fullmatch(segments[2]) is not None
        )
    )


def page_url(url: object, scope: Scope, *, hop: bool = False, source: str | None = None) -> bool:
    """A document a signed in discovery may open (B19): what read_url lets through within the
    frozen scope, or, on the start's family, an address with no user part, no fragment, no
    control character and no `%` left after one decode, no page of a person or of the person's
    own lists (`personal`, read_url's rule too), that is a redirect hop (`hop`: a
    redirect within the family is followed) or a result list (`listing`) and reaches no apply,
    submit, delete, payment or account page by its path words, or a posting (`posting`), whose
    path words are its title (read by `destructive` alone, as every request's are). A result
    list's search key, if any, holds a saved title or nothing."""
    if not isinstance(url, str) or len(url) > 2000:
        return False
    if read_url(url, scope.start, scope=scope, hop=hop, source=source):
        return True
    family = family_of(scope.start)
    if family is None or family_of(url) is not family:
        return False
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return False
    path = unquote(parts.path)
    if parts.fragment or "%" in path or any(ord(c) <= 32 or 127 <= ord(c) <= 159 for c in url):
        return False
    segments = path.strip("/").split("/")
    if personal(
        family, parts.hostname, segments, feed=hop and start_feed(url, source, scope.start)
    ):
        return False
    if posting(family, segments, pairs):
        return True
    if _path_refused(segments):
        return False
    return hop or (
        listing(family, path)
        and all(not v or saved_as(v, scope.titles) is not None for k, v in pairs if k in QUERY)
    )


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


def signin_url(url: str, scope: Scope | None = None) -> bool:
    """A sign in page's address by its path (B17), or any page of a sign in host of the start's
    family (B19: `secure.indeed.com`): a signed in discovery sent there has lost the person's
    login, which the api tells as `platform_signin`."""
    try:
        parts = urlsplit(url)
        segments = parts.path.lower().split("/")
    except (ValueError, AttributeError):
        return False
    family = family_of(scope.start) if scope is not None else None
    if family is not None and parts.hostname in {f"{h}.{family.domain}" for h in family.signin}:
        return True
    folded = ["".join(ch for ch in segment if ch.isalnum()) for segment in segments]
    return any(f.startswith(SIGNIN_PATHS) for f in folded if f)


# B19: read inside every glued path segment, query key and query value of a page's POST, the
# person's own search words aside: a POST whose address says it follows, connects, messages,
# invites, saves, bookmarks, sets an alert (a saved search), favourites, subscribes, reacts, likes
# or shares never leaves, whatever its host (owner, 2026-10-29: "no ... save, message, follow or
# connect"); its body is not read. `favori` holds `favorite`, `Favoriten` and the French `favori`
# and `favoris`; `subscri` holds `subscribe` and `subscription`; `reaction` holds LinkedIn's
# `voyagerSocialDashReactions`, and leaves `react-aria-12` and `preact` alone. POST_WORDS are
# ACTION_STEMS too. `like` and `share` are short words inside others (`likely`, `sharepoint`):
# POST_WHOLE reads them as whole words only (`likes`, `normShares`, `unlike`, `?do=like`), each
# word of a path segment, key or value read split on camel case and not.
POST_WORDS = (
    "follow", "connect", "messag", "invit", "save", "bookmark", "alert", "favori", "favour",
    "subscri", "reaction",
)  # fmt: skip
POST_WHOLE = re.compile(
    r"(?:un|dis|re)?(?:likes?|liked|liking|likers?|shares?|shared|sharing|sharers?)"
)
# A search service's client metadata, read as a key and never as a value: Algolia's JavaScript
# client puts its library list into `x-algolia-agent` (`Algolia for JavaScript (4.x); Browser;
# react (18.x); react-instantsearch (7.x)`), and the key `x-algolia-api-key` is a random string.
# Skipped only on a host of the platform's search service (Family.search).
CLIENT_KEYS = frozenset({"x-algolia-agent", "x-algolia-api-key", "x-algolia-application-id"})


def posts(url: object, method: object, kind: object, scope: Scope) -> bool:
    """Whether a signed in page's background request may POST (B19): a fetch or an XHR, never a
    document, a beacon or a form, to the start's family or a host of its platform's search
    service (`Family.search`), reaching no address `destructive` or POST_WORDS refuses (the
    values of CLIENT_KEYS on that service aside). A website on no family posts nothing."""
    family = family_of(scope.start)
    if (
        method != "POST"
        or kind not in {"fetch", "xhr"}
        or family is None
        or not isinstance(url, str)
        or not (family_of(url) is family or _search_service(url, family))
    ):
        return False
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return False
    own = own_words(scope)
    service = _search_service(url, family)
    texts = [*unquote(parts.path).split("/"), *(k for k, _ in pairs)]
    texts.extend(
        v
        for k, v in pairs
        if tuple(detect.words(v)) not in own and not (service and k in CLIENT_KEYS)
    )
    glued = ["".join(detect.words(text)) for text in texts]
    return not (
        any(word in text for text in glued for word in POST_WORDS)
        or any(POST_WHOLE.fullmatch(w) for text in texts for w in detect.match_words(text))
    )


def signed_request(
    url: str,
    method: str,
    document: bool,
    scope: Scope,
    *,
    hop: bool = False,
    kind: str = "",
    source: str | None = None,
) -> bool:
    """Whether a request of a signed in discovery page may leave (B17): GET or HEAD only, a
    page's fetch or XHR POST to its website (`posts`, B19) aside, never a destructive address on
    any host (a document's too: `/logoff` or `/fr/deconnexion` in a frame would sign the person
    out), and a document (a frame's navigation) only within the frozen scope or a page of the
    start's family (`page_url`). A redirect hop (`hop`) names the address it came from
    (`source`, `start_feed`)."""
    if not isinstance(url, str) or len(url) > 8000:
        return False
    if document:
        if method not in {"GET", "HEAD"} or not page_url(url, scope, hop=hop, source=source):
            return False
    elif method not in {"GET", "HEAD"} and not posts(url, method, kind, scope):
        return False
    return not destructive(url, scope)


def _latin(name: str) -> bool:
    """Every letter and digit of the name, in NFKC with its accents aside, is basic Latin: no
    other script, no homoglyph (a Cyrillic small es inside "Accept"), nothing a folded reading
    drops."""
    text = unicodedata.normalize("NFKC", name)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return all(c.isascii() for c in text if unicodedata.category(c)[0] in "LN")


def _declines(facts: dict) -> bool:
    """Whether these facts are of a cookie banner's decline button (DECLINES, NEUTRAL)."""
    names = facts.get("names")
    if not isinstance(names, list) or not names or len(names) > 60:
        return False
    if not all(isinstance(n, str) and _latin(n) for n in names):
        return False
    phrases = [tuple(detect.words(n, camel=False)) in DECLINES for n in names]
    if not any(phrases):
        return False
    for name, phrase in zip(names, phrases, strict=True):
        # Its words with camel case split (`CybotCookiebotDialogBodyButtonDecline`): an allow
        # list, so a split spelling of another word (`AccEpt`) is no neutral word either.
        if not phrase and not all(w in NEUTRAL or w.isdigit() for w in detect.words(name)):
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


# B19: which saved values a text box takes, told from its names (its label, aria-label,
# placeholder, name and id) as the api's finder.box_of tells it from the snapshot: the same words.
# A box is a place box when a name holds a place word and no title word, and no name the other
# way; a title box the other way; else it cannot tell, and either kind of saved value goes.
PLACE_WORDS = frozenset(
    {"location", "locations", "localisation", "localization", "lieu", "lieux", "ville", "villes",
     "city", "cities", "town", "where", "region", "regions", "departement", "departements",
     "postcode", "zip", "zipcode", "postal", "country", "pays", "commune", "address", "adresse",
     "place", "standort", "ort", "stadt"}
)  # fmt: skip
TITLE_WORDS = frozenset(
    {"job", "jobs", "title", "titles", "keyword", "keywords", "motcle", "motscles", "motcles",
     "poste", "postes", "metier", "metiers", "emploi", "emplois", "intitule", "what", "quoi",
     "role", "roles", "skill", "skills", "competence", "competences", "company", "entreprise",
     "query", "search", "recherche", "rechercher", "stelle", "stellen", "beruf"}
)  # fmt: skip
BOX_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*")


def _box_name(name: str) -> str | None:
    folded = unicodedata.normalize("NFKC", name[:500]).casefold()
    raw = BOX_WORD.findall(folded)
    words: set[str] = set()
    for token in raw:
        plain = "".join(
            ch for ch in unicodedata.normalize("NFKD", token) if not unicodedata.combining(ch)
        )
        words.update(plain.split("-"))
        words.add(plain.replace("-", ""))
    place = "où" in {part for token in raw for part in token.split("-")} or bool(
        words & PLACE_WORDS
    )
    title = bool(words & TITLE_WORDS)
    return None if place == title else "place" if place else "title"


def box_kind(names: object) -> str | None:
    if not isinstance(names, list):
        return None
    kinds = {_box_name(n) for n in names[:60] if isinstance(n, str)} - {None}
    return kinds.pop() if len(kinds) == 1 else None


# B19, round 1: a text box whose names say it writes to someone or to the public (a message, a
# reply, a comment, a chat, a note or a post, in English, French and German) is never a search
# box, whatever its kind and wherever it is: Enter there would send the saved title, and a chat
# composer is often a textarea of one line. Every name of the box (its label, aria-label,
# placeholder, title, name, id and the text within) is read as detect.match_words folds it
# (NFKC, format characters, accents and case aside, camel case split and not): a word of
# COMPOSE_WORDS refuses, and in a name of one token with no space (an id or a name attribute,
# `msgComposer`, `replybox`) a COMPOSE_STEMS stem inside any of its words refuses too. "poste"
# (a job title box in French), "postal", "postcode", "posting" and "writer" are no such word.
COMPOSE_WORDS = frozenset(
    {"message", "messages", "messaging", "messagerie", "messageries", "msg", "inmail",
     "nachricht", "nachrichten", "reply", "replies", "replying", "repondre", "repondez",
     "reponds", "reponse", "reponses", "antworten", "antwort", "antworte", "comment", "comments",
     "commenting", "commenter", "commentez", "commentaire", "commentaires", "kommentar",
     "kommentare", "kommentieren", "chat", "chats", "chatting", "tchat", "chatten", "note",
     "notes", "noter", "notiz", "notizen", "post", "posts", "poster", "posten", "publier",
     "publiez", "publish", "publication", "publications", "beitrag", "beitrage", "write",
     "writes", "writing", "ecrire", "ecrivez", "ecris", "schreiben", "schreib", "schreibe"}
)  # fmt: skip
COMPOSE_STEMS = (
    "messag", "msg", "inmail", "nachricht", "reply", "repli", "repond", "repons", "antwort",
    "comment", "kommentar", "chat", "note", "notiz", "publier", "publiez", "publish", "beitrag",
    "writ", "ecrir", "ecriv", "schreib",
)  # fmt: skip


def composes(facts: dict) -> bool:
    """Whether a text box's names say it writes to someone (COMPOSE_WORDS). Fails closed: names
    that cannot be read, or more than FACTS_JS gives, compose."""
    names = facts.get("names")
    if not isinstance(names, list) or len(names) > 60:
        return True
    for name in names:
        if not isinstance(name, str):
            return True
        tokens = detect.match_words(name[:500])
        if any(t in COMPOSE_WORDS for t in tokens):
            return True
        if IDENTIFIER.fullmatch(name.strip()) and any(
            stem in t for t in tokens for stem in COMPOSE_STEMS
        ):
            return True
    return False


def search_box(facts: dict) -> bool:
    """A text box a signed in search may type in (B19): an input of type text or search, a
    textarea of one line, or an editable element with the role searchbox or combobox, whose
    names say it writes to no one (`composes`)."""
    if composes(facts):
        return False
    tag, kind, role = facts.get("tag"), facts.get("type"), facts.get("role")
    if tag == "input":
        return kind in {"text", "search"}
    if tag == "textarea":
        return facts.get("rows") == 1
    return facts.get("editable") is True and role in {"searchbox", "combobox"}


# B19: a button or link a signed in search may click by its names (owner, 2026-10-29: "press
# Search, as a person would"). An allow list: every name of the element (its text, aria-label,
# title, value, name, id and the names within it), read in NFKC with format characters, accents
# and case aside, with its camel case split or not, is one of these phrases or holds no word at
# all once the SEARCH_NEUTRAL words and the numbers are taken out, and one name is a phrase. A
# name of one token with no space (an id or a name attribute, `go`, `global-nav-search`) is no
# label a person reads: it passes unless it holds an ACTION_STEMS stem anywhere (`btnFollow`,
# `easyapply`). A name with a letter or a digit outside basic Latin refuses (`_latin`). So "Save
# search", "Search history", "Easy Apply" or "Follow" never pass, and the submit, apply and
# danger detectors are read before this.
SEARCH_PHRASES = frozenset(
    tuple(phrase.split())
    for phrase in (
        "search", "find", "rechercher", "recherche", "lancer recherche", "trouver", "suchen",
        "suche", "suche starten", "finden",
    )
)  # fmt: skip
SEARCH_NEUTRAL = frozenset(
    {"job", "jobs", "emploi", "emplois", "offre", "offres", "poste", "postes", "stelle", "stellen",
     "a", "an", "the", "for", "un", "une", "des", "les", "le", "la", "de", "du", "d", "l", "nach",
     "eine", "einen", "der", "die", "das", "button", "btn", "icon", "svg", "img", "image",
     "magnifier", "magnifying", "glass", "loupe", "lupe", "lens"}
)  # fmt: skip


# What an id or a name attribute of a search button never holds, read inside its glued words:
# applying, following, connecting, messaging, saving, sharing, alerting, reporting, blocking, an
# account's pages, in English, French and German.
ACTION_STEMS = (
    *APPLY_PARTS, *POST_WORDS, "share", "partag", "teilen", "alert", "report", "signal",
    "block", "bloqu", "suivr", "abonn", "folg", "speicher", "vernetz", "nachricht", "login",
    "logout", "signin", "signout", "signup", "register", "account", "compte", "profil",
    "delete", "remov", "suppr", "like", "upload",
)  # fmt: skip
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,99}")


def _search_name(name: str) -> str:
    """'phrase', 'neutral', or '' for a name that refuses."""
    for camel in (False, True):
        words = [
            w
            for w in detect.words(name, camel=camel)
            if w not in SEARCH_NEUTRAL and not w.isdigit()
        ]
        if not words:
            return "neutral"
        if tuple(words) in SEARCH_PHRASES:
            return "phrase"
    if IDENTIFIER.fullmatch(name.strip()):
        glued = "".join(detect.words(name))
        return "" if any(stem in glued for stem in ACTION_STEMS) else "neutral"
    return ""


def search_words(facts: dict) -> bool:
    names = facts.get("names")
    if not isinstance(names, list) or not names or len(names) > 60:
        return False
    if not all(isinstance(n, str) and _latin(n) for n in names):
        return False
    kinds = [_search_name(n) for n in names]
    return "" not in kinds and "phrase" in kinds


def _search_form(facts: dict, d: dict, scope: Scope) -> bool:
    """A control a signed in search may use is in no form, or in a GET form whose action is a
    search page or result list of its website (`listing`, or an ENDPOINTS last segment) that
    applies, submits, deletes, pays and signs out nothing: the request it makes is held to
    `signed_request` again."""
    if not facts.get("in_form"):
        return True
    action = d.get("action")
    if d.get("method") != "get" or not isinstance(action, str) or len(action) > 2000:
        return False
    if not on_site(action, scope.start) or destructive(action, scope):
        return False
    try:
        path = unquote(urlsplit(action).path)
    except ValueError:
        return False
    segments = path.strip("/").split("/")
    family = family_of(scope.start)
    return not _path_refused(segments) and (
        segments[-1] in ENDPOINTS or (family is not None and listing(family, path))
    )


def _opens(url: object, scope: Scope) -> bool:
    """A link a signed in search may follow: a page it may open (`page_url`) that is not
    `destructive`."""
    return isinstance(url, str) and page_url(url, scope) and not destructive(url, scope)


def _in_page(url: object, current: str) -> bool:
    """A link that stays on the page: no address, a fragment of the page, or a script."""
    return isinstance(url, str) and (
        not url or url.startswith("javascript:") or url.split("#", 1)[0] == current.split("#")[0]
    )


def _signed_in_control(
    facts: dict, d: dict, tool: str, action: str, current: str, scope: Scope, args: dict | None
) -> bool:
    """B19, signed in: a search as a person makes it on a real website. A text box (`search_box`)
    takes one saved title or place (`Scope.types`) and Enter; a button or link named a search
    word (`search_words`) is clicked, a form it submits held to `_search_form`; a link to a page
    of the website (`_opens`) is followed."""
    if tool == "browser_type":
        if not search_box(facts) or not _search_form(facts, d, scope):
            return False
        return action == "key" or scope.types(facts.get("names"), (args or {}).get("text"))
    if tool != "browser_click" or facts.get("editable") or facts.get("credential"):
        return False
    tag, kind, role = facts.get("tag"), facts.get("type"), facts.get("role")
    if tag == "a":
        href = d.get("href", "")
        return _opens(href, scope) or (search_words(facts) and _in_page(href, current))
    button = (
        tag == "button"
        or (tag == "input" and kind in {"button", "submit", "image"})
        or (role in {"button", "link"} and tag not in {"input", "select", "textarea"})
    )
    submits = facts.get("in_form") and tag in {"button", "input"} and kind in {"submit", "image"}
    return (
        button
        and search_words(facts)
        and not facts.get("holds_submit")
        and (not submits or _search_form(facts, d, scope))
    )


def verdict(
    facts: object,
    tool: str,
    current: str,
    *,
    scope: Scope | None = None,
    args: dict | None = None,
    action: str | None = None,
) -> str | None:
    """None when a discovery may act on this element, else the refusal's code. `action` is
    `key` for the Enter browser_type presses after its text (B19, signed in only): the detectors
    read it as Enter, the text having been judged as a typing."""
    if not isinstance(facts, dict):
        return UNSUPPORTED
    action = action or (
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
    if tool == "browser_type" and composes(facts):  # a composer, never a search box (B19)
        return UNSUPPORTED
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
    if signed_in and _signed_in_control(facts, d, tool, action, current, scope, args):
        allowed = True
    elif action == "key":  # Enter only in a signed in search's text box (B19)
        allowed = False
    elif tool == "browser_click" and facts.get("tag") == "a":
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
    if allowed and action != "key" and "point" in facts:
        return verdict(facts["point"], tool, current, scope=scope, args=args, action=action)
    return None if allowed else UNSUPPORTED
