"""The automatic corrections (SPEC-documents.md, Decision 4: "Just change it ... Never block
anything"): pure functions that change content and say what they changed.

Who corrects what:

- **By code, alone**, here:
  - `fix_separators`: with the person's dash rule on, an em dash, an en dash or a spaced
    hyphen used as a separator becomes a comma (a range of digits keeps a hyphen, a dash that
    opens or closes a line goes); "full-stack" never changes (step 2);
  - `remove_remaining`: each claim still open after the writer's correction pass loses the
    sentence that holds it, unless the claim can go alone and leave a grammatical sentence
    that is no lie: a version number after a skills term of the profile ("Python 3.12",
    "Office 2019", "Airflow v2", recorded as `version_removed`), a count before a noun ("Led
    8 engineers"), a percent after "by" or "de" ("by 40%"), a seniority word ("Senior"), a
    date at an employer ("since 2021"); the true rest of the sentence stays; a bullet,
    highlight, competency, skills group or paragraph left empty is dropped; a field that must
    not be empty takes a fallback (`Fallbacks`: the base CV's headline or summary,
    "Application for" and the posting's title, the profile's motivation, else one sentence
    naming the role and the company) (step 5).
- **By a model rewrite**, not here: the correction pass of the writer (step 4) asks it once to
  rewrite or remove each flagged claim. The generation tasks (D7 and later) run it; this module
  gives them the interface: the issues to send (`checks.Checks.claims` with `checks.excerpt`)
  and `diff_corrections(before, after, issues)`, which turns what the writer changed into
  records with `by = "writer"`.

Every change is a `Correction`, stored on the version it produced:
`{field_path, before, after, code, by, reason}`, `after` empty for a removal, `reason` a
sentence of the fixed list `REASONS`. Normalising white space is the schema's work and is not
recorded.

Pure and deterministic: no I/O, no clock, no model. The same input gives the same content and
the same records in the same order.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.documents import content as c
from app.documents import rules
from app.documents.checks import Checks
from app.documents.profile_view import ProfileView
from app.documents.rules import Issue
from app.documents.settings import DocumentSettings
from app.documents.sources import PostingView

# --- the record ---------------------------------------------------------------------------------

NOT_IN_PROFILE = "Not in your profile"
NUMBER_NOT_STATED = "A number your profile does not state"
DASH = "A dash used as a separator"
BANNED = "A phrase you banned"
FILLED_IN = "Filled in because nothing was left"
REASONS: tuple[str, ...] = (NOT_IN_PROFILE, NUMBER_NOT_STATED, DASH, BANNED, FILLED_IN)

FALLBACK = "fallback"
VERSION_REMOVED = "version_removed"

# M9's codes of a number, a year, a duration, an amount, a percentage, a date or a time.
_NUMBER_CODES = frozenset(
    (
        "number_not_in_sources",
        "percent_not_in_sources",
        "duration_not_in_profile",
        "time_not_in_sources",
        "amount_not_in_sources",
        "year_not_in_sources",
        "date_not_in_sources",
        rules.DATE_AT_EMPLOYER,
    )
)

By = Literal["writer", "code"]


def reason_for(code: str) -> str:
    if code in _NUMBER_CODES or code in (VERSION_REMOVED, rules.CHANGE, rules.DECADE):
        return NUMBER_NOT_STATED
    if code == rules.BANNED_PHRASE:
        return BANNED
    if code == rules.DASH_SEPARATOR:
        return DASH
    if code == FALLBACK:
        return FILLED_IN
    return NOT_IN_PROFILE


@dataclass(frozen=True, slots=True)
class Correction:
    field_path: str
    before: str
    after: str  # empty for a removal
    code: str
    by: By
    reason: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _record(path: str, before: str, after: str, code: str, by: By) -> Correction:
    return Correction(path, before, after, code, by, reason_for(code))


# --- paths in the JSON of a content -------------------------------------------------------------

_LISTS = ("highlights", "competencies", "paragraphs")
_REQUIRED = {"cv": ("headline", "summary"), "base": ("headline", "summary"), "letter": ("subject",)}


def _parts(path: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in path.split(".")]


def _set(data: dict[str, Any], path: str, value: str | None) -> None:
    """Set a field; None marks a list entry to drop (`_prune`) and empties a single field (a
    skills label included: its group is then dropped)."""
    *head, last = _parts(path)
    node: Any = data
    for part in head:
        node = node[part]
    node[last] = "" if value is None and isinstance(last, str) else value


def _prune(data: dict[str, Any]) -> None:
    """Drop the entries marked None or left empty: a bullet, a highlight, a competency, a
    paragraph, a skills item; a skills group whose label went or whose items all went."""
    for name in _LISTS:
        if name in data:
            data[name] = [item for item in data[name] if item]
    for role in data.get("experience", []):
        role["bullets"] = [item for item in role["bullets"] if item]
    if "skills" in data:
        kept = []
        for group in data["skills"]:
            had_items = bool(group["items"])
            group["items"] = [item for item in group["items"] if item]
            if group["label"] and (group["items"] or not had_items):
                kept.append(group)
        data["skills"] = kept


def _rebuild(kind: c.Kind, data: dict[str, Any]) -> c.CvContent | c.LetterContent:
    _prune(data)
    return c.MODELS[kind].model_validate(data)  # type: ignore[return-value]


def field_max(path: str) -> int:
    """The hard limit of a printed field, from its path."""
    head = path.split(".", 1)[0]
    if head == "skills" or head == "competencies":
        return c.TERM_MAX
    return {
        "headline": c.HEADLINE_MAX,
        "summary": c.SUMMARY_MAX,
        "highlights": c.BULLET_MAX,
        "experience": c.BULLET_MAX,
        "paragraphs": c.PARAGRAPH_MAX,
    }.get(head, c.LINE_MAX)


def clip(text: str, limit: int) -> str:
    """At most `limit` characters, cut after the last whole sentence that fits, else at the last
    space, else hard."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = 0
    for _, end in rules.sentences(text):
        stop = end
        while stop > cut and text[stop - 1].isspace():
            stop -= 1
        if stop > limit:
            break
        cut = end
    if cut:
        return text[:cut].strip()
    space = text.rfind(" ", 0, limit + 1)
    return text[: space if space > 0 else limit].strip()


# --- step 2: the dash rule ----------------------------------------------------------------------


def fix_separators(
    kind: c.Kind, content: c.CvContent | c.LetterContent, settings: DocumentSettings
) -> tuple[c.CvContent | c.LetterContent, list[Correction]]:
    """With the dash rule on, every dash used as a separator replaced, one record per field
    changed (its text before and after). Off, the content is returned as it is."""
    if not settings.no_dash_separators:
        return content, []
    found = [
        (path, text, seps)
        for path, text in c.text_fields(content)
        if (seps := rules.separators(text))
    ]
    if not found:
        return content, []
    for compact in (False, True):
        data = c.dump(content)
        records = []
        fits = True
        for path, text, seps in found:
            fixed = rules.apply_separators(text, seps, compact=compact)
            fits = fits and len(fixed) <= field_max(path)
            _set(data, path, fixed or None)
            records.append(_record(path, text, fixed, rules.DASH_SEPARATOR, "code"))
        if not fits and not compact:
            continue  # a comma and a space for a bare dash made a field too long
        try:
            return _rebuild(kind, data), records
        except ValidationError:
            # The whole content grew past its limit: the compact replacements never grow.
            if compact:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


# --- step 4: what the writer's correction pass changed ------------------------------------------


def _align(before: list[Any], after: list[Any]) -> list[int | None]:
    """For each entry of `before`, the index of the entry of `after` it became, None when it
    went. Linear, two pointers: an equal entry is kept, otherwise it went while `before` has
    more entries left than `after`, else it was rewritten into the entry facing it."""
    out: list[int | None] = []
    j = 0
    for i, item in enumerate(before):
        if j < len(after) and item == after[j]:
            out.append(j)
            j += 1
        elif len(before) - i > len(after) - j:
            out.append(None)
        else:
            out.append(j)
            j += 1
    return out


def _after_text(path: str, before: dict[str, Any], after: dict[str, Any]) -> str:
    """The text that the field `path` of `before` became in `after` ("" when it went)."""
    parts = _parts(path)
    head = parts[0]
    if len(parts) == 1:
        value = after.get(str(head), "")
        return value if isinstance(value, str) else ""
    if head in _LISTS:
        index = _align(before[head], after.get(head, []))[int(parts[1])]
        return "" if index is None else after[head][index]
    if head == "experience":
        key = before["experience"][int(parts[1])]["employer_key"]
        role = next((r for r in after.get("experience", []) if r["employer_key"] == key), None)
        if role is None:
            return ""
        old = before["experience"][int(parts[1])]["bullets"]
        index = _align(old, role["bullets"])[int(parts[3])]
        return "" if index is None else role["bullets"][index]
    if head == "skills":
        group = _align(before["skills"], after.get("skills", []))[int(parts[1])]
        if group is None:
            return ""
        new = after["skills"][group]
        if parts[2] == "label":
            return new["label"]
        index = _align(before["skills"][int(parts[1])]["items"], new["items"])[int(parts[3])]
        return "" if index is None else new["items"][index]
    return ""


def diff_corrections(
    before: c.CvContent | c.LetterContent,
    after: c.CvContent | c.LetterContent,
    issues: tuple[Issue, ...] | list[Issue],
) -> list[Correction]:
    """One record per flagged field that the writer's correction pass changed, by "writer":
    the field's text before and what it became ("" when the writer removed it). A flagged field
    left as it was gets no record (it is still an issue, and code removes it at step 5)."""
    old = c.dump(before)
    new = c.dump(after)
    texts = dict(c.text_fields(before))
    out: list[Correction] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.level != "claim" or issue.field in seen or issue.field not in texts:
            continue
        seen.add(issue.field)
        was = texts[issue.field]
        now = _after_text(issue.field, old, new)
        if now != was:
            out.append(_record(issue.field, was, now, issue.code, "writer"))
    return out


# --- step 5: what remains is removed by code ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fallbacks:
    """What a field that must not be empty takes when nothing of it is left."""

    headline: str = ""
    summary: str = ""
    subject: str = ""
    paragraph: str = ""


_SUBJECT = {"en": "Application for {title}", "fr": "Candidature au poste de {title}"}
# The one sentence of a letter with no paragraph left and no motivation answer, by what the
# posting gives: (title, company), title only, company only, neither.
_ROLE = {
    "en": (
        "I am applying for the {title} role at {company}.",
        "I am applying for the {title} role.",
        "I am applying for this role at {company}.",
        "I am applying for this role.",
    ),
    "fr": (
        "Je vous adresse ma candidature au poste de {title} chez {company}.",
        "Je vous adresse ma candidature au poste de {title}.",
        "Je vous adresse ma candidature pour ce poste chez {company}.",
        "Je vous adresse ma candidature pour ce poste.",
    ),
}


def fallbacks_for(
    view: ProfileView,
    base: c.CvContent | None,
    posting: PostingView | None,
    language: Literal["en", "fr"] = "en",
) -> Fallbacks:
    """The fallbacks of the spec's step 5, from the person's own words and the posting."""
    headline = (base.headline if base else "") or view.identity.headline
    if not headline and view.employers:
        headline = view.employers[0].title
    title = posting.title.strip() if posting else ""
    company = posting.company.strip() if posting else ""
    choice = (not title) * 2 + (not company)
    sentence = _ROLE[language][choice].format(title=title, company=company)
    return Fallbacks(
        headline=clip(headline, c.HEADLINE_MAX),
        summary=clip(base.summary if base else "", c.SUMMARY_MAX),
        subject=clip(_SUBJECT[language].format(title=title), c.LINE_MAX) if title else "",
        paragraph=clip(view.motivation, c.PARAGRAPH_MAX) or clip(sentence, c.PARAGRAPH_MAX),
    )


_VERSION_CODES = frozenset(("number_not_in_sources", "year_not_in_sources"))


def _version_shaped(span: str) -> bool:
    """ "3.12", "16", "2019", "2.x": digits with at most two dots, an optional final "x"."""
    parts = span.split(".")
    if not 1 <= len(parts) <= 3 or len(parts[0]) > 4:
        return False
    if len(parts) > 1 and parts[-1] == "x":
        parts = parts[:-1]
    return all(part.isdigit() and len(part) <= 4 for part in parts)


def _version_start(text: str, issue: Issue, terms: tuple[str, ...]) -> int | None:
    """Where the version of `issue` starts with its leading space (and a "v"), when it follows a
    skills term of the sources; None when it is no version."""
    if issue.code not in _VERSION_CODES or not _version_shaped(text[issue.start : issue.end]):
        return None
    if issue.end < len(text) and (text[issue.end].isalnum() or text[issue.end] in "+%"):
        return None
    at = issue.start
    if at > 0 and text[at - 1] in "vV":
        at -= 1
    if at == 0 or text[at - 1] != " ":
        return None
    before = rules.fold(text[max(0, at - 1 - c.TERM_MAX) : at - 1]).text
    for term in terms:
        folded = rules.fold(term).text.strip()
        if folded and before.endswith(folded):
            head = before[: len(before) - len(folded)]
            if not head or not head[-1].isalnum():
                return at - 1
    return None


# What removing a token alone would break: a count after one of these words is part of a range
# or a comparison ("from 3 to 5", "by 2", "8 out of 10"), and before one of these it is a measure.
_NO_CUT_BEFORE = frozenset(
    [
        "from",
        "to",
        "by",
        "than",
        "between",
        "and",
        "or",
        "x",
        "par",
        "entre",
        "et",
        "ou",
        "que",
        "sur",
        "out",
        "per",
        "of",
        "de",
    ]
)
_NO_CUT_AFTER = frozenset(
    [
        "to",
        "and",
        "or",
        "out",
        "of",
        "per",
        "times",
        "fois",
        "x",
        "ms",
        "s",
        "h",
        "k",
        "m",
        "km",
        "kg",
        "gb",
        "mb",
        "tb",
        "million",
        "millions",
        "billion",
        "billions",
        "thousand",
        "mille",
        "milliers",
        "milliard",
        "milliards",
        "percent",
        "pourcent",
        "pour",
    ]
)


def _word_before(text: str, at: int) -> tuple[int, str]:
    """The word that ends right before `text[at]` (one space between), and where it starts."""
    if at < 2 or text[at - 1] != " ":
        return at, ""
    start = at - 1
    while start > 0 and start > at - 40 and text[start - 1].isalpha():
        start -= 1
    return start, text[start : at - 1].lower()


def _word_after(text: str, at: int) -> str:
    if at >= len(text) - 1 or text[at] != " ":
        return ""
    stop = at + 1
    while stop < len(text) and stop < at + 40 and text[stop].isalpha():
        stop += 1
    return text[at + 1 : stop]


def _count_cut(text: str, issue: Issue) -> tuple[int, int] | None:
    """A count before a noun loses itself alone: "Led 8 engineers" becomes "Led engineers"."""
    core = text[issue.start : issue.end].rstrip("+")
    if not (core.isdigit() and len(core) <= 6) and not (core.isalpha() and core.islower()):
        return None
    _, before = _word_before(text, issue.start)
    after = _word_after(text, issue.end)
    if not before or before in _NO_CUT_BEFORE:
        return None
    if not (before + after).isascii():
        return None  # French wants an article in its place ("Encadré des développeurs")
    if not after or not after[0].islower() or after.lower() in _NO_CUT_AFTER:
        return None
    return issue.start, issue.end + 1


def _percent_cut(text: str, issue: Issue) -> tuple[int, int] | None:
    """ "by 40%" (or "de 40 %", "of 40 percent") goes with its preposition: "Cut the costs by
    40%." becomes "Cut the costs."; any other percent takes its sentence."""
    found = rules.percents(text[issue.start : issue.start + 24])
    if not found or found[0][0] != 0:
        return None
    end = issue.start + found[0][1]
    start, before = _word_before(text, issue.start)
    if before not in ("by", "de", "of") or (end < len(text) and text[end].isalnum()):
        return None
    return (start - 1, end) if start > 0 and text[start - 1] == " " else None


def _date_cut(text: str, start: int, end: int) -> tuple[int, int]:
    """ "since 2021", "(2015 to 2021)" goes with its space or its brackets; at the start of a
    sentence, with the comma after it."""
    if start > 0 and text[start - 1] == "(" and end < len(text) and text[end] == ")":
        start, end = start - 1, end + 1
    if start > 0 and text[start - 1] == " ":
        return start - 1, end
    while end < len(text) and text[end] in ", ":
        end += 1
    return start, end


def _token_cut(text: str, issue: Issue) -> tuple[int, int] | None:
    """The span to cut when a claim goes alone and its sentence stays grammatical and no lie: a
    count, a percent after "by", a seniority word, a date at an employer. None: the sentence
    goes."""
    if issue.code == rules.SENIORITY:
        if issue.end < len(text) and text[issue.end] == " ":
            return issue.start, issue.end + 1
        return None
    if issue.code == rules.DATE_AT_EMPLOYER:
        return _date_cut(text, issue.start, issue.end)
    if issue.code == "percent_not_in_sources":
        return _percent_cut(text, issue)
    if issue.code == "number_not_in_sources":
        return _count_cut(text, issue)
    return None


def remove_remaining(
    content: c.CvContent | c.LetterContent, checks: Checks, fallbacks: Fallbacks
) -> tuple[c.CvContent | c.LetterContent, list[Correction]]:
    """Each claim of `checks` (the checks of this very content) loses its sentence, by code;
    the empty entries are dropped and the empty required fields take their fallback. Offsets
    are those of the issues, into the field's text, so combining marks stay where they were.

    A version number after a skills term of the sources loses only itself: an unstated "3.12"
    must go, but "Python", a true skill, must not go with it ("Python 3.12" becomes "Python")."""
    kind = checks.kind
    texts = dict(c.text_fields(content))
    by_field: dict[str, list[Issue]] = {}
    for issue in checks.claims:
        if issue.field in texts:
            by_field.setdefault(issue.field, []).append(issue)
    data = c.dump(content)
    records: list[Correction] = []
    for path, issues in by_field.items():
        text = texts[path]
        spans = rules.sentences(text)
        starts = [start for start, _ in spans]
        removed: dict[int, str] = {}
        cuts: list[tuple[int, int, str]] = []
        for issue in issues:
            cut = _version_start(text, issue, checks.known_terms)
            if cut is not None:
                cuts.append((cut, issue.end, VERSION_REMOVED))
                continue
            token = _token_cut(text, issue)
            if token is not None:
                cuts.append((*token, issue.code))
                continue
            index = max(bisect_right(starts, issue.start) - 1, 0)
            removed.setdefault(index, issue.code)
        kept_spans = [(s, e) for i, (s, e) in enumerate(spans) if i not in removed]
        records += [
            _record(path, text[spans[i][0] : spans[i][1]].strip(), "", code, "code")
            for i, code in sorted(removed.items())
        ]
        kept = "".join(text[s:e] for s, e in kept_spans).strip()
        # The token cuts, one record per code, in the order they first stand in the field.
        applied: list[tuple[int, int]] = []
        for code in dict.fromkeys(code for *_, code in sorted(cuts)):
            applied += [(a, b) for a, b, k in cuts if k == code]
            after = "".join(_cut(text, s, e, applied) for s, e in kept_spans).strip()
            if after != kept:
                records.append(_record(path, kept, after, code, "code"))
            kept = after
        _set(data, path, kept or None)
        if path.startswith("skills.") and path.endswith(".label") and not kept:
            group = data["skills"][int(path.split(".")[1])]
            items = [item for item in group["items"] if item]
            if items:
                records.append(
                    _record(path.rsplit(".", 1)[0], ", ".join(items), "", issues[0].code, "code")
                )
    for name in _REQUIRED[kind]:
        if not data[name]:
            data[name] = ""
            value = getattr(fallbacks, name)
            if value:
                data[name] = clip(value, field_max(name))
                records.append(_record(name, "", data[name], FALLBACK, "code"))
    if kind == "letter":
        data["paragraphs"] = [item for item in data["paragraphs"] if item]
        if not data["paragraphs"] and fallbacks.paragraph:
            paragraph = clip(fallbacks.paragraph, c.PARAGRAPH_MAX)
            data["paragraphs"] = [paragraph]
            records.append(_record("paragraphs.0", "", paragraph, FALLBACK, "code"))
    return _rebuild(kind, data), records


def _cut(text: str, start: int, end: int, cuts: list[tuple[int, int]]) -> str:
    """`text[start:end]` without the spans of `cuts` inside it. A cut that opens the sentence
    gives its capital to the word after it ("Senior engineer" becomes "Engineer")."""
    out: list[str] = []
    at = start
    for low, high in sorted(cuts):
        if low < start or high > end or low < at:
            continue
        out.append(text[at:low])
        at = high
        if low == start and at < end and text[at].islower():
            out.append(text[at].upper())
            at += 1
    out.append(text[at:end])
    return "".join(out)


def records_json(records: list[Correction]) -> list[dict[str, str]]:
    """The records as the `corrections` column of a version stores them."""
    return [record.as_dict() for record in records]
