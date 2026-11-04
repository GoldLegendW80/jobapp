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
  message, connect, invite, comment, post, sign out, account, in English, French and German). A
  submit the detector sees only by structure (a `type=submit` button, Enter in a form's field) is
  let through in a search or consent form: a GET form, or a form holding no field a person writes
  to someone or applies with (no multi line text, no file, no e-mail, telephone or password box,
  no editable region) and whose control's names say no submit word. Save job, Follow, Accept
  cookies, Search and Next page pass (the tables of api/tests/browser/test_b20_line.py).
- **Requests** (`request_refused`): every request of a sweep's context goes through browserd's
  route. A document (a navigation, a form's submission) is refused when it is a POST, unless its
  address says search; a fetch, an XHR, a beacon or any other POST is refused when its address
  says apply, submit, message, invite, connect, comment, payment, sign out or account change
  (POST_STEMS). A GET or a HEAD always leaves (any public host: browserd's egress proxy and the
  host firewall keep private, loopback, tailnet and link local addresses out).

What code cannot see is declared: a script that builds its own request by a means the route
does not see (a WebSocket, a service worker's own fetch, which only the site's code can start)
leaves; a POST body is never read, only its address; a control with no word of the line in any
name, outside a form, whose script sends a message (an icon only button) is clicked. The
agent's own scripts (`browser_evaluate`) run inside SCRIPT_GUARD_JS, which refuses
`element.click()`, `form.submit()` and `form.requestSubmit()` on what these rules refuse, and
whose requests meet the same route: that, not the script, is the last line.
"""

import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

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
# "Replenishment", "Publié le"). The words of the line are read on buttons only, never on a
# link: a posting link's name is its title ("Post-doc", "SharePoint Engineer"), and a link only
# opens a page (a GET); what sends a message is a POST, which the request floor refuses.
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


def search_form(facts: dict[str, Any]) -> bool:
    """A form whose submission is a search or a consent choice: GET, or one holding no field a
    person writes to someone, applies or signs in with (WRITES, an editable region)."""
    native = facts.get("native")
    method = str(native.get("method") or "").lower() if isinstance(native, dict) else ""
    kinds = [k for k in facts.get("form_kinds") or [] if isinstance(k, str)]
    if any(k in WRITES or k.endswith(":editable") for k in kinds):
        return False
    if method == "get":
        return True
    return len(kinds) <= 40  # a consent form, a search form posted (an old career site)


def _structural(facts: dict[str, Any], action: str) -> bool:
    """The detector's `submit` came from the element's structure alone (its type, the submit it
    holds, Enter in a form's field), never from a lease selector or a submit word."""
    if facts.get("selector_hit") or facts.get("custom_submit"):
        return False
    names = [n for n in facts.get("names") or [] if isinstance(n, str)]
    if detect.button_like(facts) and detect.says_submit(names):
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
    return None


# What a POST address may not say (folded, cut into words: `/voyager/api/messaging/...`,
# `?action=apply`, `sendInvitation`, `/checkout`, `/logout`, `/account/delete`).
POST_STEMS = (
    "apply",
    "applica",
    "candidat",
    "postul",
    "bewerb",
    "submit",
    "messag",
    "conversation",
    "invit",
    "connect",
    "relationship",
    "comment",
    "reply",
    "share",
    "publish",
    "pay",
    "checkout",
    "purchas",
    "billing",
    "subscri",
    "logout",
    "signout",
    "account",
    "profile",
    "password",
    "setting",
    "delete",
    "remove",
    "sendmail",
    "mail",
)
POST_PHRASES = (("sign", "out"), ("log", "out"))
SEARCH_ADDRESS = ("search", "recherche", "suche", "query", "queries", "jobs")
URL_MAX = 4096


def _address_words(url: str) -> list[str]:
    try:
        parts = urlsplit(url[:URL_MAX])
    except ValueError:
        return []
    text = unquote(f"{parts.path} {parts.query}")
    return detect.match_words(re.sub(r"[^A-Za-z0-9]+", " ", text))


def post_refused(url: str) -> bool:
    words = _address_words(url)
    if _starts(words, POST_STEMS):
        return True
    return any(all(_starts(words, (w,)) for w in phrase) for phrase in POST_PHRASES)


def request_refused(method: object, url: object, kind: object) -> str | None:
    """None when the request leaves, else why (a code for the log, never the address): a
    document POST that is no search (`document_post`), a POST whose address says a step of the
    line (`line_post`), an address that is no http, https, data or blob one (`scheme`)."""
    if not isinstance(url, str) or not isinstance(method, str):
        return "unreadable"
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("data", "blob", "about"):
        return None
    if scheme not in ("http", "https"):
        return "scheme"
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return None
    if post_refused(url):
        return "line_post"
    if kind == "document":
        words = _address_words(url)
        return None if _starts(words, SEARCH_ADDRESS) else "document_post"
    return None


def _js_words() -> str:
    stems = sorted({*LINE_STEMS, *detect.SUBMIT_STEMS, *detect.DANGER_STEMS})
    return json.dumps({"stems": stems, "words": sorted(LINE_WORDS | detect.DANGER_WORDS)})


# The agent's own script (browser_evaluate), as `(script) => ...`: while it runs (and until the
# promise it returns settles), `HTMLElement.prototype.click`, `HTMLFormElement.prototype.submit`
# and `requestSubmit` refuse a submit control outside a GET form, and any control whose visible
# names (text, aria-label, title, value) hold a word of the line, as `control` reads them (the
# stems and words above, accents and case folded). An event the script builds itself
# (`dispatchEvent(new MouseEvent("click"))`) is not seen: the route's request floor is what holds
# then. The script's result is returned as JSON text, cut by the caller.
SCRIPT_GUARD_JS = """async ([source, rules]) => {
  const fold = s => String(s || "").normalize("NFKD").replace(/[\\u0300-\\u036f]/g, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
  const said = el => {
    const names = [el.innerText, el.textContent, el.value, el.title,
      el.getAttribute && el.getAttribute("aria-label")].map(fold).join(" ");
    const words = names.split(/[^a-z0-9]+/).filter(Boolean);
    return words.some(w => rules.words.includes(w) || rules.stems.some(s => w.startsWith(s)));
  };
  const submitter = el => {
    const tag = (el.tagName || "").toLowerCase(), type = String(el.type || "").toLowerCase();
    return (tag === "button" && (type === "submit" || (!el.getAttribute("type") && el.form))) ||
      (tag === "input" && (type === "submit" || type === "image"));
  };
  const posted = f => !!f && String(f.method || "get").toLowerCase() !== "get";
  const refused = el => said(el) || (submitter(el) && posted(el.form));
  const keep = [HTMLElement.prototype.click, HTMLFormElement.prototype.submit,
    HTMLFormElement.prototype.requestSubmit];
  HTMLElement.prototype.click = function () {
    if (refused(this)) throw new Error("browserd: refused by the line");
    return keep[0].call(this);
  };
  HTMLFormElement.prototype.submit = function () {
    if (posted(this)) throw new Error("browserd: refused by the line");
    return keep[1].call(this);
  };
  HTMLFormElement.prototype.requestSubmit = function (s) {
    if (posted(this) || (s && said(s))) throw new Error("browserd: refused by the line");
    return keep[2].call(this, s);
  };
  try {
    const value = await (0, eval)("(" + source + ")")();
    return JSON.stringify(value === undefined ? null : value);
  } finally {
    [HTMLElement.prototype.click, HTMLFormElement.prototype.submit,
      HTMLFormElement.prototype.requestSubmit] = keep;
  }
}"""
SCRIPT_RULES = json.loads(_js_words())
