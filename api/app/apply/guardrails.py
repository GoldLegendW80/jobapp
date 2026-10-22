"""The refusals before anything is spent (SPEC-apply, "Guardrails", rules 3 and 4).

**Never apply** (`never_apply`, pure). The person's `never_apply` rules (`sweep.never_apply_rules`
on the search section) use the sweep's shared matcher (`jobs.never_apply`) on the company and
the title. The text is folded first (accents, case, compatibility forms, hidden characters and
lookalike letters), and terms match whole words, optional plurals and the start or end of a
glued word. Both sweep and apply refuse "Cybersecurity Engineer" and "AppSecurity Lead" under
the owner's rule "No cybersecurity roles: cyber, security, pentest, threat";
spaces, hyphens, underscores, slashes and dots are one separator ("Cyber-Security", "red_team").
Stated cost: a short term refuses more ("ai" in a rule refuses "Aide"), and that is the side the
rule errs on.
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
  same canonical form address (`canonical_key`; a second partial unique index holds the first);
- `same_address`: as `same_form`, for an applied posting whose address is an ordinary one (a `url:`
  key, often one careers page for many roles), so Apply myself can show it as a warning;
- `same_role`: another posting of the person, at the same company and with the same normalised
  title (`company_key`, `title_key`), was submitted by the app (or is `submitting`, its click under
  way), or has an applied status with its `applied_on` in the last 180 days, counted from `now`:
  at exactly 180 days it is still refused. An applied status without `applied_on` counts too: its
  date is unknown, so nothing shows it is older (fail closed);
- `passed`: the posting's status is `skipped`. Apply myself only warns, after the other
  duplicate protections have passed; automated starts, decisions and rechecks refuse it.

**The form address** (`canonical_key`). Ashby, Greenhouse and Lever job ids are global, so the key
is the ATS and its id, wherever the form is shown: `ashby:<uuid>` from jobs.ashbyhq.com/<org>/<id>
or `?ashby_jid=`; `greenhouse:<number>` from (job-)boards(.eu).greenhouse.io/<org>/jobs/<id> (the
segment `jobs` in any case), the boards API's boards-api.greenhouse.io/v1/boards/<org>/jobs/<id>,
the embed's `?token=` or a company page's `?gh_jid=`; `lever:<uuid>` from
jobs(.eu).lever.co/<org>/<id>. A path is read only on the ATS's own domain or a subdomain of it
("notashbyhq.com" is an ordinary address).
Any other address is `url:` and the jobs module's normalised address (`slug.url_key`: http and
https alike, host lowercase without www, no fragment, no trailing slash, no tracking parameter).
An address that is not http or https has no form (`None`).

Declared open points (what these rules still miss, each toward applying):
- a never apply term in another language than the rule's ("sécurité" for "security") or a
  synonym: the person writes those terms in the rule;
- a term inside a word that neither starts nor ends with it ("cyber" in "anticyberattack");
- a term split by something drawn as a space or a mark ("S e c u r i t y", "Secu-rity", the
  Braille blank U+2800), and letters drawn like Latin ones in scripts outside the lookalike table
  (Armenian U+057D for "u", Cherokee U+13DA for "S"); a small letter is read as its capital is,
  so a reading of the small letter alone is lost: Greek small eta is "h" (not "n"), small mu "m"
  and small upsilon "y" (not "u"), small nu "n" (not "v"), the Cyrillic palochka "i" (not "l"),
  and gamma and the lunate sigma U+03F2 (which NFKC makes a sigma) are not read; the stated cost
  on the other side: a Cyrillic or Greek title is read with its lookalike letters as Latin ones, so
  a Latin term could match a word of it;
- the same role under two different titles ("ML Engineer" and "Machine Learning Engineer"), or a
  title that adds a city in parentheses: only accents, case, punctuation and gender markers
  ("H/F", "(m/w/d)") are dropped from a title, and only legal forms ("SAS", "Inc.") from a company;
- a form shown under an address the three ATS patterns do not cover (a company page that embeds
  Ashby without `ashby_jid`): it is compared as an address. A live application started on a boards
  API address before these were keyed keeps its `url:` key.
"""

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply.models import ENDED, Application, Mode, State
from app.jobs import slug
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.jobs.never_apply import NeverApplyRule, fold, matching_rule

SAME_ROLE_DAYS = 180
FULL_AUTO_TEXT_CHARS = 1500
APPLIED_STATUSES = frozenset({JobStatus.applied, JobStatus.interview, JobStatus.rejected})
SUPPORTED_ATS = ("ashby", "greenhouse", "lever", "linkedin")

NEVER_APPLY = "Your rules exclude this role."
NEVER_APPLY_REFUSED = (
    "Your rules exclude this role, so the app will not apply for you. You can still apply yourself."
)
RULES_UNREADABLE = (
    "Your never apply rules could not be read, so the app will not apply for you. Open the Search "
    "tab of your profile and save it again."
)
SENTENCES = {
    "email_manual": (
        "This posting accepts email applications. Send it from your mail app, "
        "or choose Review first to prepare and review the email here."
    ),
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
    "passed": "You passed this posting. Undo the Pass first if you want to apply.",
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
    "passed": SENTENCES["passed"],
}


@dataclass(frozen=True)
class Refusal:
    code: str
    application_id: uuid.UUID | None = None

    @property
    def sentence(self) -> str:
        return SENTENCES[self.code]


# --- never apply ---------------------------------------------------------------------------------


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


def _host(url: str) -> tuple[str, list[str], dict[str, str]] | None:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".").removeprefix("www.")
    segments = [segment for segment in parts.path.split("/") if segment]
    query = {name.lower(): value for name, value in parse_qsl(parts.query)}
    return host, segments, query


def unstub(url: str, stub_base: str | None) -> str:
    """Tests only: an address under the fake forms server read as the ATS address its path names
    (/ashby/... is jobs.ashbyhq.com/..., /greenhouse/... boards.greenhouse.io/..., /lever/...
    jobs.lever.co/...). The setting is refused in production."""
    if not stub_base or not url.startswith(stub_base.rstrip("/") + "/"):
        return url
    from app.apply.ats.wizard import fixture_job, fixture_url

    for name, original in (
        ("workday", "https://example.wd1.myworkdayjobs.com/en-US/Jobs/job/Paris/Engineer_R"),
        ("google_careers", "https://www.google.com/about/careers/applications/jobs/results/"),
        ("vie", "https://mon-vie-via.businessfrance.fr/en/offres/"),
    ):
        if fixture_url(url, stub_base, name) and (job := fixture_job(url)):
            return original + job + ("-engineer" if name == "google_careers" else "")
    rest = url[len(stub_base.rstrip("/")) + 1 :]
    first, _, tail = rest.partition("/")
    hosts = {"ashby": "jobs.ashbyhq.com", "greenhouse": "boards.greenhouse.io"}
    hosts["lever"] = "jobs.lever.co"
    hosts["linkedin"] = "www.linkedin.com"
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


def review_adapter(url: str):
    """Only known canonical job addresses, independent of the page's claims."""
    from app.apply.ats.google_careers import GoogleCareers
    from app.apply.ats.vie import Vie
    from app.apply.ats.workday import Workday

    return next(
        (a for kind in (Workday, GoogleCareers, Vie) if (a := kind()).form_url(url) is not None),
        None,
    )


def ats_of(url: str) -> str:
    """`ashby`, `greenhouse`, `lever` by the form's host, else `other`."""
    if known := review_adapter(url):
        return known.name
    found = _host(url)
    if found is None:
        return "other"
    host, _segments, query = found
    if _on(host, "linkedin.com"):
        from app.apply.ats.linkedin import LinkedIn

        return "linkedin" if LinkedIn().form_url(url) is not None else "other"
    for ats, domain in (("ashby", "ashbyhq.com"), ("greenhouse", "greenhouse.io")):
        if _on(host, domain):
            return ats
    if _on(host, "lever.co"):
        return "lever"
    if _uuid(query.get("ashby_jid", "")):
        return "ashby"
    if _number(query.get("gh_jid", "")):
        return "greenhouse"
    return "other"


def canonical_key(url: str | None) -> str | None:
    """The key of the form an address shows (module docstring), None when it shows none."""
    if url is None:
        return None
    if known := review_adapter(url):
        return f"{known.name}:{known.job_of(url)}"
    found = _host(url)
    if found is None:
        return None
    host, segments, query = found
    ats = ats_of(url)
    if ats == "linkedin":
        return f"linkedin:{segments[2]}"
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
    return f"url:{slug.url_key(url)}"


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


def title_key(title: str) -> str:
    """The title compared for the same role: folded, gender markers dropped, words of a-z and
    0-9 separated by one space."""
    return slug.text_key(_GENDER.sub(" ", slug.fold(unicodedata.normalize("NFKC", title))))


def company_key(company: str) -> str:
    """The company compared for the same role: its words without trailing legal forms."""
    words = slug.text_key(fold(company)).split()
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return " ".join(words)


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

    other = db.execute(
        sa.select(Application.id)
        .where(
            Application.user_id == user_id,
            Application.posting_id != posting.id,
            Application.canonical_key == key,
            Application.state.not_in(ENDED),
        )
        .limit(1)
    ).scalar_one_or_none()
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
    for _, _, url, _ in applied:
        if url is not None and canonical_key(url) == key:
            return Refusal("same_address" if key.startswith("url:") else "same_form")
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
    if any((company_key(c), title_key(t)) == role for c, t in recent):
        return Refusal("same_role")
    if status is JobStatus.skipped:
        return Refusal("passed")
    return None
