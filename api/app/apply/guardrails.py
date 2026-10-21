"""The refusals before anything is spent (SPEC-apply, "Guardrails", rules 3 and 4).

**Never apply** (`never_apply`, pure). The person's `never_apply` rules (`sweep.never_apply_rules`
on the search section) match the posting the way the sweep excludes a role: each rule's terms,
whole word with an optional plural, case-insensitive, on the company and the title. Three
additions, all toward refusing, since a role the person excluded must never be applied to by
the app: the text is folded first (accents, compatibility forms: "Sécurité" is "securite", a full
width letter is its plain one; the case fold comes before the marks are dropped, so every letter
reads as its capitals do, the iota subscript of polytonic Greek included), a term also matches
at the start or the end of a glued word, so the owner's rule "No cybersecurity roles: cyber,
security, pentest, threat" refuses "Cybersecurity Engineer" and "AppSecurity Lead", which the
sweep's whole word match lets through, and a character a reader does not see (a soft hyphen, a
zero width space, a control such as U+001F, a Hangul filler, the Braille blank) is read both as
nothing and as a gap, so it neither splits a term ("Secu<U+001F>rity") nor glues one to its
neighbours ("AI<U+200B>Security<U+200B>Engineer"). Spaces, hyphens, underscores, slashes and dots
are one separator ("Cyber-Security", "red_team"). Stated cost: a short term refuses more ("ai"
in a rule refuses "Aide"), and that is the side the rule errs on.
The sweep's own decision counts too: a posting whose history holds the sweep's `never_apply`
event (`swept`) is refused whatever the rules say today. In Full auto the first 1,500 characters
of the posting's text are read as well. A search section that no longer validates cannot be read,
so it refuses as a match would (`rules_unreadable`).

**Duplicates** (`duplicates`). Refused, in this order:
- `submitted` / `submit_unknown`: the posting has an application the app already clicked;
- `in_progress`: the posting has another live application (the partial unique index holds it too);
- `already_applied`: the posting's status is `applied`, `interview` or `rejected`;
- `posting_closed`: the posting's status is `closed`;
- `same_form`: another posting of the person has a live application, or an applied status, on the
  same canonical form address (`canonical_key`; a second partial unique index holds the first).
  A key stored on an application is read as these rules key its address today (`current_key`),
  so a key stored under older rules is the same form as its address's key now;
- `same_address`: as `same_form`, for an applied posting whose address is an ordinary one (a `url:`
  key, often one careers page for many roles), so Apply myself can show it as a warning, with
  `same_role` beside it when that holds too (`Refusal.also`);
- `same_role`: another posting of the person, at the same company and with the same normalised
  title (`company_key`, `title_key`), was submitted by the app (or is `submitting`, its click under
  way), or has an applied status with its `applied_on` in the last 180 days, counted from `now`:
  at exactly 180 days it is still refused. An applied status without `applied_on` counts too: its
  date is unknown, so nothing shows it is older (fail closed). Both keys read the text as the
  never apply rules do, then glue its letters and digits: a space, a hyphen, an unseen character
  or nothing between two words make the same key.

**The form address** (`canonical_key`). Ashby, Greenhouse and Lever job ids are global, so the key
is the ATS and its id, wherever the form is shown: `ashby:<uuid>` from jobs.ashbyhq.com/<org>/<id>
or `?ashby_jid=`; `greenhouse:<number>` from (job-)boards(.eu).greenhouse.io/<org>/jobs/<id> (the
segment `jobs` in any case), the boards API's boards-api.greenhouse.io/v1/boards/<org>/jobs/<id>,
the embed's `?token=` or a company page's `?gh_jid=`; `lever:<uuid>` from
jobs(.eu).lever.co/<org>/<id>. A path is read only on the ATS's own domain or a subdomain of it
("notashbyhq.com" is an ordinary address). The host is read as a browser reads it
(`browser_host`): percent escapes decoded, cut at every dot IDNA knows (the ideographic, full
width and halfwidth ones too), each label through IDNA to ASCII, no trailing dot and no leading
"www.", so a Greenhouse address written with full width dots is Greenhouse's.
Any other address is `url:` and the jobs module's normalised address of it with that host
(`slug.url_key`: http and https alike, no fragment, no trailing slash, no tracking parameter).
An address that is not http or https, or that no browser opens (a full width slash or colon in
its host, an unclosed bracket), has no form (`None`).

Declared open points (what these rules still miss, each toward applying unless said):
- a never apply term in another language than the rule's ("sécurité" for "security") or a
  synonym: the person writes those terms in the rule;
- a term inside a word that neither starts nor ends with it ("cyber" in "anticyberattack");
- a term split by something drawn as a space or a mark ("S e c u r i t y", "Secu-rity", a tab),
  and letters drawn like Latin ones in scripts outside the lookalike table (Armenian U+057D for
  "u", Cherokee U+13DA for "S"); a small letter is read as its capital is, so a reading of the
  small letter alone is lost: Greek small eta is "h" (not "n"), small mu "m" and small upsilon
  "y" (not "u"), small nu "n" (not "v"), the Cyrillic palochka "i" (not "l"), and gamma and the
  lunate sigma U+03F2 (which NFKC makes a sigma) are not read; the stated cost on the other side:
  a Cyrillic or Greek title is read with its lookalike letters as Latin ones, so a Latin term
  could match a word of it;
- the same role under two different titles ("ML Engineer" and "Machine Learning Engineer"), or a
  title that adds a city in parentheses: only accents, case, punctuation, spacing and gender
  markers ("H/F", "(m/w/d)") are dropped from a title, and only legal forms ("SAS", "Inc.") from a
  company. Toward refusing: a title in another script keeps only its lookalike letters, so two
  such titles that differ only in their other letters are one role;
- a form shown under an address the three ATS patterns do not cover (a company page that embeds
  Ashby without `ashby_jid`): it is compared as an address;
- a host is read through IDNA 2003 (Python's codec), not the UTS 46 browsers use: "ß" is "ss"
  there, so "straße.example" and "strasse.example" are one address (toward refusing), and a label
  IDNA 2003 refuses (one holding a bidi mark) is kept as written.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.models import ENDED, Application, Mode, State
from app.jobs import slug
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.jobs.sweep import NeverApplyRule
from app.profile.matching import escape

SAME_ROLE_DAYS = 180
FULL_AUTO_TEXT_CHARS = 1500
APPLIED_STATUSES = frozenset({JobStatus.applied, JobStatus.interview, JobStatus.rejected})
SUPPORTED_ATS = ("ashby", "greenhouse", "lever")

NEVER_APPLY = "Your rules exclude this role."
NEVER_APPLY_REFUSED = (
    "Your rules exclude this role, so the app will not apply for you. You can still apply yourself."
)
RULES_UNREADABLE = (
    "Your never apply rules could not be read, so the app will not apply for you. Open the Search "
    "tab of your profile and save it again."
)
SENTENCES = {
    "never_apply_role": NEVER_APPLY_REFUSED,
    "rules_unreadable": RULES_UNREADABLE,
    "no_address": "This posting has no address to apply on. Add its link first.",
    "submitted": "You already applied to this posting through the app.",
    "submit_unknown": (
        "We could not confirm an earlier application to this posting. Look for a confirmation "
        "mail or on the site."
    ),
    "in_progress": "An application to this posting is already under way.",
    "already_applied": "This posting is marked as applied.",
    "posting_closed": "This posting is closed.",
    "same_form": "This application form was already used for another of your postings.",
    "same_address": "Another of your postings with this address is marked as applied.",
    "same_role": "You applied to the same role at this company less than 180 days ago.",
}
# What Apply myself shows instead of refusing: the person decides, the app does not click.
WARNINGS = {
    "never_apply_role": NEVER_APPLY,
    "rules_unreadable": "Your never apply rules could not be read.",
    "same_address": SENTENCES["same_address"],
    "same_role": SENTENCES["same_role"],
}


@dataclass(frozen=True)
class Refusal:
    code: str
    application_id: uuid.UUID | None = None
    # The warnings found after this one (`same_role` after `same_address`): Apply myself shows all.
    also: tuple[str, ...] = ()

    @property
    def sentence(self) -> str:
        return SENTENCES[self.code]


# --- never apply ---------------------------------------------------------------------------------


# Stands, in a reading, for one character a reader does not see: it may be read as nothing or as
# a gap between two words, and the never apply match tries both.
UNSEEN = "\x00"
_U = r"\x00"
_JOINERS = re.compile(r"[ \-\u2010-\u2015_/.\x00]+")
_GAPS = frozenset({"Zs", "Zl", "Zp"})
_GAP_CONTROLS = frozenset("\t\n\r")
# Format characters (soft hyphen, zero width space and joiners, word joiner, BOM, bidi marks), the
# other controls (U+001C to U+001F, U+0085, \x0b and \x0c too), surrogates, private use, unassigned.
_UNSEEN_CATEGORIES = frozenset({"Cf", "Cc", "Cs", "Co", "Cn"})
# Drawn blank: the Hangul fillers (NFKC maps U+3164 and U+FFA0 to U+1160) and the Braille blank.
_BLANKS = frozenset("\u115f\u1160\u3164\uffa0\u2800")
# Keys are small letters as NFKC, the case fold and NFKD leave them: any other key is never reached.
_LOOKALIKES = str.maketrans(
    "авекмнорстхуѕіјһԁԛԝүӏαβεζηικμνορτυχϳᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘʀꜱᴛᴜᴠᴡʏᴢıȷɑɡɩ",
    "abekmhopctxysijhdqwyiabezhikmnoptyxjabcdefghijklmnoprstuvwyzijagi",
)


def _unseen(c: str) -> bool:
    return unicodedata.category(c) in _UNSEEN_CATEGORIES or c in _BLANKS


def _read(c: str) -> str:
    """One character of a case folded NFKD text as the fold keeps it: a gap is a space, a mark
    (an accent, a variation selector) is nothing, what a reader does not see is UNSEEN."""
    category = unicodedata.category(c)
    if category in _GAPS or c in _GAP_CONTROLS:
        return " "
    if _unseen(c):
        return UNSEEN
    if category in ("Mn", "Me") or unicodedata.combining(c):
        return ""
    return c


def _reading(text: str) -> str:
    """NFKC, the case fold, then NFKD, each character read (`_read`), lookalike letters read as
    Latin. The case fold comes before the marks are dropped, as the iota subscript of polytonic
    Greek folds to a letter: "\u1fb3" is read as its capitals "\u0391\u0399" are, "ai"."""
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    return "".join(map(_read, folded)).translate(_LOOKALIKES)


def _joined(run: re.Match[str]) -> str:
    return " " if run.group().strip(UNSEEN) else UNSEEN


def _marked(text: str) -> str:
    """`_reading` with every run of gaps, hyphens, underscores, slashes, dots and UNSEEN one
    space, or one UNSEEN when the run holds nothing else."""
    return _JOINERS.sub(_joined, _reading(text))


def fold(text: str) -> str:
    """The text as the never apply rules read it, what a reader does not see dropped: accents,
    case, compatibility forms, soft hyphens, zero width characters and controls, and every run of
    spaces, hyphens, underscores, slashes and dots one space. "Sécurité" is "securite", a full
    width letter is its plain one, "Secu\u00adrity" (a soft hyphen), "Secu\x1frity" (a control) and
    "Security" written with Cyrillic letters are "security", "Red-Team" and "red_team" are
    "red team". A capital and its small letter read alike (Greek capital and small eta are both
    "h"), so a rule matches a title whatever the case of either."""
    return _marked(text).replace(UNSEEN, "")


def spaced(text: str) -> str:
    """`text` with each character a reader does not see written as a space: the reading as a gap
    that `fold`, which drops them, leaves out ("Example<zero width space>Inc" is "Example Inc")."""
    return "".join(" " if _unseen(c) else c for c in text)


def _spelled(term: str) -> str:
    """A folded term as a pattern that also matches it with an unseen character between two of
    its letters, or in place of one of its spaces."""
    return f"{_U}?".join(f"[ {_U}]" if c == " " else escape(c) for c in term)


def _term_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """The sweep's whole word match of the folded terms, widened to a term that starts or ends
    a glued word, searched in a marked reading (`_marked`): an unseen character inside a term is
    read as nothing, one beside it as a gap (`\\w` does not match it). Linear: one alternation,
    searched once, with no nested repetition."""
    folded = [fold(term).strip() for term in terms]
    alternation = "|".join(_spelled(term) for term in folded if term)
    if not alternation:
        return None
    plural = rf"(?:(?:{_U}?e)?{_U}?s)?"  # the sweep's PLURAL, s or es
    starts = rf"(?<!\w)(?:{alternation})"  # "cyber" in "cybersecurity"
    ends = rf"(?:{alternation}){plural}(?!\w)"  # "security" in "cybersecurity"
    return re.compile(f"{starts}|{ends}")


def matching_rule(rules: Iterable[NeverApplyRule], *texts: str) -> NeverApplyRule | None:
    """The first rule whose terms match one of `texts`, or None."""
    marked = " \n ".join(_marked(text) for text in texts if text)
    for rule in rules:
        pattern = _term_pattern(rule.terms)
        if pattern is not None and pattern.search(marked) is not None:
            return rule
    return None


def never_apply(
    posting: Job,
    rules: Sequence[NeverApplyRule] | None,
    *,
    swept: bool,
    mode: Mode,
) -> str | None:
    """`never_apply_role` when the posting is excluded, `rules_unreadable` when the rules could
    not be read (`rules` None), else None. The caller decides what the code does per mode."""
    if swept:
        return "never_apply_role"
    if rules is None:
        return "rules_unreadable"
    texts = [posting.company, posting.title]
    if mode is Mode.full_auto:
        texts.append((posting.posting_text or "")[:FULL_AUTO_TEXT_CHARS])
    return "never_apply_role" if matching_rule(rules, *texts) is not None else None


def swept_never_apply(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> bool:
    """Whether the sweep moved this posting to skipped for a never apply rule."""
    found = db.execute(
        sa.select(JobEvent.id)
        .where(
            JobEvent.user_id == user_id,
            JobEvent.job_id == posting_id,
            JobEvent.kind == JobEventKind.status,
            JobEvent.data["via"].astext == "never_apply",
        )
        .limit(1)
    ).first()
    return found is not None


# --- the form address ----------------------------------------------------------------------------


def _uuid(text: str) -> str | None:
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def _number(text: str) -> str | None:
    return str(int(text)) if text.isascii() and text.isdigit() and len(text) <= 20 else None


# What IDNA reads as a dot between labels: the full stop, the ideographic, full width and halfwidth.
_DOTS = re.compile("[.\u3002\uff0e\uff61]")


def _label(label: str) -> str:
    """One label of a host through IDNA 2003 (NFKC, the case fold, and what it maps to nothing,
    such as a soft hyphen) to ASCII; a label IDNA refuses is kept, NFKC and case folded."""
    try:
        return label.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return unicodedata.normalize("NFKC", label).casefold()


def browser_host(hostname: str) -> str:
    """A host (lowercase, as `urlsplit` gives it) as a browser reads it: percent escapes decoded,
    cut at every dot IDNA knows, each label through IDNA, no trailing dot. Written with full
    width dots ("boards\uff0egreenhouse\uff0eio"), with "%2E" or with a trailing dot, the
    host is "boards.greenhouse.io"; an ASCII host without escapes or a trailing dot is as is."""
    return ".".join(_label(label) for label in _DOTS.split(unquote(hostname))).rstrip(".")


@dataclass(frozen=True)
class _Address:
    host: str  # as a browser reads it (`browser_host`), without any leading "www."
    segments: list[str]
    query: dict[str, str]
    url: str  # the address with that host, for `slug.url_key`; as written when the host was


def _host(url: str) -> _Address | None:
    try:
        parts = urlsplit(url.strip())
        hostname = parts.hostname
    except ValueError:
        return None  # a browser opens no page there: an unclosed IPv6 bracket, a full width slash
    if parts.scheme.lower() not in ("http", "https") or not hostname:
        return None
    host = browser_host(hostname)
    while host.startswith("www."):
        host = host.removeprefix("www.")
    address = url.strip()
    if host != hostname:
        try:
            port = parts.port
        except ValueError:
            port = None  # as `slug.url_key` reads it
        netloc = host if port is None else f"{host}:{port}"
        address = urlunsplit(parts._replace(netloc=netloc))
    segments = [segment for segment in parts.path.split("/") if segment]
    query = {name.lower(): value for name, value in parse_qsl(parts.query)}
    return _Address(host, segments, query, address)


def unstub(url: str, stub_base: str | None) -> str:
    """Tests only: an address under the fake forms server read as the ATS address its path names
    (/ashby/... is jobs.ashbyhq.com/..., /greenhouse/... boards.greenhouse.io/..., /lever/...
    jobs.lever.co/...). The setting is refused in production."""
    if not stub_base or not url.startswith(stub_base.rstrip("/") + "/"):
        return url
    rest = url[len(stub_base.rstrip("/")) + 1 :]
    first, _, tail = rest.partition("/")
    hosts = {"ashby": "jobs.ashbyhq.com", "greenhouse": "boards.greenhouse.io"}
    hosts["lever"] = "jobs.lever.co"
    return f"https://{hosts[first]}/{tail}" if first in hosts else url


def _on(host: str, domain: str) -> bool:
    """`host` is `domain` or one of its subdomains: "notashbyhq.com" is not Ashby's."""
    return host == domain or host.endswith("." + domain)


def _greenhouse_job(segments: list[str], query: dict[str, str]) -> str | None:
    """The job id of a Greenhouse address: a board's /<org>/jobs/<id>, the boards API's
    /v1/boards/<org>/jobs/<id>, the embed's /embed/job_app?token=<id>, segment names in any case."""
    lowered = [segment.lower() for segment in segments]
    if len(lowered) >= 3 and lowered[1] == "jobs":
        return _number(segments[2])
    if len(lowered) >= 5 and lowered[:2] == ["v1", "boards"] and lowered[3] == "jobs":
        return _number(segments[4])
    if lowered[-2:] == ["embed", "job_app"]:
        return _number(query.get("token", ""))
    return None


def _ats(found: _Address) -> str:
    if _on(found.host, "linkedin.com"):
        return "other"  # the host decides first: a LinkedIn address with ?gh_jid= is not Greenhouse
    for ats, domain in (("ashby", "ashbyhq.com"), ("greenhouse", "greenhouse.io")):
        if _on(found.host, domain):
            return ats
    if _on(found.host, "lever.co"):
        return "lever"
    if _uuid(found.query.get("ashby_jid", "")):
        return "ashby"
    if _number(found.query.get("gh_jid", "")):
        return "greenhouse"
    return "other"


def ats_of(url: str) -> str:
    """`ashby`, `greenhouse`, `lever` by the form's host, else `other`."""
    found = _host(url)
    return "other" if found is None else _ats(found)


def canonical_key(url: str | None) -> str | None:
    """The key of the form an address shows (module docstring), None when it shows none."""
    found = None if url is None else _host(url)
    if found is None:
        return None
    host, segments, query = found.host, found.segments, found.query
    ats = _ats(found)
    if ats == "ashby":
        job = _uuid(segments[1]) if _on(host, "ashbyhq.com") and len(segments) >= 2 else None
        job = job or _uuid(query.get("ashby_jid", ""))
        if job:
            return f"ashby:{job}"
    if ats == "greenhouse":
        job = _greenhouse_job(segments, query) if _on(host, "greenhouse.io") else None
        job = job or _number(query.get("gh_jid", ""))
        if job:
            return f"greenhouse:{job}"
    if ats == "lever" and len(segments) >= 2:
        job = _uuid(segments[1])
        if job:
            return f"lever:{job}"
    return f"url:{slug.url_key(found.url)}"


def current_key(key: str) -> str:
    """A stored form key as these rules key its address today: a `url:` key stored under older
    rules (full width dots, "%2E" or a trailing dot in its host, a boards API address) answers to
    the key its address has now, and a key `canonical_key` gives today is itself. `duplicates`
    reads every stored key through it."""
    address = key.removeprefix("url:")
    return key if address == key else canonical_key(address) or key


# --- the same role -------------------------------------------------------------------------------

_GENDER = re.compile(
    r"\(\s*(?:[hfmwdx]\s*/\s*){1,3}[hfmwdx]\s*\)"  # (H/F), (m/w/d)
    r"|(?<!\w)(?:[hfmwdx]\s*/\s*){1,3}[hfmwdx](?!\w)"  # H/F, f/m/d
    r"|\(\s*all genders\s*\)",
)
_LEGAL_FORMS = frozenset(
    {
        "inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "sas", "sasu", "sa", "sarl",
        "eurl", "gmbh", "ag", "bv", "nv", "corp", "corporation", "co", "company",
    }
)  # fmt: skip
_NOT_KEY = re.compile(r"[^a-z0-9]+")


def _key_text(text: str) -> str:
    """`text` read as `fold` reads it, but with what a reader does not see read as a space, and
    the slashes and parentheses of a gender marker still there."""
    return _reading(text).replace(UNSEEN, " ")


def title_key(title: str) -> str:
    """The title compared for the same role: read as the never apply rules read it (`fold`),
    gender markers dropped, then its letters a to z and digits glued, so a space, a hyphen, a
    character a reader does not see or nothing between two words make the same key: "Applied
    AI-Engineer (H/F)", "Applied AI Engi\u00adneer" and "applied ai engineer" are all
    "appliedaiengineer"."""
    return _NOT_KEY.sub("", _GENDER.sub(" ", _key_text(title)))


def company_key(company: str) -> str:
    """The company compared for the same role: read as `title_key` reads a title, its words of a
    to z and digits without trailing legal forms, glued: "Example Robotics, Inc." and
    "ExampleRobotics" are "examplerobotics"."""
    words = _NOT_KEY.sub(" ", _key_text(company)).split()
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return "".join(words)


# --- duplicates ----------------------------------------------------------------------------------


def _live(application: Application) -> bool:
    return State(application.state) not in ENDED


def duplicates(
    db: Session,
    user_id: uuid.UUID,
    posting: Job,
    key: str,
    now: datetime,
    *,
    exclude: uuid.UUID | None = None,
) -> Refusal | None:
    """The first duplicate refusal for starting an application on `posting` with the form key
    `key`, or None. The caller holds the posting's row lock and the person's apply lock. The gate
    (A6) passes the application it decides on as `exclude`: it is not its own duplicate."""
    own = db.execute(
        sa.select(Application).where(
            Application.user_id == user_id, Application.posting_id == posting.id
        )
    ).scalars()
    for application in own:
        if not _live(application) or application.id == exclude:
            continue
        state = State(application.state)
        code = state.value if state in (State.submitted, State.submit_unknown) else "in_progress"
        return Refusal(code, application.id)
    status = JobStatus(posting.status)
    if status in APPLIED_STATUSES:
        return Refusal("already_applied")
    if status is JobStatus.closed:
        return Refusal("posting_closed")

    key = current_key(key)
    stored = db.execute(
        sa.select(Application.id, Application.canonical_key).where(
            Application.user_id == user_id,
            Application.posting_id != posting.id,
            Application.state.not_in(ENDED),
            # A `url:` key may be stored under older rules: `current_key` reads it again.
            sa.or_(Application.canonical_key == key, Application.canonical_key.startswith("url:")),
        )
    ).all()
    other = next((found for found, stored_key in stored if current_key(stored_key) == key), None)
    if other is not None:
        return Refusal("same_form", other)

    since = now - timedelta(days=SAME_ROLE_DAYS)
    applied = db.execute(
        sa.select(Job.company, Job.title, Job.url, Job.applied_on).where(
            Job.user_id == user_id,
            Job.id != posting.id,
            Job.status.in_(APPLIED_STATUSES),
        )
    ).all()
    same_address = any(url is not None and canonical_key(url) == key for _, _, url, _ in applied)
    if same_address and not key.startswith("url:"):
        return Refusal("same_form")
    clicked = db.execute(
        sa.select(Job.company, Job.title)
        .join(Application, Application.posting_id == Job.id)
        .where(
            Application.user_id == user_id,
            Job.id != posting.id,
            # `submitting` has no `submitted_at` yet, but its click is under way (A6).
            sa.or_(Application.submitted_at >= since, Application.state == State.submitting),
        )
    ).all()
    recent = [(company, title) for company, title in clicked]
    recent += [
        (company, title)
        for company, title, _, applied_on in applied
        if applied_on is None or applied_on >= since.date()
    ]
    role = (company_key(posting.company), title_key(posting.title))
    same_role = any((company_key(c), title_key(t)) == role for c, t in recent)
    if same_address:
        return Refusal("same_address", also=("same_role",) if same_role else ())
    return Refusal("same_role") if same_role else None
