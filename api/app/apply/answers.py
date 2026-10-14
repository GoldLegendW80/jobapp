# ruff: noqa: E501, the pattern table below reads best one rule to a line
"""Answers to the standard questions of an application form, from the profile (SPEC-apply,
"Answers to standard questions"). No model, no network, no clock, nothing invented.

`resolve(field, context)` gives an `Answer` (a value and the source it came from) or a `Left`
(why nothing was answered, and the options of the field so the person can choose). An answer is
given only on a positive, unambiguous match of a question the module fully understands; anything
else is left, because a left field costs the person one field and a wrong answer is a false
statement in their name. Order: the person's policies first (leave blank, EEO, salary stance) and
the fields no source may fill (someone else's details, age, citizenship, current pay, free text);
then the person's answer bank; then the identity section, the documents, the `answers` section,
the experience, education and skills sections. A required question no source holds is left.

Work authorisation is answered for the country the question names, else the posting's, and only
for a question made of known words; years of experience only for overall experience, with the
question's comparison read as written.

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
    "i don't want to answer",
    "i do not want to answer",
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


def _option_words(option: str) -> str:
    """An option folded with its punctuation gone: "Yes, I am" reads "yes i am"."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", fold(option))).strip()


def _member(family: tuple[str, ...], words: str) -> bool:
    return any(words == member or words.startswith(f"{member} ") for member in family)


def _family_option(family: tuple[str, ...], options: tuple[str, ...]) -> str | None:
    """The one option that is a member of the family. Yes and no: the bare word first ("Yes",
    "No, I do not"), then the phrases ("I am", "I do not"); an option that also reads as the
    other answer or as a decline is never one of them. The wordy families (Other, Prefer not to
    say): the member as a phrase inside the option."""
    if family is YES or family is NO:
        other = NO if family is YES else YES
        for tier in (family[:4], family):
            hits = [
                option
                for option in options
                if _member(tier, words := _option_words(option))
                and not (family is YES and _member(other, words))
                and not _member(_DECLINE, words)
                and not any(d in words for d in _DECLINE)
            ]
            if hits:
                return hits[0] if len(hits) == 1 else None
        return None
    hits = []
    for option in options:
        folded = fold(option)
        if any(
            folded == member or re.search(rf"\b{re.escape(member)}\b", folded) for member in family
        ):
            hits.append(option)
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


def _words(*lines: str) -> frozenset[str]:
    """A vocabulary written as lines of space separated words."""
    return frozenset(word for line in lines for word in line.split())


# The countries and regions a question can name, English and French, nouns and adjectives. A
# profile entry's country is read through the same table, so "UK" and "United Kingdom" agree.
_COUNTRY_NAMES: tuple[tuple[str, str], ...] = (
    (
        "European Union",
        r"\beu\b|\be\.u\.|european union|\beea\b|european economic area|\beurope\b|\beuropean\b"
        r"|union europeenne|\bue\b|espace economique europeen|\beurope(?:en|enne)s?\b|schengen",
    ),
    (
        "United States",
        r"united states|\bthe us\b|\bu\.s\.(?:a\.?)?|\busa\b|\bamerica\b|\bamerican\b"
        r"|etats-unis|etats unis|\bamericaine?s?\b",
    ),
    (
        "United Kingdom",
        r"united kingdom|\buk\b|\bu\.k\.|great britain|\bbritain\b|\bbritish\b|\bengland\b"
        r"|\bscotland\b|\bwales\b|royaume-uni|royaume uni|\bbritanniques?\b|angleterre",
    ),
    ("France", r"\bfrance\b|\bfrench\b|\bfrancaise?s?\b"),
    ("Germany", r"\bgermany\b|\bgerman\b|\ballemagne\b|\ballemande?s?\b"),
    ("Spain", r"\bspain\b|\bspanish\b|\bespagne\b|\bespagnole?s?\b"),
    ("Netherlands", r"netherlands|\bdutch\b|\bholland\b|pays-bas|pays bas|neerlandaise?s?"),
    ("Belgium", r"\bbelgium\b|\bbelgian\b|\bbelgique\b|\bbelges?\b"),
    ("Switzerland", r"switzerland|\bswiss\b|\bsuisses?\b"),
    ("Italy", r"\bitaly\b|\bitalian\b|\bitalie\b|\bitalienne?s?\b"),
    ("Portugal", r"\bportugal\b|\bportuguese\b|\bportugaise?s?\b"),
    ("Ireland", r"\bireland\b|\birish\b|\birlande\b|\birlandaise?s?\b"),
    ("Luxembourg", r"luxembourg"),
    ("Poland", r"\bpoland\b|\bpolish\b|\bpologne\b|\bpolonaise?s?\b"),
    ("Sweden", r"\bsweden\b|\bswedish\b|\bsuede\b|\bsuedoise?s?\b"),
    ("Denmark", r"\bdenmark\b|\bdanish\b|\bdanemark\b|\bdanoise?s?\b"),
    ("Austria", r"\baustria\b|\baustrian\b|\bautriche\b|\bautrichienne?s?\b"),
    ("Canada", r"\bcanada\b|\bcanadian\b|\bcanadienne?s?\b|\bquebec\b"),
    ("Singapore", r"\bsingapore\b|\bsingaporean\b|\bsingapour\b"),
    ("India", r"\bindia\b|\bindian\b|\binde\b"),
    ("Japan", r"\bjapan\b|\bjapanese\b|\bjapon\b"),
    ("Australia", r"\baustralia\b|\baustralian\b|\baustralie\b"),
    ("South Korea", r"\bkorea\b|\bkorean\b|\bcoree\b"),
    ("Israel", r"\bisrael\b|\bisraeli\b"),
    ("United Arab Emirates", r"united arab emirates|\buae\b|\bemirates\b|\bemirats\b|\bdubai\b"),
    ("China", r"\bchina\b|\bchinese\b|\bchine\b"),
)
_COUNTRY_PATTERNS = tuple((name, re.compile(pattern)) for name, pattern in _COUNTRY_NAMES)


def named_countries(label: str) -> tuple[tuple[str, ...], str]:
    """The countries and regions a label names, and its folded text with those names blanked.
    "US" counts only in capitals or after "the" ("join us" names no country)."""
    text = fold(label)
    if re.search(r"\bUS\b|\bU\.S\b", label or ""):
        text = re.sub(r"\bus\b", " the us ", text)
    names: list[str] = []
    for name, pattern in _COUNTRY_PATTERNS:
        if pattern.search(text):
            names.append(name)
            text = pattern.sub(" ", text)
    return tuple(names), re.sub(r"\s+", " ", text).strip()


def _country_key(name: str) -> str:
    named, _ = named_countries(name)
    return named[0] if len(named) == 1 else fold(name)


# A plain work authorisation question is made of these words and a country, nothing else: any
# other word ("other than", "your country of residence", "Quebec", "a spouse") is not understood.
_WORK_WORDS = _words(
    "a an any are be can could currently do does for future have hold in is legal legally lawful",
    "lawfully",
    "now of or right status the this to will would you your yes no country role position job",
    "employment company location located based where which here work working authorized",
    "authorised authorization authorisation authorizations eligible eligibility entitled able",
    "permitted allowed permit permits valid require required requires requirement need needs",
    "needed sponsor sponsored sponsorship visa visas immigration employer us time etes vous",
    "actuellement legalement autorise autorisee autorises autorisees autorisation de a au aux",
    "en dans le la les l d du des ce cet cette pays poste emploi travailler travail avez",
    "besoin un une parrainage sponsoring permis titre valide disposez possedez beneficiez",
    "droit necessitez necessiterez necessite aurez maintenant ou avenir futur pour etre",
    "eligible pouvez pourriez oui non entreprise societe e",
)
_NEGATION = re.compile(
    r"\bnot\b|n't\b|\bwithout\b|\bunable\b|\bcannot\b|\bnever\b|\bno longer\b|\bnone\b|\bexempt"
    r"|\bsans\b|\bne\b|\bn'|\bpas\b|\bjamais\b|\baucune?\b|\bplus besoin\b"
)
# "Can you work in the UK without sponsorship?" is the one negated form read on purpose: it asks
# whether the person is authorised with no sponsorship, the inverse of "do you need sponsorship".
_WITHOUT_SPONSORSHIP = re.compile(
    r"\b(?:without|sans)((?:\s+[a-z']+){0,4}?)\s+(?:sponsor\w*|parrainage)\b"
)
_WITHOUT_FILLER = _words(
    "needing requiring the need for a an any visa employer company employment immigration",
    "avoir besoin d' de un une du necessiter",
)
_NEEDS = re.compile(r"\brequir|\bneed|necessit|besoin|\bsponsor\w* (?:required|needed)")
_SPONSOR_WORDS = re.compile(r"sponsor|parrain|\bvisas?\b")
_AUTHORISED_WORDS = re.compile(
    r"authori[sz]|eligib|entitled|permitted|allowed|right to work|able to work|can you work"
    r"|work permit|autoris|permis de travail|droit de travailler|pouvez-vous travailler"
    r"|pouvez vous travailler"
)


def _work_question(label: str) -> tuple[str, tuple[str, ...]] | str:
    """What a work authorisation label asks, as (kind, countries named), or why it is not
    understood. Kinds: "needs" (do you need sponsorship), "authorised" (may you work there)."""
    countries, text = named_countries(label)
    text = re.sub(r"\(?\byes ?/ ?no\b\)?|\(?\boui ?/ ?non\b\)?", " ", text)
    inverted = False
    without = _WITHOUT_SPONSORSHIP.search(text)
    if without:
        filler = re.sub(r"d'", "d' ", without.group(1)).split()
        if any(word not in _WITHOUT_FILLER for word in filler):
            return "negated_question"
        text = text[: without.start()] + " " + text[without.end() :]
        inverted = True
    if _NEGATION.search(text):
        return "negated_question"
    if any(word not in _WORK_WORDS for word in re.findall(r"[a-z0-9]+", text)):
        return "question_not_understood"
    if len(countries) > 1:
        return "several_countries_named"
    if inverted:
        if _SPONSOR_WORDS.search(text):
            return "question_not_understood"
        return "authorised", countries
    if _SPONSOR_WORDS.search(text):
        # "Visa status" is a sentence for a text box; as a yes or no it means nothing settled.
        return ("needs" if _NEEDS.search(text) else "plain_status"), countries
    if _AUTHORISED_WORDS.search(text):
        return "authorised", countries
    return "plain_status", countries


def _countries_entries(
    field: FormField, ctx: Context, s: Sections, named: tuple[str, ...]
) -> tuple[list[WorkAuthorization], Left | None]:
    """The person's entries for the country the question names, else for the posting's."""
    if named:
        countries: tuple[str, ...] = named
    elif ctx.posting is None or not ctx.posting.countries:
        return [], _left(field, "no_posting_country")
    else:
        countries = ctx.posting.countries
    by_country = {_country_key(w.country): w for w in s.answers.work_authorization}
    entries = []
    for country in countries:
        entry = by_country.get(_country_key(country))
        if entry is None:
            return [], _left(field, "country_not_in_work_authorization")
        entries.append(entry)
    return entries, None


def _work_authorization(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    """Work authorisation and sponsorship, for the country the question names, else for the
    posting's. Answered only for a question made of known words: a negation other than
    "without sponsorship", a second country or an unknown word leaves the field."""
    asked = _work_question(field.label)
    if isinstance(asked, str):
        return _left(field, asked)
    kind, named = asked
    entries, problem = _countries_entries(field, ctx, s, named)
    if problem:
        return problem
    needs = {e.needs_sponsorship for e in entries}
    boolean_form = field.kind in ("yes_no", "checkbox") or (
        field.kind in _CHOICES and _family_option(YES, field.options) is not None
    )
    if boolean_form:
        if kind == "plain_status" or len(needs) != 1 or None in needs:
            return _left(field, "countries_disagree_or_unstated")
        needed = bool(needs.pop())
        return _boolean(
            field, needed if kind == "needs" else not needed, "answers.work_authorization"
        )
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


# A currency the label names: a range in another currency is never given for it.
_CURRENCY_SIGNS = (
    (r"€|\beuros?\b", "EUR"),
    (r"£|\bpounds?\b", "GBP"),
    (r"(?<![a-z])\$|\bdollars?\b", "USD"),
    (r"\bc\$|\bca\$", "CAD"),
    (r"\bs\$", "SGD"),
    (r"\ba\$", "AUD"),
)
_CURRENCY_CODES = (
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "CAD",
    "SGD",
    "AUD",
    "SEK",
    "DKK",
    "PLN",
    "JPY",
    "INR",
)


def _salary(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    a = s.answers
    stance = a.salary_stance_of
    if stance == "leave_blank":
        return _left(field, "salary_leave_blank")
    if stance == "sentence":
        if not a.salary_policy:
            return _left(field, "no_salary_sentence")
        return _text(field, a.salary_policy, "answers.salary_policy")
    wanted = {code for pattern, code in _CURRENCY_SIGNS if re.search(pattern, text)}
    words = set(re.findall(r"[a-z]+", text))
    wanted |= {code for code in _CURRENCY_CODES if code.lower() in words}
    wanted |= {r.currency for r in a.salary_ranges if r.currency.lower() in words}
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


_APPROXIMATE = re.compile(
    r"about|around|approx|roughly|nearly|almost|over|more than|environ|presque|pres de|plus de|~|\+"
)


def _years(s: Sections) -> tuple[float, float] | None:
    """The person's overall years as a span: exact for the skills number, half a year either side
    for a statement that hedges ("About 3.5 years")."""
    if s.skills.years_experience is not None:
        return s.skills.years_experience, s.skills.years_experience
    stated = s.answers.years_experience_stated or ""
    found = re.search(r"\d+(?:[.,]\d+)?", stated)
    if not found:
        return None
    years = float(found.group().replace(",", "."))
    if _APPROXIMATE.search(fold(stated)):
        return max(0.0, years - 0.5), years + 0.5
    return years, years


# An overall years question is made of these words, a number and a comparison, nothing else. A
# technology, a tool, a domain or a role ("with React", "relevant", "in management") is a subject
# the profile stores no years for.
_YEARS_WORDS = _words(
    "how many years year yrs of total overall professional work working experience",
    "experiences do you have has your the in number what is are yes no combien d annees annee",
    "ans an experience experiences professionnelle professionnelles professionnel",
    "professionnels avez vous au total nombre quel quelle est votre vos oui non",
)
_N = r"(\d+(?:[.,]\d+)?)"
# Each comparison, its operator; the first that matches decides, and its words are taken out.
_COMPARISONS: tuple[tuple[str, str], ...] = (
    (
        rf"\b(?:no more than|not more than|pas plus de|at most|up to|au plus|au maximum|maximum(?: of)?|jusqu'a)\s+{_N}",
        "le",
    ),
    (rf"\b(?:less than|fewer than|under|below|moins de|inferieure? a)\s+{_N}", "lt"),
    (rf"\b(?:more than|over|greater than|in excess of|plus de|superieure? a)\s+{_N}", "gt"),
    (
        rf"\b(?:at least|a minimum of|minimum(?: of)?|min\.?|au moins|au minimum|un minimum de)\s+{_N}",
        "ge",
    ),
    (rf"{_N}\s+(?:or more|or above|ou plus|et plus)\b", "ge"),
    (rf"{_N}\s+(?:or less|or fewer|ou moins)\b", "le"),
    (rf"{_N}\s*\+", "ge"),
    (rf"{_N}\s+(?:years?|ans?)\s+(?:or more|or longer|ou plus|et plus|minimum)\b", "ge_after"),
    (rf"{_N}\s+(?:years?|ans?)\s+(?:or less|or fewer|ou moins|maximum)\b", "le_after"),
)


def _comparison(text: str) -> tuple[str, float, str] | str | None:
    """The comparison a years question makes, as (operator, number, text without it); None for
    a question with no number; a reason for one it cannot read (two numbers, a range)."""
    if len(re.findall(r"\d+(?:[.,]\d+)?", text)) > 1:
        return "two_numbers"
    for pattern, op in _COMPARISONS:
        found = re.search(pattern, text)
        if found:
            rest = text[: found.start()] + " " + text[found.end() :]
            if op.endswith("_after"):
                rest += " years"
                op = op.removesuffix("_after")
            return op, float(found.group(1).replace(",", ".")), rest
    bare = re.search(rf"{_N}\s*(?:years?|ans?)\b", text)
    if bare:  # "Do you have 5 years of experience?" reads as at least five
        return (
            "ge",
            float(bare.group(1).replace(",", ".")),
            text[: bare.start()] + " years " + text[bare.end() :],
        )
    return None


def _holds(op: str, low: float, high: float, n: float) -> bool | None:
    """Whether the person's span satisfies `op n`: True, False, or None when the span straddles."""

    def test(y: float) -> bool:
        return {"ge": y >= n, "gt": y > n, "lt": y < n, "le": y <= n}[op]

    first, last = test(low), test(high)
    return first if first == last else None


def _option_span(option: str) -> tuple[float, float] | None:
    """The years an option covers, as [low, high), or None when it names no years."""
    text = fold(option)
    numbers = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text)]
    if len(numbers) == 2 and numbers[0] <= numbers[1]:
        return numbers[0], numbers[1] + 1
    if len(numbers) != 1:
        return None
    n = numbers[0]
    if re.search(r"less than|fewer than|under|below|moins de|inferieur|<", text):
        return 0.0, n
    if re.search(r"up to|at most|or less|ou moins|jusqu|maximum|max\b", text):
        return 0.0, n + 1
    if re.search(r"more than|over|plus de|superieur|>", text):
        return n + 1e-9, float("inf")
    if re.search(r"\+|or more|ou plus|et plus|at least|au moins|minimum", text):
        return n, float("inf")
    return n, n + 1


def _range_option(options: tuple[str, ...], low: float, high: float) -> str | None:
    """The one option whose span holds the person's whole span."""
    hits = []
    for option in options:
        span = _option_span(option)
        if span is not None and span[0] <= low and high < span[1] + (1e-9 if low != high else 0):
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _experience_years(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left:
    """Overall years of experience, and only those: the question must be about overall or
    professional experience, every comparison is read with its operator, and a span that
    straddles the threshold leaves the field."""
    asked = _comparison(re.sub(r"\(?\byes ?/ ?no\b\)?|\(?\boui ?/ ?non\b\)?", " ", text))
    if isinstance(asked, str):
        return _left(field, asked)
    rest = asked[2] if asked else text
    if _NEGATION.search(rest):
        return _left(field, "negated_question")
    if any(word not in _YEARS_WORDS for word in re.findall(r"[a-z0-9]+", rest)):
        return _left(field, "subject_years_not_stated")
    years = _years(s)
    boolean_form = field.kind in ("yes_no", "checkbox") or (
        field.kind in _CHOICES and _family_option(YES, field.options) is not None
    )
    if boolean_form:
        if asked is None:
            return _left(field, "no_threshold")
        if years is None:
            return _left(field, "years_not_stated")
        held = _holds(asked[0], years[0], years[1], asked[1])
        if held is None:
            return _left(field, "years_too_close_to_call")
        return _boolean(field, held, "skills.years_experience")
    if asked is not None:
        return _left(field, "not_a_yes_or_no")
    if years is None:
        return _left(field, "years_not_stated")
    if field.kind in _CHOICES and field.options:
        picked = _range_option(field.options, *years)
        if picked is None:
            return _left(field, "no_matching_option")
        return Answer(
            (picked,) if field.kind == "multi" else picked, "skills.years_experience", picked
        )
    stated = s.answers.years_experience_stated
    if stated and s.skills.years_experience is None:
        return Answer(stated, "answers.years_experience_stated")
    return Answer(f"{years[0]:g}", "skills.years_experience")


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


def _city(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    city = s.identity.city
    return _text(field, city, "identity.city") if city else _left(field, "no_city")


def _location(field: FormField, ctx: Context, s: Sections, _: str) -> Answer | Left:
    parts = [p for p in (s.identity.city, s.identity.country) if p]
    if not parts:
        return _left(field, "no_location")
    return _text(field, ", ".join(parts), "identity.city")


_SALARY = r"salary|compensation|remuneration|pay expectation|prtentions|pretentions|salaire|expected pay|rate expectation|tarif"


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
        r"authori[sz]ed to work|authori[sz]ation to work|right to work|legally (?:able|entitled|eligible)|eligible to work|work permit|work authori[sz]ation|autorisation de travail|autorise(?:\(e\)|e)?s? a travailler|permis de travail|droit de travailler",
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
        _SALARY,
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
    ("city", r"^(?:current |your )?(?:city|ville)(?: actuelle)?$", _city),
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


# --- policies first: what no source may answer, and the person's stated policies ------------

# A field about someone else: a referrer, a reference, an emergency contact, a manager.
_THIRD_PERSON = re.compile(
    r"\breferr|\breferee|\breferences?\b|emergency|next of kin|\bmanager'?s?\b|\bsupervisor"
    r"|\brecruiter|\bspouse|\bparrain(?:e|s)?\b|\bmarraine|personne a (?:contacter|prevenir)"
    r"|\brecommand|\bconjoint|\bresponsable\b|superieur hierarchique|\bguardian|\btuteur"
)
_HOW_HEARD = r"how did you (?:hear|find|learn)|where did you (?:hear|find)|source of application|comment avez-vous (?:entendu|connu|decouvert)|referral source"
_REFERENCE_CHECK = r"reference check|references? (?:check|contact)|contacter vos references"
# A question about age: never read as years of experience, and the profile stores no age.
_AGE = re.compile(
    r"\byears? old\b|\bold\b|\byears? of age\b|\bage of\b|legal age|age legal|age minimum"
    r"|\bmajeure?s?\b|\bmajorite\b|\b(?:16|18|21)\s*(?:\+|ans\b(?!\s*d))"
    r"|\b(?:over|at least|above|au moins|plus de)\s+(?:16|18|21)\b(?!\s*\+?\s*(?:years?|ans)\s+(?:of|d))"
)
_CITIZENSHIP = re.compile(r"citizen|nationalit|citoyen|passport|passeport")
_CURRENT_PAY = re.compile(
    r"\b(?:current|currently|present|previous|last|today'?s?|existing|actuel(?:le)?s?|dernier|derniere|precedente?)\b"
    r"(?:\W+\w+){0,3}?\W+(?:salary|compensation|pay|remuneration|salaire|package|base|ctc|wage|earn\w*|gagne\w*)\b"
    r"|\b(?:salary|compensation|remuneration|salaire|package|ctc|pay)\b(?:\W+\w+){0,3}?\W+(?:actuel(?:le)?|current|currently|today|now)\b"
)
_PRONOUNS = r"pronoun|pronom"


# A long answer the question asks the person to write: the agent's, whatever keyword it holds.
_FREE_TEXT_OPENER = re.compile(
    r"^(?:why|pourquoi|tell us|describe|decrivez|explain|expliquez|racontez|parlez-nous|what makes|qu'est-ce qui)\b"
)


def _policies_first(field: FormField, ctx: Context, s: Sections, text: str) -> Answer | Left | None:
    """What is settled before the answer bank is read: fields no source may fill (someone
    else's details, age, citizenship, current pay) and the person's policies (EEO, salary
    stance). A bank answer typed once never overrides a policy. None: go on to the bank."""
    if _THIRD_PERSON.search(text) and not re.search(f"{_HOW_HEARD}|{_REFERENCE_CHECK}", text):
        return _left(field, "third_person")
    if _AGE.search(text):
        return _left(field, "age_not_stored")
    if _CITIZENSHIP.search(text):
        return _left(field, "citizenship_not_stored")
    if _CURRENT_PAY.search(text):
        return _left(field, "current_salary_not_stored")
    if not re.search(_PRONOUNS, text):
        for name, pattern, handler in _COMPILED:
            if name.startswith("eeo_") and pattern.search(text):
                return handler(field, ctx, s, text)
    if _FREE_TEXT_OPENER.search(text) and (
        field.kind == "textarea" or text.startswith(("why", "pourquoi"))
    ):
        # Written for one company: a banked answer is never replayed to another.
        return _left(field, "free_text_for_the_agent")
    if re.search(_SALARY, text):
        return _salary(field, ctx, s, text)
    return None


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
    settled = _policies_first(field, context, sections, text)
    if settled is not None:
        return settled
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
