"""The line of the website search (browser B20, owner 2026-11-04: "I want the exact same way I'm
doing on my mac on my jetson"). The search agent of a sweep lease may do anything a person does
in a browser, with every Playwright CLI command, on any public address, except what needs the
person's approval, which on the Jetson is the apply gate's alone (hard rules 3 and 4). Whatever
it uses (a click, Enter, a key, a script it runs on the page, a request the page sends), it never:

- submits an application or a form that is not a search or a cookie or consent choice;
- sends anything to a person (a message, a reply, an invitation or connection request, a comment
  or a post, a mail);
- pays or enters payment details, signs out, deletes or changes the account, its profile or its
  settings;
- types a password, a code or any credential (signing in is the person's, in the live view).

Two floors hold it in code:

- **Controls** (`control`): before a click, a double click, Enter, Space or a typing, the facts of
  the element (detect.FACTS_JS) go to browserd's own detectors (detect.verdict: submit, danger,
  card fields, credentials), then to the words of the line (LINE_STEMS, LINE_PHRASES: apply,
  message, connect, invite, comment, post, sign out, account, in English, French and German; on
  a link, signing out, Connect and the account's own pages: LINK_STEMS, LINK_PHRASES). A
  submit the detector sees only by structure (a `type=submit` button, Enter in a form's field) is
  let through in a search or consent form: a GET form, or a form holding no field a person writes
  to someone or applies with (no multi line text, no file, no e-mail, telephone or password box,
  no editable region) and whose control's names say no submit word. Save job, Follow, Accept
  cookies, Search and Next page pass (the tables of api/tests/browser/test_b20_line.py).
- **Requests** (`request_refused`): every request of a sweep's context goes through browserd's
  route. A sign out address is refused whatever the method and kind (`signs_out`), and a DELETE,
  a PUT or a PATCH to a session or a token. A document (a navigation, a form's submission) is
  refused when it is a POST, unless its path says a search or a consent choice
  (`search_or_consent`); a fetch, an XHR, a beacon or
  any other POST is refused when its address says apply, submit, message, invite, connect,
  comment, payment, sign out or account change (POST_INSIDE, POST_STEMS: its path, query keys
  and query values, a search client's metadata aside), or is too long to be read whole. Any
  other GET or HEAD leaves (any public host: browserd's egress proxy and the host firewall keep
  private, loopback, tailnet and link local addresses out).

What code cannot see is declared: a script that builds its own request by a means the route
does not see (a WebSocket, a service worker's own fetch, which only the site's code can start)
leaves; a POST body is never read, only its address; a control with no word of the line in any
name, outside a form, whose script sends a message (an icon only button) is clicked. The
agent's own scripts (`browser_evaluate`) run inside SCRIPT_GUARD_JS, which judges
`element.click()` on detect.FACTS_JS's facts by these rules and words, refuses `form.submit()`
and `form.requestSubmit()` on a form that is no search form, and whose requests meet the same
route: that, not the script, is the last line.
"""

import re
from typing import Any
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit

from browserd import detect

# A word starting with one of these, on a button or a link, is the line (detect's SUBMIT_STEMS
# and DANGER_STEMS hold the rest: submit, send, apply, postuler, delete, pay, checkout...).
LINE_STEMS = (
    "bewerb",  # Bewerben, Jetzt bewerben, Bewerbung absenden
    "absend",
    "abschick",
    "einreich",
    "senden",  # Nachricht senden
    "message",
    "messag",
    "nachricht",
    "repond",  # Répondre
    "antwort",
    "invit",  # Invite, Inviter
    "einlad",
    "vernetz",  # Vernetzen (LinkedIn's Connect in German)
    "comment",
    "kommentar",
    "kommentier",
    "repost",
    "partag",  # Partager
    "teilen",
    "share",
    "logout",
    "signout",
    "deconnex",  # Déconnexion
    "deconnect",  # Se déconnecter
    "abmeld",  # Abmelden
    "auslogg",
    "losch",  # Löschen, Konto löschen
    "bezahl",
    "zahlung",
    "kaufen",
    "kasse",
)
# Whole words only (a stem would refuse what a search shows: "Connecticut", "Postgres",
# "Replenishment", "Publié le"). The words of the line are read on buttons only: a posting
# link's name is its title ("Post-doc", "SharePoint Engineer"), and a link only opens a page (a
# GET); what sends a message is a POST, which the request floor refuses. Signing out and the
# account's own pages are read on links too (LEAVE_STEMS, LINK_PHRASES below).
LINE_WORDS = frozenset(
    {
        "connect",
        "connecter",
        "post",
        "poster",
        "reply",
        "publish",
        "publier",
        "mail",
        "email",
        "dm",
        "chat",
        "pay",
        "veroffentlichen",
    }
)
# Two words together (folded): an account or profile change, a French Easy Apply.
LINE_PHRASES = (
    ("sign", "out"),
    ("log", "out"),
    ("sign", "off"),  # a button or a menu item only: on a link, a title ("Sign-off Engineer")
    ("candidature", "simplifi"),
    ("delete", "account"),
    ("supprimer", "compte"),
    ("close", "account"),
    ("edit", "profile"),
    ("modifier", "profil"),
    ("profil", "bearbeit"),
    ("change", "password"),
    ("account", "settings"),
    ("parametres", "compte"),
    ("kontoeinstellung",),
)
# Read on a link as on a button (review of B20, round 1: LinkedIn's "Sign Out" in its Me menu is
# `<a href="/m/logout/">`, Indeed's "Sign out" a link too, and a GET signs out): a word starting
# with one of LEAVE_STEMS, a whole word of LINK_WORDS (LinkedIn's Connect link, "Invite Jane Roe
# to connect"), or the whole words of a phrase side by side, one word between at most ("Supprimer
# le compte", "Change your password"). Never a word's start in a phrase: "Logistics Outbound",
# "Sign Language Interpreter, Outreach" and "Blog Outreach" are posting titles. "Log off" too
# (review of B20, round 2), but not "Sign off", a posting's title on a link ("Sign-off
# Engineer"): read on a button or a menu item only (LINE_PHRASES), and a link's address is read.
LEAVE_STEMS = ("logout", "signout", "logoff", "deconnex", "deconnect", "abmeld", "auslogg")
LINK_STEMS = (*LEAVE_STEMS, "kontoeinstellung")
LINK_WORDS = frozenset({"connect"})
LINK_PHRASES = (
    ("sign", "out"),
    ("log", "out"),
    ("log", "off"),
    ("fermer", "session"),  # Windows' French Log off, "Fermer la session"
    ("candidature", "simplifiee"),
    ("close", "account"),
    ("delete", "account"),
    ("supprimer", "compte"),
    ("konto", "loschen"),
    ("edit", "profile"),
    ("modifier", "profil"),
    ("profil", "bearbeiten"),
    ("change", "password"),
    ("account", "settings"),
    ("parametres", "compte"),
)
# The fields of a form a person writes to someone, applies or signs in with: a form holding one
# is never a search form, so its submission is refused (`tag:type`, detect.FACTS_JS form_kinds).
WRITES = frozenset(
    {"textarea:", "textarea:textarea", "input:file", "input:email", "input:tel", "input:password"}
)
SEARCH_STEMS = ("search", "recherch", "such", "find", "trouv", "jobs", "emploi", "stellen")


def _starts(tokens: list[str], stems: tuple[str, ...]) -> bool:
    return any(token.startswith(stem) for token in tokens for stem in stems)


def says_line(names: list[str]) -> bool:
    """A name of the element says a step of the line (LINE_STEMS, LINE_WORDS, LINE_PHRASES)."""
    tokens = [w for name in names for w in detect.match_words(name)]
    if _starts(tokens, LINE_STEMS) or any(token in LINE_WORDS for token in tokens):
        return True
    return any(all(_starts(tokens, (word,)) for word in phrase) for phrase in LINE_PHRASES)


def _near(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    first, last = phrase
    return any(t == first and last in tokens[i + 1 : i + 3] for i, t in enumerate(tokens))


def says_leave(names: list[str]) -> bool:
    """A name of a link or a button says signing out or the account's own pages (LINK_STEMS,
    LINK_WORDS, LINK_PHRASES), each name read alone."""
    for name in names:
        tokens = detect.match_words(name)
        if _starts(tokens, LINK_STEMS) or any(token in LINK_WORDS for token in tokens):
            return True
        if any(_near(tokens, phrase) for phrase in LINK_PHRASES):
            return True
    return False


def search_form(facts: dict[str, Any]) -> bool:
    """A form whose submission is a search or a consent choice: GET, or one holding no field a
    person writes to someone, applies or signs in with (WRITES, an editable region)."""
    method = _method(facts)
    kinds = [k for k in facts.get("form_kinds") or [] if isinstance(k, str)]
    if any(k in WRITES or k.endswith(":editable") for k in kinds):
        return False
    if method == "get":
        return True
    return len(kinds) <= 40  # a consent form, a search form posted (an old career site)


# An application's noun on a button of a form that is no GET one: never a search, whatever
# verb is beside it ("Search application", "Candidature", "Bewerbung suchen"; part A of B20).
APPLICATION_STEMS = ("applica", "candidat", "bewerb", "postul")


def _method(facts: dict[str, Any]) -> str:
    form = facts.get("discovery")  # FACTS_JS: the form's method is `discovery.method`
    return str(form.get("method") or "").lower() if isinstance(form, dict) else ""


def _structural(facts: dict[str, Any], action: str) -> bool:
    """The detector's `submit` came from the element's structure alone (its type, the submit it
    holds, Enter in a form's field), never from a lease selector, a submit word, or an
    application's noun on a button of a form that is not sent by GET (APPLICATION_STEMS)."""
    if facts.get("selector_hit") or facts.get("custom_submit"):
        return False
    names = [n for n in facts.get("names") or [] if isinstance(n, str)]
    if detect.button_like(facts) and detect.says_submit(names):
        return False
    if _method(facts) != "get" and _starts(detect._glued(names), APPLICATION_STEMS):
        return False
    return action in ("click", "key", "check")


def button(facts: dict[str, Any]) -> bool:
    """A button like control that is no link (detect.button_like less `a` and role link)."""
    return (
        detect.button_like(facts)
        and facts.get("tag") != "a"
        and str(facts.get("role") or "") != "link"
    )


def control(facts: Any, action: str) -> str | None:
    """None when the sweep's agent may act on the element, else the refusal's code (detect's
    codes, or `line` for a word of the line). `action` is click, check, type or key."""
    code = detect.verdict(facts, action=action)
    if code == "submit" and isinstance(facts, dict):
        own = facts
        point = facts.get("point") if action in detect.POINTED else None
        judged = [own, point] if isinstance(point, dict) else [own]
        if all(_structural(f, action) and search_form(f) for f in judged):
            code = None
    if code is not None:
        return code
    if not isinstance(facts, dict):
        return "unreadable"
    for judged_facts in (facts, facts.get("point")):
        if not isinstance(judged_facts, dict):
            continue
        names = [n for n in judged_facts.get("names") or [] if isinstance(n, str)]
        if action != "type" and button(judged_facts) and says_line(names):
            return "line"
        if action != "type" and detect.button_like(judged_facts) and says_leave(names):
            return "line"
    return None


# What a POST address may not say, read in its path segments, its query keys and its query
# values (CLIENT_KEYS aside), each folded and cut into words (`/voyager/api/messaging/...`,
# `?action=apply`, `sendInvitation`, `/checkout`, `/logout`, `/account/delete`). POST_INSIDE is
# read inside every word glued (B19's reading, part B of B20: `/api/quickapply`,
# `/jobs/42/easyapply`, `/api/sendmessage` say it as `/apply` does); POST_STEMS, short or common
# inside other words (`display`, `gmail`, `sharepoint`), at the start of a word only.
POST_INSIDE = (
    "apply",
    "applica",
    "candidat",
    "postul",
    "bewerb",
    "submit",
    "messag",
    "conversation",
    "invit",
    "logout",
    "signout",
    "checkout",
    "sendmail",
)
POST_STEMS = (
    "connect",
    "relationship",
    "comment",
    "reply",
    "share",
    "publish",
    "pay",
    "purchas",
    "billing",
    "subscri",
    "account",
    "profile",
    "password",
    "setting",
    "delete",
    "remove",
    "mail",
)
POST_PHRASES = (("sign", "out"), ("log", "out"))
# A search service's client metadata, never read (B19): Algolia's JavaScript client lists its
# libraries in `x-algolia-agent` ("react-instantsearch", "e2e-connect-ui"), its keys are random
# strings, and `x-algolia-application-id` holds "application". Welcome to the Jungle searches
# this way.
CLIENT_KEYS = frozenset({"x-algolia-agent", "x-algolia-api-key", "x-algolia-application-id"})
# A document POST that is a search or a consent choice says so, inside a word of its path
# (`/search`, `/careersection/jobsearch.ftl`, `/recherche`, `/Suche`) or, for a consent, of its
# host too (`consent.example.org/save`, `/cookie-consent`, `/gdpr`). Never in its query: a
# posting's own address carries tracking values (`/jobs/4012345?gh_src=jobsearch`, `?ref=search`),
# and an application form posted to its own page's address is that address (review of B20,
# round 1). Never "jobs": an ATS's application form posts to `/<company>/jobs/<id>` (part B).
SEARCH_INSIDE = ("search", "recherch", "suche", "query", "queries")
CONSENT_INSIDE = ("consent", "cookie", "gdpr")
URL_MAX = 4096  # a POST to a longer address is refused: what lies past it is never read


def _texts(url: str) -> tuple[list[str], list[str], list[str]] | None:
    """The path segments, the query keys and values (CLIENT_KEYS aside) of an address, and its
    host's labels; None when it cannot be read whole (fail closed)."""
    if len(url) > URL_MAX:
        return None
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=200)
        host = parts.hostname or ""
    except ValueError:
        return None
    own = [(k, v) for k, v in pairs if k.lower() not in CLIENT_KEYS]
    query = [*(k for k, _ in own), *(v for _, v in own)]
    return unquote(parts.path).split("/"), query, host.split(".")


def _inside(texts: list[str], stems: tuple[str, ...]) -> bool:
    glued = ["".join(detect.words(text)) for text in texts]
    return any(stem in text for text in glued for stem in stems)


def post_refused(url: str) -> bool:
    """A POST address that says a step of the line (POST_INSIDE, POST_STEMS, POST_PHRASES), or
    one that cannot be read whole."""
    read = _texts(url)
    if read is None:
        return True
    texts = [*read[0], *read[1]]
    if _inside(texts, POST_INSIDE):
        return True
    words = [w for text in texts for w in detect.match_words(text)]
    if _starts(words, POST_STEMS):
        return True
    return any(all(_starts(words, (w,)) for w in phrase) for phrase in POST_PHRASES)


def search_or_consent(url: str) -> bool:
    """A document POST's path says a search or a consent choice (SEARCH_INSIDE,
    CONSENT_INSIDE), or its host a consent; never its query."""
    read = _texts(url)
    if read is None:
        return False
    path, _, labels = read
    return _inside(path, SEARCH_INSIDE) or _inside([*path, *labels], CONSENT_INSIDE)


# A sign out address (review of B20, round 1): no request of any method or kind leaves for it,
# a link, a navigation, `location.href`, a popup, a fetch or an image alike. Read in the path
# segments, query keys and query values, decoded up to DECODED times (a redirect's target is
# often encoded twice), as words: one starting with LEAVE_STEMS, or "sign out" or "log out"
# side by side (`/m/logout/`, `/auth/logout`, `/sign-out`, `/log/out`, `signOut`,
# `?continue=%2Flogout`, `/Account/LogOff`, `/log-off`), or a word ending with LEAVE_ENDS
# (`/dologout`, `/userlogout`, `/applogoff.aspx`), never inside a glued word
# (`/blog-outreach-manager`, `/blogoutreach-lead`). Never "sign off" side by side or glued: a
# posting's address says `/jobs/sign-off-engineer-42` or `sta-signoff-engineer`. A DELETE, a PUT
# or a PATCH may not say a session or a token either (`DELETE /api/v1/sessions`, Welcome to the
# Jungle's way out). An address past READ_MAX characters is refused (a server answers 414 far
# below it).
LEAVE_PHRASES = (("sign", "out"), ("log", "out"), ("log", "off"))
LEAVE_ENDS = ("logout", "signout", "logoff")
SESSION_STEMS = ("session", "token")
READ_MAX = 32768
DECODED = 3


def address_words(url: str) -> list[str] | None:
    """The words of an address's path and query (decoded), in order; None past READ_MAX."""
    if len(url) > READ_MAX:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    texts = [*parts.path.split("/"), *re.split(r"[&;=]", parts.query)]
    found: list[str] = []
    for text in texts:
        for _ in range(DECODED):
            text = unquote_plus(text)
        found.extend(detect.match_words(text))
    return found


def signs_out(url: object) -> bool:
    """The address signs out (LEAVE_STEMS, LEAVE_ENDS, LEAVE_PHRASES), or cannot be read to
    tell."""
    if not isinstance(url, str):
        return True
    words = address_words(url)
    if words is None:
        return True
    return (
        _starts(words, LEAVE_STEMS)
        or any(word.endswith(LEAVE_ENDS) for word in words)
        or any(detect._phrase(words, p) for p in LEAVE_PHRASES)
    )


def request_refused(method: object, url: object, kind: object) -> str | None:
    """None when the request leaves, else why (a code for the log, never the address): a sign
    out address, any method (`sign_out`), a document POST that is no search nor consent choice
    (`document_post`), a POST whose address says a step of the line (`line_post`), an address
    that is no http, https, data or blob one (`scheme`)."""
    if not isinstance(url, str) or not isinstance(method, str):
        return "unreadable"
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("data", "blob", "about"):
        return None
    if scheme not in ("http", "https"):
        return "scheme"
    if signs_out(url):
        return "sign_out"
    verb = method.upper()
    if verb in ("GET", "HEAD", "OPTIONS"):
        return None
    if verb != "POST" and _starts(address_words(url) or [], SESSION_STEMS):
        return "sign_out"
    if post_refused(url):
        return "line_post"
    if kind == "document" and not search_or_consent(url):
        return "document_post"
    return None


def script_rules() -> dict[str, Any]:
    """Every word list and bound the control floor reads (detect's and the line's), for
    SCRIPT_GUARD_JS: a script's `click()` is judged by the same words as a click."""
    return {
        "submit": list(detect.SUBMIT_STEMS),
        "confirm": list(detect.CONFIRM_VERBS),
        "nouns": list(detect.APPLICATION_NOUNS),
        "danger": list(detect.DANGER_STEMS),
        "dangerWords": sorted(detect.DANGER_WORDS),
        "cancel": list(detect.CANCEL_VERBS),
        "contact": list(detect.CONTACT_VERBS),
        "recruiter": list(detect.RECRUITER_NOUNS),
        "buttonTags": sorted(detect.BUTTON_TAGS),
        "buttonRoles": sorted(detect.BUTTON_ROLES),
        "buttonInputs": sorted(detect.BUTTON_INPUTS),
        "submitTypes": sorted(detect.SUBMIT_TYPES),
        "card": detect.CARD.pattern,
        "namesMax": detect.NAMES_MAX,
        "nameMax": detect.NAME_MAX,
        "lineStems": list(LINE_STEMS),
        "lineWords": sorted(LINE_WORDS),
        "linePhrases": [list(p) for p in LINE_PHRASES],
        "linkStems": list(LINK_STEMS),
        "linkWords": sorted(LINK_WORDS),
        "linkPhrases": [list(p) for p in LINK_PHRASES],
        "writes": sorted(WRITES),
        "applicationStems": list(APPLICATION_STEMS),
        "formMax": 40,
    }


# The agent's own script (browser_evaluate), as `(script) => ...`. While it runs (and until the
# promise it returns settles), `HTMLElement.prototype.click` reads the element's facts with
# detect.FACTS_JS itself and judges them as `control(facts, "click")` does, in the same words
# (script_rules: detect's submit, danger and card rules, the line's words, phrases and link
# phrases, the search form test), ported below line for line: what a click refuses, a script's
# click() refuses. A submit control of a form that is sent otherwise than by GET, or that holds
# a field a person writes to someone or applies with (WRITES, an editable region, as
# `search_form` reads it), is refused too, and so are that form's `submit()` and
# `requestSubmit()` (fail closed: a consent form posted is clicked with browser_click). What a
# script does past the guard (an event it builds and dispatches, another realm's prototypes, a
# click it schedules with a timer, which runs after the guard is gone) meets the route's request
# floor, which is the last line. The script's result is returned as JSON text, cut by the caller.
SCRIPT_GUARD_JS = r"""async ([source, S]) => {
  const factsOf = /*facts*/;
  const fold = (text, camel) => {
    let t = String(text).normalize("NFKC").replace(/\p{Cf}/gu, "").normalize("NFKD")
      .replace(/\p{Mn}/gu, "");
    if (camel) t = t.replace(/([a-z])(?=[A-Z])/g, "$1 ");
    return t.toLowerCase().replace(/ß/g, "ss").split(/[^0-9a-z]+/).filter(Boolean);
  };
  const matchWords = text => {
    const split = fold(text, true), whole = fold(text, false);
    return split.join(" ") === whole.join(" ") ? split : split.concat(whole);
  };
  const glued = names => {
    const tokens = [], folded = [];
    for (let name of names.slice(0, S.namesMax)) {
      name = name.slice(0, S.nameMax);
      for (const text of [name, name.split(" ").join("").split("_").join("").split("-").join("")]) {
        const split = fold(text, true);
        tokens.push(...split);
        folded.push(...matchWords(text).slice(split.length));
      }
    }
    return tokens.concat(folded);
  };
  const starts = (tokens, stems) => tokens.some(t => stems.some(s => t.startsWith(s)));
  const card = new RegExp(S.card);
  const isCard = field => card.test(fold(String(field).slice(0, S.nameMax * 4), true).join(" "));
  const saysSubmit = names => {
    const t = glued(names);
    return starts(t, S.submit) || (starts(t, S.confirm) && starts(t, S.nouns));
  };
  const saysDanger = names => {
    const t = glued(names);
    return starts(t, S.danger) || t.some(w => S.dangerWords.includes(w)) ||
      (starts(t, S.contact) && starts(t, S.recruiter)) ||
      (starts(t, S.cancel) && starts(t, S.nouns));
  };
  const saysLine = names => {
    const t = names.flatMap(matchWords);
    return starts(t, S.lineStems) || t.some(w => S.lineWords.includes(w)) ||
      S.linePhrases.some(p => p.every(w => starts(t, [w])));
  };
  const saysLeave = names => names.some(name => {
    const t = matchWords(name);
    return starts(t, S.linkStems) || t.some(w => S.linkWords.includes(w)) ||
      S.linkPhrases.some(([a, b]) =>
        t.some((w, i) => w === a && t.slice(i + 1, i + 3).includes(b)));
  });
  const buttonLike = f => {
    const tag = f.tag || "", kind = f.type || "", role = f.role || "";
    if (tag === "input") return S.buttonInputs.includes(kind);
    if (["select", "textarea", "option", "label"].includes(tag)) return false;
    if (role && !S.buttonRoles.includes(role)) return false;
    return S.buttonTags.includes(tag) || ["button", "link", "menuitem", "tab"].includes(role);
  };
  const namesOf = f => f.names.filter(n => typeof n === "string");
  const own = f => {  // detect._own, action click
    if (!f || !Array.isArray(f.names)) return "unreadable";
    const names = namesOf(f), button = buttonLike(f);
    if (isCard(f.field || "")) return "card_field";
    if ((f.form_fields || []).filter(x => typeof x === "string").some(isCard)) return "card_field";
    if (button && saysDanger(names)) return "danger";
    if (f.selector_error) return "bad_selector";
    if (f.selector_hit || f.custom_submit) return "submit";
    if (["button", "input"].includes(f.tag) && S.submitTypes.includes(f.type)) return "submit";
    if (f.holds_submit) return "submit";
    if (button && saysSubmit(names)) return "submit";
    return null;
  };
  const method = f => f.discovery && typeof f.discovery === "object" ?
    String(f.discovery.method || "").toLowerCase() : "";
  const searchForm = f => {  // search_form
    const kinds = (f.form_kinds || []).filter(k => typeof k === "string");
    if (kinds.some(k => S.writes.includes(k) || k.endsWith(":editable"))) return false;
    return method(f) === "get" || kinds.length <= S.formMax;
  };
  const structural = f => {  // _structural, action click
    if (f.selector_hit || f.custom_submit) return false;
    const names = namesOf(f);
    if (buttonLike(f) && saysSubmit(names)) return false;
    return !(method(f) !== "get" && starts(glued(names), S.applicationStems));
  };
  const control = f => {  // control(facts, "click")
    let code = own(f);
    if (code === "submit" && structural(f) && searchForm(f)) code = null;
    if (code) return code;
    const names = namesOf(f), link = f.tag === "a" || f.role === "link";
    if (buttonLike(f) && !link && saysLine(names)) return "line";
    if (buttonLike(f) && saysLeave(names)) return "line";
    return null;
  };
  const kindOf = n =>
    String(n.type || (n.getAttribute && n.getAttribute("type")) || "").toLowerCase();
  // A form that is no search form for a script: sent otherwise than by GET, or holding a field
  // a person writes to someone or applies with, or more controls than FACTS_JS reads.
  const writing = f => {
    try {
      if (!f) return false;
      if (String(f.method || "get").toLowerCase() !== "get") return true;
      const fields = Array.from(f.elements || []);
      if (fields.length > 300) return true;
      const kinds = fields.map(n =>
        n.tagName.toLowerCase() + ":" + (n.isContentEditable ? "editable" : kindOf(n)));
      return kinds.some(k => S.writes.includes(k) || k.endsWith(":editable")) ||
        !!f.querySelector("[contenteditable]:not([contenteditable=false])");
    } catch (e) { return true; }
  };
  const submitter = el => {
    const tag = (el.tagName || "").toLowerCase(), type = String(el.type || "").toLowerCase();
    return (tag === "button" && (type === "submit" || (!el.getAttribute("type") && el.form))) ||
      (tag === "input" && (type === "submit" || type === "image"));
  };
  const sends = el => submitter(el) && (writing(el.form) || (el.hasAttribute("formmethod") &&
    String(el.formMethod || "").toLowerCase() !== "get"));
  const refused = el => {
    try { return control(factsOf(el, {sels: [], point: false})) !== null || sends(el); }
    catch (e) { return true; }
  };
  const keep = [HTMLElement.prototype.click, HTMLFormElement.prototype.submit,
    HTMLFormElement.prototype.requestSubmit];
  HTMLElement.prototype.click = function () {
    if (refused(this)) throw new Error("browserd: refused by the line");
    return keep[0].call(this);
  };
  HTMLFormElement.prototype.submit = function () {
    if (writing(this)) throw new Error("browserd: refused by the line");
    return keep[1].call(this);
  };
  HTMLFormElement.prototype.requestSubmit = function (s) {
    if (writing(this) || (s && refused(s))) throw new Error("browserd: refused by the line");
    return keep[2].call(this, s);
  };
  try {
    const value = await (0, eval)("(" + source + ")")();
    return JSON.stringify(value === undefined ? null : value);
  } finally {
    [HTMLElement.prototype.click, HTMLFormElement.prototype.submit,
      HTMLFormElement.prototype.requestSubmit] = keep;
  }
}""".replace("/*facts*/", detect.FACTS_JS.strip())
SCRIPT_RULES = script_rules()
