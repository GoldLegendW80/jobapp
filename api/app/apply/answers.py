# ruff: noqa: E501, the pattern table below reads best one rule to a line
"""Answers to the standard questions of an application form, from the profile (SPEC-apply,
"Answers to standard questions"). No model, no network, no clock, nothing invented.

`resolve(field, context)` gives an `Answer` (a value and the source it came from) or a `Left`
(why nothing was answered, and the options of the field so the person can choose). Sources, in
this order: the person's answer bank, the identity section, the documents, the `answers` section,
the experience, education and skills sections. A required question no source holds is left.

The patterns are English and French. A rule that matches but finds no value in the profile leaves
the field; it never falls through to a guess. Option matching: exact, prefix, contains, and the
families "Other" and "Prefer not to say". A context without a posting (the profile run of A18a)
answers from the profile alone and leaves everything that needs the posting's country.
"""

import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from app.apply import terms
from app.apply.bank import AnswerBank
from app.jobs.gazetteer import places_of
from app.profile.schemas import (
    AnswersSection,
    ExperienceSection,
    IdentitySection,
    SkillsSection,
    WorkAuthorization,
)

FieldKind = Literal[
    "text",
    "textarea",
    "yes_no",
    "single",
    "multi",
    "select",
    "combobox",
    "date",
    "file",
    "checkbox",
]
_CHOICES = ("single", "select", "combobox", "multi", "yes_no")

# The country of each city of the jobs gazetteer, which names cities without their country.
CITY_COUNTRY: dict[str, str] = {
    **dict.fromkeys(
        (
            "Paris",
            "Bordeaux",
            "Lyon",
            "Toulouse",
            "Lille",
            "Strasbourg",
            "Nice",
            "Marseille",
            "Sete",
            "Nantes",
            "Rennes",
            "Niort",
            "Dijon",
            "Grenoble",
            "Brest",
            "Martinique",
        ),
        "France",
    ),
    **dict.fromkeys(("London", "Glasgow", "Brighton", "Abingdon"), "United Kingdom"),
    **dict.fromkeys(("Geneva", "Zurich", "Lausanne"), "Switzerland"),
    **dict.fromkeys(("Berlin", "Munich", "Hanover", "Stuttgart"), "Germany"),
    **dict.fromkeys(("Barcelona", "Madrid"), "Spain"),
    **dict.fromkeys(
        (
            "San Francisco Bay Area",
            "Los Angeles",
            "San Diego",
            "Seattle",
            "Denver",
            "Phoenix",
            "Dallas",
            "Minneapolis",
            "Green Bay",
            "Chicago",
            "Bloomington",
            "Cincinnati",
            "Atlanta",
            "Washington DC",
            "New York",
            "Boston",
        ),
        "United States",
    ),
    **dict.fromkeys(("Montreal", "Toronto", "Ottawa"), "Canada"),
    "Luxembourg": "Luxembourg",
    "Brussels": "Belgium",
    "Amsterdam": "Netherlands",
    "Dublin": "Ireland",
    "Milan": "Italy",
    "Warsaw": "Poland",
    "Stockholm": "Sweden",
    "Esbjerg": "Denmark",
    "Tallinn": "Estonia",
    "Bucharest": "Romania",
    "Sofia": "Bulgaria",
    "Singapore": "Singapore",
    "Bengaluru": "India",
    "Seoul": "South Korea",
    "Sydney": "Australia",
    "Melbourne": "Australia",
}


def posting_countries(location: str | None) -> tuple[str, ...]:
    """The countries a posting's location text names, through the jobs gazetteer: the country
    of every city it names, else every country it names, in the gazetteer's order."""
    found: list[str] = []
    for place in places_of(location):
        country = place.name if place.kind == "country" else CITY_COUNTRY.get(place.name)
        if country and country not in found:
            found.append(country)
    return tuple(found)


@dataclass(frozen=True)
class FormField:
    label: str
    kind: FieldKind = "text"
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class Answer:
    value: str | bool | tuple[str, ...]
    source: str  # where it came from, e.g. "identity.email" or "bank"
    option: str | None = None  # the option chosen, when the field lists options


@dataclass(frozen=True)
class Left:
    reason: str
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class Posting:
    countries: tuple[str, ...] = ()
    title: str | None = None

    @classmethod
    def at(cls, location: str | None, title: str | None = None) -> "Posting":
        return cls(posting_countries(location), title)


@dataclass(frozen=True)
class Documents:
    """The document versions of the application: file paths for the upload fields and the
    letter's text for a cover letter box."""

    cv_path: str | None = None
    letter_path: str | None = None
    letter_text: str | None = None


@dataclass(frozen=True)
class Context:
    user_id: uuid.UUID
    sections: Mapping[str, Any]  # the profile's stored sections
    posting: Posting | None = None
    documents: Documents | None = None
    bank: AnswerBank | None = None
    applied_before: bool | None = None  # from the app's own applications, when known

    def __post_init__(self) -> None:
        if self.bank is not None and self.bank.user_id != self.user_id:
            raise ValueError("the answer bank belongs to another person")


# --- reading ---------------------------------------------------------------------------------


def _model[T](cls: type[T], data: Any) -> T:
    """A section as its model; a stored section that no longer validates reads as empty."""
    try:
        return cls.model_validate(data or {})  # type: ignore[attr-defined, no-any-return]
    except ValidationError:
        return cls.model_validate({})  # type: ignore[attr-defined, no-any-return]


def fold(text: str | None) -> str:
    plain = unicodedata.normalize("NFD", (text or "").lower().replace("\u2019", "'"))
    return re.sub(r"\s+", " ", re.sub("[̀-ͯ]", "", plain)).strip()


YES = ("yes", "oui", "y", "true", "i do", "i am", "j'accepte")
NO = ("no", "non", "n", "false", "i do not", "i don't", "i am not")
_OTHER = ("other", "others", "autre", "autres", "something else", "not listed", "none of the above")
_DECLINE = (
    "prefer not to say",
    "prefer not to answer",
    "prefer not to disclose",
    "prefer not to state",
    "decline to self identify",
    "decline to answer",
    "decline to state",
    "decline to identify",
    "i don't wish to answer",
    "i do not wish to answer",
    "i do not wish to disclose",
    "i don't wish to disclose",
    "choose not to disclose",
    "choose not to answer",
    "do not wish to answer",
    "rather not say",
    "not to say",
    "no answer",
    "je prefere ne pas repondre",
    "prefere ne pas repondre",
    "ne souhaite pas repondre",
    "ne souhaite pas le preciser",
    "je ne souhaite pas",
)


def _whole_words(inner: str, outer: str) -> bool:
    """`inner` inside `outer` as whole words ("man" is not in "woman")."""
    return len(inner) >= 3 and re.search(rf"\b{re.escape(inner)}\b", outer) is not None


def match_option(text: str, options: tuple[str, ...]) -> str | None:
    """The option that stands for `text`: exact, then prefix, then contains (either way), after
    folding case and accents. The families "Other" and "Prefer not to say" match their
    equivalents. Two options at the same level make no match (ambiguous, left)."""
    want = fold(text)
    if not want or not options:
        return None
    folded = [(fold(option), option) for option in options]
    for test in (
        lambda o: o == want,
        lambda o: (len(want) >= 3 and o.startswith(want)) or want.startswith(f"{o} "),
        lambda o: _whole_words(want, o) or (len(o) >= 3 and _whole_words(o, want)),
    ):
        hits = [raw for f, raw in folded if f and test(f)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    for family in (_OTHER, _DECLINE, YES, NO):
        if want in family:
            picked = _family_option(family, options)
            if picked is not None:
                return picked
    return None


def _family_option(family: tuple[str, ...], options: tuple[str, ...]) -> str | None:
    """The one option that is a member of the family: the whole option or its start (yes, no), or
    the member as a phrase inside it for the wordy families (Other, Prefer not to say)."""
    loose = family is not YES and family is not NO
    hits = []
    for option in options:
        folded = fold(option)
        for member in family:
            if (
                folded == member
                or folded.startswith(f"{member} ")
                or (loose and re.search(rf"\b{re.escape(member)}\b", folded))
            ):
                hits.append(option)
                break
    return hits[0] if len(hits) == 1 else None


def _boolean(field: FormField, value: bool, source: str) -> Answer | Left:
    """A yes or no for a field of any kind: the boolean for a checkbox or a bare yes/no, the
    matching option for a choice."""
    if field.kind in ("checkbox",) or (field.kind == "yes_no" and not field.options):
        return Answer(value, source)
    if field.options:
        picked = _family_option(YES if value else NO, field.options)
        if picked is None:
            return Left("no_matching_option", field.options, field.required)
        return Answer(picked, source, option=picked)
    return Answer("Yes" if value else "No", source)


def _text(field: FormField, value: str, source: str) -> Answer | Left:
    """A sentence for a field: kept as written for text, matched to an option for a choice."""
    if field.kind in _CHOICES and field.options:
        picked = match_option(value, field.options)
        if picked is None:
            return Left("no_matching_option", field.options, field.required)
        return Answer((picked,) if field.kind == "multi" else picked, source, option=picked)
    if field.kind in ("yes_no", "checkbox", "file"):
        return Left("not_a_yes_or_no", field.options, field.required)
    return Answer(value, source)


# --- rules -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sections:
    identity: IdentitySection
    experience: ExperienceSection
    skills: SkillsSection
    answers: AnswersSection


Handler = Callable[[FormField, Context, Sections, str], Answer | Left | None]


def _left(field: FormField, reason: str) -> Left:
    return Left(reason, field.options, field.required)


def _identity_value(attr: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
        value = getattr(s.identity, attr)
        return _text(field, value, f"identity.{attr}") if value else _left(field, f"no_{attr}")

    return handler


def _name_part(part: Literal["first", "last", "full"]) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
        name = s.identity.full_name
        if not name:
            return _left(field, "no_full_name")
        words = name.split()
        if part == "full" or len(words) == 1:
            value = name if part != "last" else ""
        else:
            value = words[0] if part == "first" else " ".join(words[1:])
        return Answer(value, "identity.full_name") if value else _left(field, "no_last_name")

    return handler


def _preferred_name(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    if s.answers.preferred_name:
        return Answer(s.answers.preferred_name, "answers.preferred_name")
    return _left(field, "no_preferred_name")


def _pronouns(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    return (
        _text(field, s.answers.pronouns, "answers.pronouns")
        if s.answers.pronouns
        else _left(field, "no_pronouns")
    )


def _link(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    ident = s.identity
    if "linkedin" in text:
        url = ident.linkedin_url or next((k.url for k in ident.links if "linkedin" in k.url), None)
        source = "identity.linkedin_url"
    elif "github" in text:
        url = next((k.url for k in ident.links if "github" in k.url.lower()), None)
        source = "identity.links"
    else:
        url, source = ident.website, "identity.website"
        if not url and ident.links:
            url, source = ident.links[0].url, "identity.links"
    return Answer(url, source) if url else _left(field, "no_link")


def _languages(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    known = {fold(lang.name): lang for lang in s.identity.languages}
    if field.kind in ("yes_no", "checkbox"):
        for name in known:
            if re.search(rf"\b{re.escape(name)}\b", text):
                return _boolean(field, True, "identity.languages")
        return _left(field, "language_not_in_profile")
    if not known:
        return _left(field, "no_languages")
    listed = ", ".join(
        lang.name + (f" ({lang.level})" if lang.level else "") for lang in s.identity.languages
    )
    return _text(field, listed, "identity.languages")


def _countries_entries(
    field: FormField, ctx: Context, s: Sections
) -> tuple[list[WorkAuthorization], Left | None]:
    if ctx.posting is None or not ctx.posting.countries:
        return [], _left(field, "no_posting_country")
    by_country = {fold(w.country): w for w in s.answers.work_authorization}
    entries = []
    for country in ctx.posting.countries:
        entry = by_country.get(fold(country))
        if entry is None:
            return [], _left(field, "country_not_in_work_authorization")
        entries.append(entry)
    return entries, None


def _work_authorization(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    entries, problem = _countries_entries(field, ctx, s)
    if problem:
        return problem
    needs = {e.needs_sponsorship for e in entries}
    sponsorship = bool(re.search(r"sponsor|visa|parrain", text))
    boolean_form = field.kind in ("yes_no", "checkbox") or (
        field.kind in _CHOICES and _family_option(YES, field.options) is not None
    )
    if boolean_form and len(needs) == 1 and None not in needs:
        needed = needs.pop()
        return _boolean(
            field, bool(needed) if sponsorship else not needed, "answers.work_authorization"
        )
    if boolean_form:
        return _left(field, "countries_disagree_or_unstated")
    answers = {e.answer for e in entries}
    if len(answers) != 1:
        return _left(field, "countries_disagree_or_unstated")
    return _text(field, answers.pop(), "answers.work_authorization")


def _plain_answer(attr: str, reason: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
        value = getattr(s.answers, attr)
        return _text(field, value, f"answers.{attr}") if value else _left(field, reason)

    return handler


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _start_date(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    value = s.answers.earliest_start
    if not value:
        return _left(field, "no_earliest_start")
    if field.kind == "date" and not _ISO_DATE.match(value):
        return _left(field, "start_is_not_a_date")
    return _text(field, value, "answers.earliest_start")


def _salary(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    a = s.answers
    stance = a.salary_stance_of
    if stance == "leave_blank":
        return _left(field, "salary_leave_blank")
    if stance == "sentence":
        if not a.salary_policy:
            return _left(field, "no_salary_sentence")
        return _text(field, a.salary_policy, "answers.salary_policy")
    codes = {"€": "EUR", "£": "GBP", "$": "USD", "chf": "CHF"}
    wanted = {c for sym, c in codes.items() if sym in text}
    wanted |= {r.currency for r in a.salary_ranges if r.currency.lower() in text.split()}
    ranges = [r for r in a.salary_ranges if not wanted or r.currency in wanted]
    if len(ranges) != 1 or field.kind not in ("text", "textarea"):
        return _left(field, "no_single_salary_range")
    r = ranges[0]
    span = f"{r.minimum} to {r.maximum}" if r.maximum is not None else f"from {r.minimum}"
    return Answer(f"{span} {r.currency} per {r.period}", "answers.salary_ranges")


_EEO_TOPICS: tuple[tuple[str, str], ...] = (
    ("sexual_orientation", r"sexual orientation|orientation sexuelle"),
    ("gender", r"\bgender\b|\bsex\b|\bsexe\b|\bgenre\b|gender identity"),
    ("ethnicity", r"ethnic|origine"),
    ("race", r"\brace\b|racial"),
    ("disability", r"disabilit|handicap"),
    ("veteran", r"veteran|military|ancien combattant"),
    ("age", r"\bage\b|date of birth|birth ?date|date de naissance|\bbirthday\b"),
)


def eeo_topic(text: str) -> str | None:
    for topic, pattern in _EEO_TOPICS:
        if re.search(pattern, text):
            return topic
    return None


def _eeo(topic: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
        policy = s.answers.eeo_policy
        if policy == "leave":
            return _left(field, "eeo_policy_leave")
        if policy == "stored":
            stored = next((e.answer for e in s.answers.eeo_answers if e.topic == topic), None)
            if stored is None:
                return _left(field, "eeo_not_stored")
            result = _text(field, stored, f"answers.eeo_answers.{topic}")
            return result
        if not field.options:
            return _left(field, "eeo_no_decline_option")
        picked = _family_option(_DECLINE, field.options)
        if picked is None:
            return _left(field, "eeo_no_decline_option")
        return Answer((picked,) if field.kind == "multi" else picked, "answers.eeo_policy", picked)

    return handler


def _years(s: Sections) -> float | None:
    if s.skills.years_experience is not None:
        return s.skills.years_experience
    found = re.search(r"\d+(?:[.,]\d+)?", s.answers.years_experience_stated or "")
    return float(found.group().replace(",", ".")) if found else None


_THRESHOLD = re.compile(r"(\d+)\s*\+?\s*(?:or more\s+|ans?\s+|years?\s+)?(?:years?|ans)")


def _subject(text: str) -> str:
    found = re.search(r"(?:years?|ans)\b.{0,12}?\b(?:of|with|in|d'|de|en|avec)\b\s*(.+)", text)
    subject = (
        re.sub(r"\bexperience\b|\bexperiences\b|expérience|experience", "", found.group(1))
        if found
        else ""
    )
    return re.sub(r"[^a-z0-9+#. ]+", " ", subject).strip()


def _in_skills(subject: str, s: Sections) -> bool:
    if not subject:
        return True
    have = [c for c in s.skills.have]
    for category in have:
        names = [category.name, *category.terms]
        if any(fold(n) and re.search(rf"\b{re.escape(fold(n))}\b", subject) for n in names):
            return True
    return False


def _range_option(options: tuple[str, ...], years: float) -> str | None:
    hits = []
    for option in options:
        numbers = [float(n) for n in re.findall(r"\d+(?:[.,]\d+)?", option.replace(",", "."))]
        text = fold(option)
        if (
            (len(numbers) >= 2 and numbers[0] <= years <= numbers[1] + 0.999)
            or (
                len(numbers) == 1
                and re.search(r"\+|or more|plus|more than", text)
                and years >= numbers[0]
            )
            or (
                len(numbers) == 1
                and re.search(r"less than|under|moins|<", text)
                and years < numbers[0]
            )
        ):
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _experience_years(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    years = _years(s)
    threshold = _THRESHOLD.search(text)
    if field.kind in ("yes_no", "checkbox") or (
        threshold and field.kind in _CHOICES and _family_option(YES, field.options)
    ):
        if not threshold:
            return _left(field, "no_threshold")
        if years is None:
            return _left(field, "years_not_stated")
        if years < float(threshold.group(1)):
            return _boolean(field, False, "skills.years_experience")
        if _in_skills(_subject(text), s):
            return _boolean(field, True, "skills.years_experience")
        return _left(field, "subject_not_proven")
    if years is None:
        return _left(field, "years_not_stated")
    if field.kind in _CHOICES and field.options:
        picked = _range_option(field.options, years)
        if picked is None:
            return _left(field, "no_matching_option")
        return Answer(
            (picked,) if field.kind == "multi" else picked, "skills.years_experience", picked
        )
    stated = s.answers.years_experience_stated
    if stated and s.skills.years_experience is None:
        return Answer(stated, "answers.years_experience_stated")
    shown = f"{years:g}"
    return Answer(shown, "skills.years_experience")


def _latest_school(s: Sections) -> Any:
    schools = s.experience.education
    return schools[0] if schools else None


def _education(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    degree, source = s.answers.highest_degree, "answers.highest_degree"
    if not degree:
        school = _latest_school(s)
        degree, source = (school.degree, "experience.education") if school else (None, "")
    return _text(field, degree, source) if degree else _left(field, "no_degree")


def _graduation_year(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    if s.answers.highest_degree_year:
        return _text(field, str(s.answers.highest_degree_year), "answers.highest_degree_year")
    school = _latest_school(s)
    year = re.match(r"\d{4}", school.end or "") if school else None
    if year:
        return _text(field, year.group(), "experience.education")
    return _left(field, "no_graduation_year")


def _school(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    school = _latest_school(s)
    return (
        _text(field, school.school, "experience.education") if school else _left(field, "no_school")
    )


def _heard(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    heard = s.answers.heard_about
    if not heard:
        return _left(field, "no_heard_about")
    if field.kind in _CHOICES and field.options:
        picked = match_option(heard, field.options) or _family_option(_OTHER, field.options)
        if picked is None:
            return _left(field, "no_matching_option")
        return Answer((picked,) if field.kind == "multi" else picked, "answers.heard_about", picked)
    return _text(field, heard, "answers.heard_about")


def _previously_applied(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    if ctx.applied_before:
        return _boolean(field, True, "applications")
    default = s.answers.previously_applied_default
    if default is None:
        return _left(field, "no_previously_applied_default")
    return _boolean(field, default, "answers.previously_applied_default")


def _flag(attr: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
        value = getattr(s.answers, attr)
        if value is None:
            return _left(field, f"no_{attr}")
        if field.kind in ("text", "textarea"):
            return _left(field, "not_a_yes_or_no")
        return _boolean(field, value, f"answers.{attr}")

    return handler


def _document(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    docs = ctx.documents
    letter = bool(re.search(r"cover|letter|lettre|motivation", text))
    if field.kind == "file":
        path = (docs.letter_path if letter else docs.cv_path) if docs else None
        return (
            Answer(path, "documents.letter" if letter else "documents.cv")
            if path
            else _left(field, "no_document")
        )
    if letter and docs and docs.letter_text and field.kind in ("text", "textarea"):
        return Answer(docs.letter_text, "documents.letter_text")
    return _left(field, "free_text_for_the_agent")


def _free_text(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    return _left(field, "free_text_for_the_agent")


def _location(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    parts = [p for p in (s.identity.city, s.identity.country) if p]
    if not parts:
        return _left(field, "no_location")
    return _text(field, ", ".join(parts), "identity.city")


# Order matters: the first rule whose pattern matches the folded label decides the field.
RULES: tuple[tuple[str, str, Handler], ...] = (
    (
        "preferred_name",
        r"preferred (?:first )?name|nickname|prenom d'usage|nom d'usage|surnom",
        _preferred_name,
    ),
    ("pronouns", r"pronoun|pronom", _pronouns),
    (
        "eeo_sexual_orientation",
        r"sexual orientation|orientation sexuelle",
        _eeo("sexual_orientation"),
    ),
    ("eeo_gender", r"\bgender\b|\bsex\b|\bsexe\b|\bgenre\b", _eeo("gender")),
    ("eeo_ethnicity", r"ethnic|origine ethnique", _eeo("ethnicity")),
    ("eeo_race", r"\brace\b|racial", _eeo("race")),
    ("eeo_disability", r"disabilit|handicap", _eeo("disability")),
    ("eeo_veteran", r"veteran|military service|ancien combattant", _eeo("veteran")),
    ("eeo_age", r"\bage\b|date of birth|birth ?date|date de naissance|birthday", _eeo("age")),
    ("first_name", r"first name|given name|forename|\bprenom\b", _name_part("first")),
    (
        "last_name",
        r"last name|surname|family name|nom de famille|^nom$|^nom \*?$",
        _name_part("last"),
    ),
    (
        "full_name",
        r"full name|legal name|nom complet|nom et prenom|prenom et nom|^name$|^your name$|^nom prenom$",
        _name_part("full"),
    ),
    ("email", r"e-?mail|courriel", _identity_value("email")),
    ("phone", r"phone|mobile|telephone|portable|\btel\b", _identity_value("phone")),
    ("linkedin", r"linkedin", _link),
    ("links", r"github|portfolio|website|site web|personal (?:site|page)|\burl\b|\blien\b", _link),
    (
        "authorization",
        r"authori[sz]ed to work|authori[sz]ation to work|right to work|legally (?:able|entitled|eligible)|eligible to work|work permit|work authori[sz]ation|autorisation de travail|autorise a travailler|permis de travail|droit de travailler",
        _work_authorization,
    ),
    ("sponsorship", r"sponsor|visa|parrain", _work_authorization),
    (
        "notice",
        r"notice period|preavis|how soon can you|when can you (?:join|start)",
        _plain_answer("notice_period", "no_notice_period"),
    ),
    (
        "start_date",
        r"earliest start|start date|available (?:from|to start)|availability|date de debut|date de prise de poste|disponibilite|disponible a partir|date de demarrage",
        _start_date,
    ),
    (
        "relocation",
        r"relocat|demenag|mobilite geographique",
        _plain_answer("relocation", "no_relocation"),
    ),
    (
        "travel",
        r"willing(?:ness)? to travel|travel(?:ling)? (?:required|requirement)|deplacements?",
        _plain_answer("travel", "no_travel"),
    ),
    ("onsite", r"on-?site|in-?office|sur site|presentiel", _flag("willing_onsite")),
    ("hybrid", r"hybrid|hybride", _flag("willing_hybrid")),
    (
        "background_check",
        r"background check|criminal record|verification des antecedents|casier judiciaire",
        _flag("background_check_consent"),
    ),
    (
        "reference_check",
        r"reference check|references? (?:check|contact)|contacter vos references",
        _flag("reference_check_consent"),
    ),
    (
        "salary",
        r"salary|compensation|remuneration|pay expectation|prtentions|pretentions|salaire|expected pay|rate expectation|tarif",
        _salary,
    ),
    (
        "previously_applied",
        r"previously (?:applied|worked|employed)|applied (?:to|before|for a position)|(?:ever|already) (?:applied|worked)|deja (?:postule|travaille)|ancien(?:ne)? (?:salarie|employe)",
        _previously_applied,
    ),
    (
        "how_heard",
        r"how did you (?:hear|find|learn)|where did you (?:hear|find)|source of application|comment avez-vous (?:entendu|connu|decouvert)|referral source",
        _heard,
    ),
    (
        "years_experience",
        r"years? of (?:\w+ )*experience|(?:\d+) ?\+? ?years|annees? d'experience|ans d'experience|how many years|combien d'annees",
        _experience_years,
    ),
    (
        "highest_degree",
        r"highest (?:level of )?(?:education|degree)|level of education|education level|niveau d'etudes|niveau de formation|diplome|^degree$",
        _education,
    ),
    (
        "graduation_year",
        r"graduation (?:year|date)|year of graduation|annee d'obtention|annee de diplome",
        _graduation_year,
    ),
    ("school", r"school|university|universite|ecole|institution|college", _school),
    ("languages", r"language|langue|\bspeak\b|fluen|proficien|parlez", _languages),
    ("cover_letter", r"cover letter|lettre de motivation|motivation letter", _document),
    ("resume", r"resume|\bcv\b|curriculum", _document),
    (
        "location",
        r"current location|where are you (?:based|located)|\bcity\b|\bville\b|^location$|localisation|lieu de residence",
        _location,
    ),
    ("country", r"\bcountry\b|\bpays\b|country of residence", _identity_value("country")),
    (
        "free_text",
        r"why|pourquoi|tell us|motivation|describe|decrivez|racontez|cover|anything else|additional information|informations? complementaires?",
        _free_text,
    ),
)
_COMPILED = tuple((name, re.compile(pattern), handler) for name, pattern, handler in RULES)


def _leave_blank_hit(text: str, s: Sections) -> bool:
    for item in s.answers.leave_blank:
        topic = fold(item.split(":", 1)[0])
        if 2 < len(topic) <= 40 and re.search(rf"\b{re.escape(topic)}\b", text):
            return True
    return False


# A checkbox that asks about the person (a yes or no in disguise) goes by the rules below.
_BOX_QUESTION = re.compile(r"relocat|on-?site|hybrid|authori[sz]ed|sponsor|willing|eligible")


def _checkbox(field: FormField) -> Answer | Left:
    box = terms.classify(field.label)
    if box.allowed:
        return Answer(True, f"terms.{box.kind}")
    return Left("person_choice", (), field.required)


def resolve(field: FormField, context: Context) -> Answer | Left:
    """The answer to one field of a form, or why it is left. Never raises on a form's content."""
    text = fold(field.label)
    if not text:
        return Left("no_label", field.options, field.required)
    sections = Sections(
        identity=_model(IdentitySection, context.sections.get("identity")),
        experience=_model(ExperienceSection, context.sections.get("experience")),
        skills=_model(SkillsSection, context.sections.get("skills")),
        answers=_model(AnswersSection, context.sections.get("answers")),
    )
    if field.kind == "checkbox" and not _BOX_QUESTION.search(text):
        return _checkbox(field)  # a legal box, not a question about the person
    if _leave_blank_hit(text, sections):
        return Left("person_leave_blank", field.options, field.required)
    bank = context.bank.lookup(field.label) if context.bank else None
    if bank is not None and field.kind not in ("file", "checkbox"):
        if field.kind in _CHOICES and field.options and field.kind != "yes_no":
            picked = match_option(bank, field.options)
            if picked is None:
                return Left("no_matching_option", field.options, field.required)
            return Answer((picked,) if field.kind == "multi" else picked, "bank", picked)
        if field.kind == "yes_no" and fold(bank) in (*YES, *NO):
            return _boolean(field, fold(bank) in YES, "bank")
        return Answer(bank, "bank")
    for _name, pattern, handler in _COMPILED:
        if pattern.search(text):
            result = handler(field, context, sections, text)
            if result is not None:
                return result
    if field.kind == "checkbox":
        return _checkbox(field)
    return Left("unknown_question", field.options, field.required)
