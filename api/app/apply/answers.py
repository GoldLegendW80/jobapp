# ruff: noqa: E501, the pattern table below reads best one rule to a line
"""Answers to the standard questions of an application form, from the profile (SPEC-apply,
"Answers to standard questions"). No model, no network, no clock, nothing invented.

`resolve(field, context)` gives an `Answer` (a value and the source it came from) or a `Left`
(why nothing was answered, and the options of the field so the person can choose).

A closed world: a label is answered only when, normalised (case, accents, punctuation, required
marks, a few filler words), it FULLY matches one of the canonical question templates below, each
an anchored pattern in English and French with typed slots (a country, a place, a language, a
currency, a years comparison) and its answer rule. Anything else is left as
"not_a_known_question": no keyword or partial match ever answers, because a left field costs the
person one field and a wrong answer is a false statement in their name. A label holding two
questions is left; a negated or inverted one is left unless a template is written for that
exact form ("without sponsorship").

Options too are a closed world: an answer picks an option only when the option's whole text is in
that answer's vocabulary (the yes and no options, the work authorisation status options by
category, "Required" and "Not required" for a question that asks whether sponsorship or a work
permit is needed, "I consent" and "I do not consent" for a consent question, the language
levels, the degrees and their levels) or reads exactly as the stored value. No option is ever picked
because it starts with or holds a word of the answer. A free value (a notice period, a salary or
relocation sentence) goes into text fields only. The field's kind has the last word: a file field
takes only a document, a date field only a stored date.

Order: the person's policies first (leave blank, the EEO policy, the salary stance) and the fields
no source may fill (someone else's details, age, citizenship and birthplace, a criminal record,
pay received, free text); then the person's answer bank, for the exact question it was typed for;
then the templates. A rule that matches but finds no value in the profile leaves the field.
Work authorisation is answered for the country the question names, else the posting's; a member
state of the EU with no entry of its own is answered from the person's EU entry; Europe, the EEA,
Schengen and EFTA read only an entry of their own. An entry's `needs_sponsorship` is the one
fact read: needing sponsorship there is needing a work permit there, and not being authorised
there (`_authorisation_option`). Holding a permit, or a visa, is never read. A context
without a posting (the profile run of A18a) leaves everything that needs the posting's country.
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
    cities: tuple[str, ...] = ()  # folded, the gazetteer's cities the location names

    @classmethod
    def at(cls, location: str | None, title: str | None = None) -> "Posting":
        cities = tuple(fold(p.name) for p in places_of(location) if p.kind == "city")
        return cls(posting_countries(location), title, cities)


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


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def option_key(text: str | None) -> str:
    """An option or a stored value as the vocabularies read it: folded, every mark but letters
    and digits gone. "Yes, I don't" reads "yes i don t"."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", fold(text))).strip()


# --- option vocabularies -----------------------------------------------------------------------
# A closed world for options too: an answer picks an option only when the option's whole text,
# read by `option_key`, is in the vocabulary of that answer. No option is ever picked because it
# starts with a word or holds a phrase ("I am a US citizen" is not a yes; "Sponsorship needed" is
# not chosen because a stored sentence holds it).

# The options that say yes, and no, in full, English and French.
YES_OPTION_TEXTS = (
    "Yes",
    "Oui",
    "Y",
    "True",
    "Yes, I am",
    "Yes, I do",
    "Yes, I will",
    "Yes, I would",
    "Yes, I can",
    "Yes, I have",
    "I am",
    "I do",
    "I will",
    "I would",
    "I can",
    "I have",
    "Oui, je le suis",
    "Oui, tout à fait",
)
NO_OPTION_TEXTS = (
    "No",
    "Non",
    "N",
    "False",
    "No, I am not",
    "No, I'm not",
    "No, I do not",
    "No, I don't",
    "No, I will not",
    "No, I won't",
    "No, I would not",
    "No, I wouldn't",
    "No, I cannot",
    "No, I can't",
    "No, I have not",
    "No, I haven't",
    "I am not",
    "I'm not",
    "I do not",
    "I don't",
    "I will not",
    "I won't",
    "I would not",
    "I wouldn't",
    "I cannot",
    "I can't",
    "I have not",
    "I haven't",
    "Non, je ne le suis pas",
    "Non, pas du tout",
)
YES_OPTIONS = frozenset(option_key(t) for t in YES_OPTION_TEXTS)
NO_OPTIONS = frozenset(option_key(t) for t in NO_OPTION_TEXTS)

# The options that state a work authorisation status, by category. An option is chosen only
# when its whole text is in one category AND that category is true for the person in the country
# asked: "authorised" when their entry needs no sponsorship, "requires_sponsorship" and
# "not_authorised" when it does.
# The profile stores no citizenship, residence permit or visa, so the last three categories are
# never true: such an option is known, and never chosen.
AUTHORISATION_OPTION_TEXTS: dict[str, tuple[str, ...]] = {
    "authorised": (
        "Authorized to work",
        "Authorised to work",
        "I am authorized to work",
        "I am authorised to work",
        "Authorized to work without sponsorship",
        "Authorised to work without sponsorship",
        "No sponsorship needed",
        "No sponsorship required",
        "Sponsorship not needed",
        "Sponsorship not required",
        "I do not need sponsorship",
        "I don't need sponsorship",
        "I do not require sponsorship",
        "I don't require sponsorship",
        "I will not need sponsorship",
        "I won't need sponsorship",
        "I will not require sponsorship",
        "I won't require sponsorship",
        "Autorisé à travailler",
        "Je suis autorisé à travailler",
        "Pas besoin de parrainage",
        "Aucun parrainage nécessaire",
        "Sans parrainage",
        "Yes, I am authorized",
        "Yes, I am authorised",
        "Yes, I am authorized to work",
        "Yes, I am authorised to work",
        "No, I do not require sponsorship",
        "No, I don't require sponsorship",
        "No, I do not need sponsorship",
        "No, I don't need sponsorship",
        "No, I will not require sponsorship",
        "No, I will not need sponsorship",
        "No, sponsorship not required",
        "No, sponsorship not needed",
        "Do not require sponsorship",
        "Don't require sponsorship",
        "Does not require sponsorship",
        "Do not need sponsorship",
        "Don't need sponsorship",
        "Does not need sponsorship",
        "Visa sponsorship not required",
        "Visa sponsorship not needed",
        "No visa sponsorship required",
        "No visa sponsorship needed",
        "I do not need visa sponsorship",
        "I don't need visa sponsorship",
        "I do not require visa sponsorship",
        "I don't require visa sponsorship",
        "I will not need visa sponsorship",
        "I will not require visa sponsorship",
        "Parrainage non nécessaire",
        "Oui, je suis autorisé",
        "Oui, je suis autorisé(e)",
        "Non, je n'ai pas besoin de parrainage",
    ),
    "requires_sponsorship": (
        "Sponsorship needed",
        "Sponsorship required",
        "Requires sponsorship",
        "Require sponsorship",
        "Needs sponsorship",
        "Need sponsorship",
        "I need sponsorship",
        "I require sponsorship",
        "I will need sponsorship",
        "I will require sponsorship",
        "I would need sponsorship",
        "I would require sponsorship",
        "Visa sponsorship needed",
        "Visa sponsorship required",
        "I need visa sponsorship",
        "I will need visa sponsorship",
        "I will require visa sponsorship",
        "Parrainage nécessaire",
        "Besoin de parrainage",
        "Besoin d'un parrainage",
        "J'ai besoin d'un parrainage",
        "Yes, I require sponsorship",
        "Yes, I need sponsorship",
        "Yes, I will require sponsorship",
        "Yes, I will need sponsorship",
        "Yes, sponsorship required",
        "Yes, sponsorship needed",
        "Yes, visa sponsorship required",
        "Yes, visa sponsorship needed",
        "I require visa sponsorship",
        "Oui, j'ai besoin d'un parrainage",
    ),
    # Not authorised to work there: true exactly when sponsorship is needed.
    "not_authorised": (
        "Not authorized to work",
        "Not authorised to work",
        "No, I am not authorized",
        "No, I am not authorised",
        "No, I am not authorized to work",
        "No, I am not authorised to work",
        "Non, je ne suis pas autorisé",
        "Non, je ne suis pas autorisé(e)",
    ),
    "citizen": (
        "Citizen",
        "I am a citizen",
        "I am a US citizen",
        "US citizen",
        "I am a British citizen",
        "British citizen",
        "EU citizen",
        "I am an EU citizen",
        "Citoyen",
        "Citoyen européen",
    ),
    "permanent_resident": (
        "Permanent resident",
        "I am a permanent resident",
        "Green card holder",
        "I have a green card",
        "I hold a green card",
        "Résident permanent",
    ),
    "visa_holder": (
        "On a visa",
        "I am on a visa",
        "Visa holder",
        "I hold a visa",
        "I hold a work visa",
        "I am on an H-1B visa",
        "H-1B visa",
        "I hold a Skilled Worker visa",
        "Titulaire d'un visa",
    ),
}
AUTHORISATION_OPTIONS: dict[str, str] = {
    option_key(text): category
    for category, texts in AUTHORISATION_OPTION_TEXTS.items()
    for text in texts
}

# The yes and no options of the EEO questions that word them per topic, for a stored "Yes" or
# "No" (the policy "stored"): the standard self identification wordings.
EEO_YES_NO_OPTION_TEXTS: dict[str, dict[bool, tuple[str, ...]]] = {
    "disability": {
        True: (
            "Yes, I have a disability",
            "Yes, I have a disability (or previously had a disability)",
            "Yes, I have a disability, or have had one in the past",
        ),
        False: ("No, I do not have a disability", "No, I don't have a disability"),
    },
    "veteran": {
        True: (
            "I am a protected veteran",
            "I identify as one or more of the classifications of protected veteran",
        ),
        False: ("I am not a protected veteran", "I am not a veteran"),
    },
}

# The options that give or refuse a consent (a background or a reference check), read only for
# a consent question and only from the person's stored consent.
CONSENT_OPTION_TEXTS: dict[bool, tuple[str, ...]] = {
    True: (
        "I consent",
        "I agree",
        "I accept",
        "I give my consent",
        "Yes, I consent",
        "Yes, I agree",
        "Yes, I accept",
        "Consent",
        "Agree",
        "Je consens",
        "J'accepte",
        "Je donne mon consentement",
        "Oui, je consens",
        "Oui, j'accepte",
    ),
    False: (
        "I do not consent",
        "I don't consent",
        "I do not agree",
        "I don't agree",
        "I do not accept",
        "I don't accept",
        "I disagree",
        "No, I do not consent",
        "No, I don't consent",
        "No, I do not agree",
        "No, I don't agree",
        "No, I do not accept",
        "No, I don't accept",
        "Do not consent",
        "Disagree",
        "Je ne consens pas",
        "Je n'accepte pas",
        "Non, je ne consens pas",
        "Non, je n'accepte pas",
    ),
}
# The options that say whether sponsorship or a work permit is needed, without naming it: read
# only for a question that asks whether it is needed ("Visa sponsorship required?").
NEED_OPTION_TEXTS: dict[bool, tuple[str, ...]] = {
    True: (
        "Required",
        "Needed",
        "Yes, required",
        "Yes, needed",
        "Requis",
        "Requise",
        "Nécessaire",
        "Oui, requis",
        "Oui, nécessaire",
    ),
    False: (
        "Not required",
        "Not needed",
        "No, not required",
        "No, not needed",
        "Non requis",
        "Non requise",
        "Pas requis",
        "Pas requise",
        "Non nécessaire",
        "Pas nécessaire",
        "Non, pas requis",
        "Non, pas nécessaire",
    ),
}


def _by_answer(texts: Mapping[bool, tuple[str, ...]]) -> dict[bool, frozenset[str]]:
    return {said: frozenset(option_key(t) for t in words) for said, words in texts.items()}


CONSENT_OPTIONS = _by_answer(CONSENT_OPTION_TEXTS)
NEED_OPTIONS = _by_answer(NEED_OPTION_TEXTS)

# A degree, read as (level, rank, name). Rank 0 is one degree (an MSc, a licence), rank 1 the
# name of its level (Master's degree): a stored degree picks an option of its own rank and name,
# else one naming its level, never a narrower one ("Master's degree" never picks "MSc", it may
# be an MA). Rank 2, the French count of years after the baccalaureate, only confirms a name
# beside it ("Master (Bac+5)"): a bare "Bac+3" is never picked, a 4 year bachelor may be Bac+4.
DEGREE_TEXTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Baccalauréat": ("secondary", ("Baccalauréat", "Bac")),
    "Licence": ("bachelor", ("Licence",)),
    "BSc": ("bachelor", ("BSc", "B.Sc.", "BS", "B.S.", "Bachelor of Science")),
    "BA": ("bachelor", ("BA", "B.A.", "Bachelor of Arts")),
    "BEng": ("bachelor", ("BEng", "B.Eng.", "Bachelor of Engineering")),
    "MSc": ("master", ("MSc", "M.Sc.", "Master of Science")),
    "MA": ("master", ("MA", "M.A.", "Master of Arts")),
    "MEng": ("master", ("MEng", "M.Eng.", "Master of Engineering")),
    "MBA": ("master", ("MBA", "M.B.A.", "Master of Business Administration")),
    "PhD": ("doctorate", ("PhD", "Ph.D.", "DPhil", "D.Phil.", "Doctor of Philosophy")),
}
DEGREE_LEVEL_TEXTS: dict[str, tuple[str, ...]] = {
    "secondary": ("High school", "High school diploma", "Secondary school", "Secondary education"),
    "associate": ("Associate degree", "Associate's degree", "Associates degree", "Associate's"),
    "bachelor": (
        "Bachelor",
        "Bachelors",
        "Bachelor's",
        "Bachelor degree",
        "Bachelors degree",
        "Bachelor's degree",
    ),
    "master": (
        "Master",
        "Masters",
        "Master's",
        "Master degree",
        "Masters degree",
        "Master's degree",
        "Master 2",
    ),
    "doctorate": ("Doctorate", "Doctorate degree", "Doctoral degree", "Doctorat"),
}
DEGREE_SCALE_TEXTS = {
    "associate": "Bac+2",
    "bachelor": "Bac+3",
    "master": "Bac+5",
    "doctorate": "Bac+8",
}
_DEGREE_OF: dict[str, tuple[str, int, str]] = {
    **{option_key(t): (level, 0, name) for name, (level, ts) in DEGREE_TEXTS.items() for t in ts},
    **{option_key(t): (level, 1, level) for level, ts in DEGREE_LEVEL_TEXTS.items() for t in ts},
    **{option_key(t): (level, 2, level) for level, t in DEGREE_SCALE_TEXTS.items()},
}

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


Words = Mapping[bool, frozenset[str]]


def _yes_no_option(value: bool, options: tuple[str, ...], words: Words | None = None) -> str | None:
    """The one option whose whole text says yes (or no), from the vocabulary and the words of
    the question (consent, needed); none or two is no match."""
    vocabulary = (YES_OPTIONS if value else NO_OPTIONS) | (words[value] if words else frozenset())
    hits = [option for option in options if option_key(option) in vocabulary]
    return hits[0] if len(hits) == 1 else None


def match_option(text: str, options: tuple[str, ...]) -> str | None:
    """The option that stands for `text`: the one whose whole text reads the same (case, accents
    and punctuation aside), never a prefix or a part of it. A bare yes or no takes the one option
    of the yes or no vocabulary; "Other" and "Prefer not to say" take their family's one option.
    Two options make no match (ambiguous, left)."""
    want = option_key(text)
    if not want or not options:
        return None
    hits = [option for option in options if option_key(option) == want]
    if hits:
        return hits[0] if len(hits) == 1 else None
    if want in YES_OPTIONS or want in NO_OPTIONS:
        return _yes_no_option(want in YES_OPTIONS, options)
    for family in (_OTHER, _DECLINE):
        if fold(text) in family:
            return _family_option(family, options)
    return None


def _family_option(family: tuple[str, ...], options: tuple[str, ...]) -> str | None:
    """The one option that holds a member of a wordy family (Other, Prefer not to say) as a
    phrase: "Decline to self-identify" reads "decline to self identify"."""
    hits = []
    for option in options:
        words = re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", fold(option))).strip()
        if any(
            words == member or re.search(rf"\b{re.escape(member)}\b", words) for member in family
        ):
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _boolean(
    field: FormField, value: bool, source: str, words: Words | None = None
) -> Answer | Left:
    """A yes or no for a field of any kind: the boolean for a checkbox or a bare yes/no, the one
    option of the yes or no vocabulary (and the question's own words) for a choice."""
    if field.kind in ("checkbox",) or (field.kind == "yes_no" and not field.options):
        return Answer(value, source)
    if field.options:
        picked = _yes_no_option(value, field.options, words)
        if picked is None:
            return Left("no_matching_option", field.options, field.required)
        return Answer(picked, source, option=picked)
    return Answer("Yes" if value else "No", source)


def _text(field: FormField, value: str, source: str, free: bool = False) -> Answer | Left:
    """A stored value for a field: kept as written for text, the option that reads the same for
    a choice. A free value (a notice period, a salary sentence, a relocation or travel sentence,
    a start date) goes into text fields only: it is never read against options."""
    if field.kind in _CHOICES and field.options:
        picked = None if free else match_option(value, field.options)
        if picked is None:
            return Left("no_matching_option", field.options, field.required)
        return Answer((picked,) if field.kind == "multi" else picked, source, option=picked)
    if field.kind in ("yes_no", "checkbox", "file"):
        return Left("not_a_yes_or_no", field.options, field.required)
    if field.kind == "date" and not _ISO_DATE.match(value):
        return Left("not_a_date", field.options, field.required)
    return Answer(value, source)


# --- normalising a label -------------------------------------------------------------------


# A verb that only asks for the field ("Select your gender") is dropped with its "your".
_LEAD_IN = re.compile(
    r"^(?:(?:please|kindly|veuillez|merci de|merci d) )?(?:indicate|select|enter|provide|specify"
    r"|state|choose|indiquer|preciser|renseigner|saisir|selectionner|indiquez|precisez|renseignez"
    r"|selectionnez|saisissez) (?:your |votre |vos )?"
)
_FILLER = re.compile(r"\b(?:please|kindly|currently|actuellement|svp)\b")


def normalise(label: str | None) -> str:
    """A label as the templates read it: folded, its required marks, "(yes/no)", "(e)" and
    punctuation gone (a decimal point and "+", "/", "€", "£", "$" kept), a few filler words and
    a leading "Please select your" dropped. "Êtes-vous autorisé(e) à travailler en France ?"
    reads "etes vous autorise a travailler en france"; "the US" in capitals reads "usa"."""
    raw = re.sub(r"\bU\.S\.(?:A\.)?|\bU\.S\b|\bUSA\b|\bUS\b", " USA ", label or "")
    text = fold(raw)
    text = re.sub(r"\((?:required|optional|obligatoire|facultatif)\)|[*✱]", " ", text)
    text = re.sub(r"\(?\b(?:yes ?(?:/|or) ?no|oui ?(?:/|ou) ?non)\b\)?", " ", text)
    text = re.sub(r"\((?:e|s|es|ne)\)", "", text)
    text = re.sub(r"[^a-z0-9+€£$/. ]+", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s+", " ", _FILLER.sub(" ", text)).strip()
    return _LEAD_IN.sub("", text).strip()


# Two questions in one label: never answered as one. The pairs below are one question each.
_ONE_QUESTION_PAIRS = re.compile(
    r"now or in the future|maintenant ou (?:a l avenir|dans le futur)|race / ethnicity|yes or no|oui ou non"
    r"|resume / cv|cv / resume|(?:portfolio|website) (?:/|or) (?:portfolio|website)|website blog (?:/|or) portfolio|hispanic (?:/|or) latino|\b(?:or|ou) (?:more|less|fewer|above|longer|plus|moins)\b"
)
_TWO_QUESTIONS = re.compile(
    r"\bor\b| / |\band (?:do|are|have|will|would|can|did|is) you\b"
    r"|\bet (?:avez|etes|pouvez|serez|aurez|parlez) vous\b"
)


def _two_questions(label: str, text: str) -> bool:
    # "ou" is read before folding: "où" (where) is one question, "ou" (or) joins two.
    lower = re.sub(r"maintenant ou", " ", _ONE_QUESTION_PAIRS.sub(" ", (label or "").lower()))
    return (
        label.count("?") > 1
        or _TWO_QUESTIONS.search(_ONE_QUESTION_PAIRS.sub(" ", text)) is not None
        or re.search(r"\bou\b", lower) is not None
    )


# A negated or inverted question. Only a template written for its exact negated form answers it.
_NEGATED = re.compile(
    r"\bnot\b|n t\b|\bwithout\b|\bunable\b|\bunwilling\b|\bcannot\b|\bnever\b|\bno longer\b"
    r"|\bnone\b|\bproblem\b|\bdeal ?breaker\b|\bobject\b|\bsans\b|\bne\b|\bn\b(?! de\b)|\bpas\b"
    r"|\bjamais\b|\baucune?\b|\bplus besoin\b"
)


# --- slots -------------------------------------------------------------------------------------

# The countries and regions a question can name, as normalised phrases, English and French. A
# profile entry's country is read through the same table, so "UK" and "United Kingdom" agree.
EU = "European Union"
EU_MEMBERS: dict[str, tuple[str, ...]] = {
    "Austria": ("austria", "autriche"),
    "Belgium": ("belgium", "belgique"),
    "Bulgaria": ("bulgaria", "bulgarie"),
    "Croatia": ("croatia", "croatie"),
    "Cyprus": ("cyprus", "chypre"),
    "Czechia": ("czechia", "czech republic", "republique tcheque", "tchequie"),
    "Denmark": ("denmark", "danemark"),
    "Estonia": ("estonia", "estonie"),
    "Finland": ("finland", "finlande"),
    "France": ("france",),
    "Germany": ("germany", "allemagne"),
    "Greece": ("greece", "grece"),
    "Hungary": ("hungary", "hongrie"),
    "Ireland": ("ireland", "irlande"),
    "Italy": ("italy", "italie"),
    "Latvia": ("latvia", "lettonie"),
    "Lithuania": ("lithuania", "lituanie"),
    "Luxembourg": ("luxembourg",),
    "Malta": ("malta", "malte"),
    "Netherlands": ("netherlands", "holland", "pays bas"),
    "Poland": ("poland", "pologne"),
    "Portugal": ("portugal",),
    "Romania": ("romania", "roumanie"),
    "Slovakia": ("slovakia", "slovaquie"),
    "Slovenia": ("slovenia", "slovenie"),
    "Spain": ("spain", "espagne"),
    "Sweden": ("sweden", "suede"),
}
COUNTRIES: dict[str, tuple[str, ...]] = {
    # Only the EU itself and its member states read the EU entry. Europe, the EEA, Schengen and
    # EFTA are other regions (Switzerland, Norway, the UK): each reads only an entry of its own.
    EU: ("european union", "eu", "e u", "union europeenne", "ue"),
    "Europe": ("europe", "continental europe", "l europe"),
    "European Economic Area": (
        "eea",
        "european economic area",
        "espace economique europeen",
        "eee",
    ),
    "Schengen Area": (
        "schengen",
        "schengen area",
        "schengen zone",
        "espace schengen",
        "zone schengen",
    ),
    "EFTA": ("efta", "european free trade association", "aele"),
    "United States": (
        "united states",
        "united states of america",
        "usa",
        "u s a",
        "america",
        "etats unis",
        "etats unis d amerique",
    ),
    "United Kingdom": (
        "united kingdom",
        "uk",
        "u k",
        "great britain",
        "britain",
        "england",
        "royaume uni",
        "angleterre",
        "grande bretagne",
    ),
    **EU_MEMBERS,
    "Switzerland": ("switzerland", "suisse"),
    "Norway": ("norway", "norvege"),
    "Canada": ("canada",),
    "Singapore": ("singapore", "singapour"),
    "India": ("india", "inde"),
    "Japan": ("japan", "japon"),
    "Australia": ("australia", "australie"),
    "South Korea": ("south korea", "korea", "coree du sud", "coree"),
    "Israel": ("israel",),
    "United Arab Emirates": ("united arab emirates", "uae", "emirats arabes unis"),
    "China": ("china", "chine"),
}
_COUNTRY_OF = {phrase: name for name, phrases in COUNTRIES.items() for phrase in phrases}


def _alternation(phrases: Any) -> str:
    return "|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True))


# The posting's own country, as a question names it.
_HERE = (
    r"this country|the country|ce pays|le pays du poste"
    r"|the country (?:where|in which) (?:this|the) (?:job|role|position) is (?:located|based)"
)
C_SLOT = rf"(?:the |l |le |la |les )?(?P<country>{_HERE}|{_alternation(_COUNTRY_OF)})"


def _country_key(name: str) -> str:
    """The country a profile entry or a posting names: "the EU", "UK", "Royaume-Uni" each read
    as their table name; anything else as its folded text."""
    text = normalise(name)
    text = re.sub(r"^(?:the|l|le|la|les) ", "", text)
    if text in _COUNTRY_OF:
        return _COUNTRY_OF[text]
    found = {
        country
        for phrase, country in _COUNTRY_OF.items()
        if re.search(rf"\b{re.escape(phrase)}\b", text)
    }
    return found.pop() if len(found) == 1 else text


_PLACE_COUNTRY = {fold(city): country for city, country in CITY_COUNTRY.items()}
P_SLOT = rf"(?:the |l |le |la )?(?P<place>{_alternation([*_PLACE_COUNTRY, *_COUNTRY_OF])})"

# The languages a question can name, as normalised phrases, English and French.
LANGUAGES: dict[str, tuple[str, ...]] = {
    "english": ("english", "anglais"),
    "french": ("french", "francais"),
    "german": ("german", "allemand"),
    "spanish": ("spanish", "espagnol", "castillan"),
    "italian": ("italian", "italien"),
    "portuguese": ("portuguese", "portugais"),
    "dutch": ("dutch", "neerlandais"),
    "mandarin": ("mandarin", "mandarin chinese"),
    "chinese": ("chinese", "chinois"),
    "japanese": ("japanese", "japonais"),
    "arabic": ("arabic", "arabe"),
    "russian": ("russian", "russe"),
    "polish": ("polish", "polonais"),
    "korean": ("korean", "coreen"),
    "hindi": ("hindi",),
    "swedish": ("swedish", "suedois"),
    "danish": ("danish", "danois"),
    "norwegian": ("norwegian", "norvegien"),
    "finnish": ("finnish", "finnois"),
    "greek": ("greek", "grec"),
    "turkish": ("turkish", "turc"),
    "romanian": ("romanian", "roumain"),
    "czech": ("czech", "tcheque"),
    "hebrew": ("hebrew", "hebreu"),
    "catalan": ("catalan",),
    "ukrainian": ("ukrainian", "ukrainien"),
}
_LANGUAGE_OF = {phrase: name for name, phrases in LANGUAGES.items() for phrase in phrases}
L_SLOT = rf"(?:l )?(?P<language>{_alternation(_LANGUAGE_OF)})"


def _language_key(name: str) -> str:
    text = re.sub(r"\s*\(.*\)\s*$", "", fold(name)).strip()
    return _LANGUAGE_OF.get(normalise(text), normalise(text))


_CURRENCY = (
    r"(?P<currency>eur|euros?|€|usd|us dollars?|dollars?|\$|gbp|pounds?|£|chf|cad|sgd|aud|sek"
    r"|dkk|pln|jpy|inr)"
)
_N = r"(\d+(?:[.,]\d+)?)"
_CMP = (
    r"no more than|not more than|pas plus de|at most|up to|au plus|au maximum|maximum of|maximum"
    r"|less than|fewer than|under|below|moins de|more than|over|greater than|plus de|at least"
    r"|a minimum of|minimum of|minimum|au moins|au minimum|un minimum de"
)
Y_SLOT = (
    rf"(?P<years>(?:(?:{_CMP}) )?\d+(?:[.,]\d+)?(?: ?\+)?"
    r"(?: (?:or more|or above|or less|or fewer|ou plus|ou moins|et plus))? (?:years?|yrs|ans?)"
    r"(?: (?:or more|or longer|or less|or fewer|ou plus|ou moins|et plus|minimum|maximum))?)"
)


# --- answering helpers ---------------------------------------------------------------------


@dataclass(frozen=True)
class Sections:
    identity: IdentitySection
    experience: ExperienceSection
    skills: SkillsSection
    answers: AnswersSection


Handler = Callable[[FormField, Context, Sections, re.Match[str]], Answer | Left]


def _left(field: FormField, reason: str) -> Left:
    return Left(reason, field.options, field.required)


def _boolean_form(field: FormField, words: Words | None = None) -> bool:
    known = YES_OPTIONS | NO_OPTIONS | (words[True] | words[False] if words else frozenset())
    return field.kind in ("yes_no", "checkbox") or (
        field.kind in _CHOICES and any(option_key(o) in known for o in field.options)
    )


def _identity_value(attr: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        value = getattr(s.identity, attr)
        return _text(field, value, f"identity.{attr}") if value else _left(field, f"no_{attr}")

    return handler


def _name_part(part: Literal["first", "last", "full"]) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
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


def _answers_value(attr: str, reason: str, free: bool = False) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        value = getattr(s.answers, attr)
        return _text(field, value, f"answers.{attr}", free) if value else _left(field, reason)

    return handler


def _phone_code(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    """The calling code of the person's phone ("+33 6 ..." gives +33), as the option that holds
    it, or as text."""
    code = re.match(r"^\+(\d{1,3})(?:[\s.\-()]|$)", (s.identity.phone or "").strip())
    if not code:
        return _left(field, "no_phone_country_code")
    if field.kind in _CHOICES and field.options:
        hits = [o for o in field.options if re.search(rf"\+\s?{code.group(1)}(?!\d)", o)]
        if len(hits) != 1:
            return _left(field, "no_matching_option")
        return Answer(hits[0], "identity.phone", option=hits[0])
    return _text(field, f"+{code.group(1)}", "identity.phone")


def _location(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    parts = [p for p in (s.identity.city, s.identity.country) if p]
    if not parts:
        return _left(field, "no_location")
    return _text(field, ", ".join(parts), "identity.city")


def _link(kind: Literal["linkedin", "github", "website"]) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        ident = s.identity
        if kind == "linkedin":
            url = ident.linkedin_url or next(
                (k.url for k in ident.links if "linkedin" in k.url), None
            )
            source = "identity.linkedin_url"
        elif kind == "github":
            url = next((k.url for k in ident.links if "github" in k.url.lower()), None)
            source = "identity.links"
        else:
            url, source = ident.website, "identity.website"
        if field.kind not in ("text", "textarea"):
            return _left(field, "not_a_text_field")  # a file field never takes a link
        return Answer(url, source) if url else _left(field, "no_link")

    return handler


# A level too low to say the person speaks the language; a level that says they are fluent.
_BEGINNER = re.compile(
    r"\ba1\b|\ba2\b|beginner|basic|elementary|limited|notions?\b|debutant|elementaire|scolaire"
)
_FLUENT = re.compile(
    r"native|mother tongue|maternelle|bilingu|fluent|courant|\bc1\b|\bc2\b|full professional"
)


def _spoken(s: Sections) -> list[Any]:
    return [lang for lang in s.identity.languages if not _BEGINNER.search(fold(lang.level))]


# A language level, as a CEFR grade or a word: the words of each band, English and French. An
# option naming a CEFR grade is chosen only at the person's own grade; an option naming a word
# only at the person's own band (a stored C1 is "fluent", never "native", never "C2").
CEFR_BAND = {
    "a1": "basic",
    "a2": "basic",
    "b1": "intermediate",
    "b2": "intermediate",
    "c1": "fluent",
    "c2": "fluent",
}
LEVEL_WORDS: dict[str, tuple[str, ...]] = {
    "basic": (
        "basic",
        "beginner",
        "elementary",
        "limited",
        "notions",
        "debutant",
        "elementaire",
        "scolaire",
        "basique",
        "basic user",
    ),
    "intermediate": ("intermediate", "conversational", "intermediaire", "independent user"),
    "fluent": (
        "fluent",
        "advanced",
        "full professional",
        "courant",
        "avance",
        "courante",
        "business fluent",
        "proficient user",
    ),
    # These labels state their own band, without establishing a CEFR grade or fluency.
    "professional": ("professional", "professionnel", "professionnelle"),
    "professional_working": ("professional working",),
    "limited_working": ("limited working",),
    "business": ("business",),
    "native": (
        "native",
        "mother tongue",
        "native or bilingual",
        "bilingual",
        "langue maternelle",
        "maternelle",
        "bilingue",
    ),
}
_BAND_OF = {option_key(word): band for band, words in LEVEL_WORDS.items() for word in words}
_LEVEL_FILLER = re.compile(r"\b(?:proficiency|level|niveau|speaker|cefr|cecrl)\b")


def language_level(text: str | None) -> tuple[str | None, str] | None:
    """A level as (CEFR grade or None, band): "C1" reads ("c1", "fluent"), "Advanced (C1)"
    ("c1", "fluent"), "Langue maternelle" (None, "native"). None when it is not a level or its
    words disagree ("Basic (C2)")."""
    words = _LEVEL_FILLER.sub(" ", option_key(text))
    grades = re.findall(r"\b[abc][12]\b", words)
    rest = re.sub(r"\s+", " ", re.sub(r"\b[abc][12]\b", " ", words)).strip()
    if len(grades) > 1 or (not grades and not rest):
        return None
    grade = grades[0] if grades else None
    band = _BAND_OF.get(rest) if rest else CEFR_BAND[grade]  # type: ignore[index]
    if band is None or (grade is not None and CEFR_BAND[grade] != band):
        return None
    return grade, band


def _level_holds(
    option: tuple[str | None, str] | None, person: tuple[str | None, str] | None
) -> bool:
    if option is None or person is None:
        return False
    return option[0] == person[0] if option[0] is not None else option[1] == person[1]


_LANGUAGE_PHRASES = sorted(_LANGUAGE_OF, key=len, reverse=True)


def _language_option(option: str) -> tuple[str, str] | None:
    """An option read as (language, the level text after it): "English (C1)" reads ("english",
    "c1"), "Français" ("french", ""), "French (Français)" ("french", ""). None when it does not
    open with a language."""
    key = option_key(option)
    for phrase in _LANGUAGE_PHRASES:
        if key == phrase or key.startswith(f"{phrase} "):
            language, rest = _LANGUAGE_OF[phrase], key[len(phrase) :].strip()
            if _LANGUAGE_OF.get(rest) == language:
                rest = ""  # the language named twice, in two languages
            return language, rest
    return None


def _language_picks(options: tuple[str, ...], spoken: list[Any]) -> list[str] | None:
    """The options for the languages the person speaks, each at the person's level when the
    option states one. None when a spoken language the options name has no option, or more than
    one, at the person's level: never two levels of one language, never a level not held."""
    levels = {_language_key(lang.name): language_level(lang.level) for lang in spoken}
    named: dict[str, list[str]] = {}
    for option in options:
        read = _language_option(option)
        if read is None or read[0] not in levels:
            continue
        language, level = read
        chosen = named.setdefault(language, [])
        if not level or _level_holds(language_level(level), levels[language]):
            chosen.append(option)
    if any(len(chosen) != 1 for chosen in named.values()):
        return None
    return [o for o in options if any(o in chosen for chosen in named.values())]


def _languages(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    """The languages the person speaks: every one of them in a multi select, each at its level
    when the options state levels; the one option a single choice can hold only when the person
    speaks one; the stored list as text."""
    spoken = _spoken(s)
    if not spoken:
        return _left(field, "no_languages")
    if field.kind in _CHOICES and field.options and field.kind != "yes_no":
        picked = _language_picks(field.options, spoken)
        if field.kind == "multi":
            if not picked:
                return _left(field, "no_matching_option")
            return Answer(tuple(picked), "identity.languages", option=picked[0])
        if len(spoken) != 1 or picked is None or len(picked) != 1:
            return _left(field, "several_languages_one_choice")
        return Answer(picked[0], "identity.languages", option=picked[0])
    if field.kind in ("yes_no", "checkbox", "file"):
        return _left(field, "not_a_yes_or_no")
    listed = ", ".join(
        lang.name + (f" ({lang.level})" if lang.level else "") for lang in s.identity.languages
    )
    return Answer(listed, "identity.languages")


def _language_level(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    """The person's level in the language named: the stored level as text, the one option at
    that level for a choice (a grade option only at the stored grade)."""
    want = _LANGUAGE_OF[m.group("language")]
    lang = next((g for g in s.identity.languages if _language_key(g.name) == want), None)
    if lang is None:
        return _left(field, "language_not_in_profile")
    if not lang.level:
        return _left(field, "no_language_level")
    if field.kind in _CHOICES and field.options and field.kind != "yes_no":
        person = language_level(lang.level)
        hits = []
        for option in field.options:
            read = _language_option(option)
            level = read[1] if read is not None and read[0] == want else option
            if _level_holds(language_level(level), person):
                hits.append(option)
        if len(hits) != 1:
            return _left(field, "no_matching_option")
        return Answer(
            (hits[0],) if field.kind == "multi" else hits[0], "identity.languages", hits[0]
        )
    if field.kind not in ("text", "textarea"):
        return _left(field, "not_a_yes_or_no")
    return Answer(lang.level, "identity.languages")


def _speaks(fluent: bool) -> Handler:
    """Do you speak X (any level above a beginner's), are you fluent in X (a fluent level): a
    yes when the profile says so, else left; a language missing from the profile is no "No"."""

    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        if not _boolean_form(field):
            return _left(field, "not_a_text_question")
        want = _LANGUAGE_OF[m.group("language")]
        for lang in _spoken(s):
            if _language_key(lang.name) == want:
                if fluent and not _FLUENT.search(fold(lang.level)):
                    return _left(field, "fluency_not_stated")
                return _boolean(field, True, "identity.languages")
        return _left(field, "language_not_in_profile")

    return handler


def _entries(
    field: FormField, ctx: Context, s: Sections, named: str | None
) -> tuple[list[WorkAuthorization], Left | None]:
    """The person's entries for the country the question names, else for the posting's. A
    member state of the EU with no entry of its own is answered from the person's EU entry."""
    if named is not None and not re.fullmatch(_HERE, named):
        countries: tuple[str, ...] = (_COUNTRY_OF[named],)
    elif ctx.posting is None or not ctx.posting.countries:
        return [], _left(field, "no_posting_country")
    else:
        countries = ctx.posting.countries
    by_country = {_country_key(w.country): w for w in s.answers.work_authorization}
    entries = []
    for country in countries:
        key = _country_key(country)
        entry = by_country.get(key)
        if entry is None and key in EU_MEMBERS:
            entry = by_country.get(EU)
        if entry is None:
            return [], _left(field, "country_not_in_work_authorization")
        entries.append(entry)
    return entries, None


def _authorisation_option(field: FormField, entries: list[WorkAuthorization]) -> Answer | Left:
    """The one status option (`AUTHORISATION_OPTIONS`) whose category is true for the person in
    the country asked: "authorised" when the entries need no sponsorship, "requires_sponsorship"
    when they do. A citizenship, residence or visa option is never true: the profile stores none.
    A status option reads the same whatever the label asks, so it is chosen on its own truth.
    The profile keeps no dates: an entry that needs sponsorship reads as not authorised there,
    also for a person authorised today who needs sponsorship later."""
    needs = {e.needs_sponsorship for e in entries}
    if len(needs) != 1 or None in needs:
        return _left(field, "countries_disagree_or_unstated")
    true = {"requires_sponsorship", "not_authorised"} if needs.pop() else {"authorised"}
    hits = [o for o in field.options if AUTHORISATION_OPTIONS.get(option_key(o)) in true]
    if len(hits) != 1:
        return _left(field, "no_matching_option")
    picked = hits[0]
    return Answer(
        (picked,) if field.kind == "multi" else picked, "answers.work_authorization", picked
    )


def _work(kind: Literal["authorised", "needs", "status"]) -> Handler:
    """Work authorisation ("may you work there"), sponsorship or a work permit ("do you need
    it", which also takes "Required" and "Not required") and the status sentence, for the
    country the question names, else for the posting's."""
    words = NEED_OPTIONS if kind == "needs" else None

    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        entries, problem = _entries(field, ctx, s, m.groupdict().get("country"))
        if problem:
            return problem
        if _boolean_form(field, words):
            needs = {e.needs_sponsorship for e in entries}
            if kind == "status" or len(needs) != 1 or None in needs:
                return _left(field, "countries_disagree_or_unstated")
            needed = bool(needs.pop())
            said = _boolean(
                field,
                needed if kind == "needs" else not needed,
                "answers.work_authorization",
                words,
            )
            if isinstance(said, Left) and field.options:
                # "Oui, je suis autorisé(e)" beside "Non": a status option, true on its own.
                status = _authorisation_option(field, entries)
                return status if isinstance(status, Answer) else said
            return said
        if field.kind in _CHOICES and field.options:
            return _authorisation_option(field, entries)
        answers = {e.answer for e in entries}
        if len(answers) != 1:
            return _left(field, "countries_disagree_or_unstated")
        return _text(field, answers.pop(), "answers.work_authorization")

    return handler


def _start_date(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    value = s.answers.earliest_start
    if not value:
        return _left(field, "no_earliest_start")
    if field.kind == "date" and not _ISO_DATE.match(value):
        return _left(field, "start_is_not_a_date")
    return _text(field, value, "answers.earliest_start", free=True)


# The words a relocation sentence may hold besides the places it names. A sentence is read for
# a yes or no only when every other word is one of these: "Yes, within France only." names
# France, "only" makes every other country a no; "Open to Paris or Lyon." names two cities;
# "Yes, anywhere." covers every place. Anything else ("but", "except", "maybe", a condition) is
# not read, and a bare "Yes" names no place, so it covers none.
_RELOCATION_WORDS = frozenset(
    [
        "yes",
        "oui",
        "open",
        "to",
        "relocate",
        "relocating",
        "relocation",
        "willing",
        "happy",
        "ready",
        "within",
        "in",
        "inside",
        "and",
        "or",
        "et",
        "a",
        "en",
        "au",
        "aux",
        "dans",
        "the",
        "le",
        "la",
        "les",
        "l",
        "i",
        "am",
        "je",
        "suis",
        "would",
        "be",
        "pret",
        "prete",
        "ouvert",
        "ouverte",
        "demenager",
        "mobile",
        "only",
        "uniquement",
        "seulement",
        "exclusively",
    ]
)
_ANYWHERE = re.compile(r"\b(?:anywhere|partout|worldwide|any location|n importe ou)\b")
_RELOCATION_ONLY = re.compile(r"\b(?:only|uniquement|seulement|exclusively)\b")
_PLACE_PHRASES = re.compile(rf"\b(?:{_alternation([*_PLACE_COUNTRY, *_COUNTRY_OF])})\b")


def _relocation_covers(sentence: str, posting: Posting) -> bool | None:
    """Whether the person's relocation sentence covers the posting's place: True when it says
    anywhere or names the posting's city or country (or the EU for a member state), False only
    for a bare "No" or a sentence saying "only" that names places elsewhere, else None."""
    key = option_key(sentence)
    if key in NO_OPTIONS:
        return False
    anywhere = _ANYWHERE.search(key) is not None
    places = _PLACE_PHRASES.findall(key)
    rest = _PLACE_PHRASES.sub(" ", _ANYWHERE.sub(" ", key)).split()
    if not (anywhere or places) or any(word not in _RELOCATION_WORDS for word in rest):
        return None
    cities = {p for p in places if p in _PLACE_COUNTRY}
    countries = {_COUNTRY_OF[p] for p in places if p not in _PLACE_COUNTRY}
    only = _RELOCATION_ONLY.search(key) is not None

    def covered(city: str | None, country: str) -> bool | None:
        if anywhere or city in cities or country in countries:
            return True
        if EU in countries and country in EU_MEMBERS:
            return True
        if not only or (city is None and country in {_PLACE_COUNTRY[c] for c in cities}):
            return None  # a city named in the posting's country: the posting may be there
        return False

    targets = [(c, _PLACE_COUNTRY[c]) for c in posting.cities if c in _PLACE_COUNTRY] or [
        (None, c) for c in posting.countries
    ]
    verdicts = {covered(city, country) for city, country in targets}
    return verdicts.pop() if len(verdicts) == 1 else None


def _relocation(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    """The person's relocation sentence as text; a yes or no only for a known posting the
    sentence explicitly covers or excludes (`_relocation_covers`). A question naming a place
    answers only for the posting's own city or country."""
    place = m.groupdict().get("place")
    posting = ctx.posting
    if place is not None:
        country = _PLACE_COUNTRY.get(place) or _COUNTRY_OF.get(place)
        here = posting is not None and (
            place in posting.cities if place in _PLACE_COUNTRY else country in posting.countries
        )
        if not here:
            return _left(field, "not_the_posting_location")
    sentence = s.answers.relocation
    if not sentence:
        return _left(field, "no_relocation")
    if not _boolean_form(field):
        return _text(field, sentence, "answers.relocation", free=True)
    if posting is None or not (posting.cities or posting.countries):
        return _left(field, "no_posting_location")
    verdict = _relocation_covers(sentence, posting)
    if verdict is None:
        return _left(field, "relocation_does_not_cover_the_posting")
    return _boolean(field, verdict, "answers.relocation")


def _flag(attr: str, words: Words | None = None) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        value = getattr(s.answers, attr)
        if value is None:
            return _left(field, f"no_{attr}")
        if field.kind in ("text", "textarea"):
            return _left(field, "not_a_yes_or_no")
        return _boolean(field, value, f"answers.{attr}", words)

    return handler


# A currency the label names: a range in another currency is never given for it.
_CURRENCY_SIGNS = (
    (r"€|\beuros?\b", "EUR"),
    (r"£|\bpounds?\b", "GBP"),
    (r"(?<![a-z])\$|\bdollars?\b", "USD"),
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


def _salary(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    a = s.answers
    stance = a.salary_stance_of
    if stance == "leave_blank":
        return _left(field, "salary_leave_blank")
    if stance == "sentence":
        if not a.salary_policy:
            return _left(field, "no_salary_sentence")
        return _text(field, a.salary_policy, "answers.salary_policy", free=True)
    text = m.string
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
    ("gender", r"\bgender\b|\bsex\b|\bsexe\b|\bgenre\b"),
    ("ethnicity", r"ethnic|origine"),
    ("race", r"\brace\b|racial"),
    ("disability", r"disabilit|handicap"),
    ("veteran", r"veteran|military|ancien combattant"),
    ("age", r"\bage\b|date of birth|birth ?date|date de naissance|\bbirthday\b"),
)


def eeo_topic(text: str) -> str | None:
    """The EEO topic a label touches anywhere, for the policy stage: such a label never reads
    the bank. Only a label that fully matches an EEO template is answered."""
    for topic, pattern in _EEO_TOPICS:
        if re.search(pattern, text):
            return topic
    return None


def _eeo(topic: str) -> Handler:
    def handler(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
        policy = s.answers.eeo_policy
        if policy == "leave":
            return _left(field, "eeo_policy_leave")
        if policy == "stored":
            stored = next((e.answer for e in s.answers.eeo_answers if e.topic == topic), None)
            if stored is None:
                return _left(field, "eeo_not_stored")
            words = EEO_YES_NO_OPTION_TEXTS.get(topic, {})
            said = option_key(stored)
            if words and field.options and (said in YES_OPTIONS or said in NO_OPTIONS):
                listed = {option_key(t) for t in words[said in YES_OPTIONS]}
                hits = [o for o in field.options if option_key(o) in listed]
                if len(hits) == 1 and match_option(stored, field.options) is None:
                    source = f"answers.eeo_answers.{topic}"
                    return Answer((hits[0],) if field.kind == "multi" else hits[0], source, hits[0])
            return _text(field, stored, f"answers.eeo_answers.{topic}")
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


# Each comparison of a years slot, its operator; the first that matches decides.
_COMPARISONS: tuple[tuple[str, str], ...] = (
    (
        rf"\b(?:no more than|not more than|pas plus de|at most|up to|au plus|au maximum|maximum(?: of)?)\s+{_N}",
        "le",
    ),
    (rf"\b(?:less than|fewer than|under|below|moins de)\s+{_N}", "lt"),
    (rf"\b(?:more than|over|greater than|plus de)\s+{_N}", "gt"),
    (
        rf"\b(?:at least|a minimum of|minimum(?: of)?|au moins|au minimum|un minimum de)\s+{_N}",
        "ge",
    ),
    (rf"{_N}\s*\+", "ge"),
    (rf"{_N}\s+(?:or more|or above|ou plus|et plus)\b", "ge"),
    (rf"{_N}\s+(?:or less|or fewer|ou moins)\b", "le"),
    (rf"{_N}\s+(?:years?|yrs|ans?)\s+(?:or more|or longer|ou plus|et plus|minimum)\b", "ge"),
    (rf"{_N}\s+(?:years?|yrs|ans?)\s+(?:or less|or fewer|ou moins|maximum)\b", "le"),
    (rf"{_N}\s*(?:years?|yrs|ans?)\b", "ge"),  # "Do you have 5 years of experience?"
)


def _comparison(slot: str) -> tuple[str, float]:
    for pattern, op in _COMPARISONS:
        found = re.search(pattern, slot)
        if found:
            return op, float(found.group(1).replace(",", "."))
    raise ValueError(slot)  # the years slot always holds one of the forms above


def _holds(op: str, low: float, high: float, n: float) -> bool | None:
    """Whether the person's span satisfies `op n`: True, False, or None when the span straddles."""

    def test(y: float) -> bool:
        return {"ge": y >= n, "gt": y > n, "lt": y < n, "le": y <= n}[op]

    first, last = test(low), test(high)
    return first if first == last else None


_YEAR_WORDS = r"(?<![a-z])(?:years?|yrs?|ans|annees?)\b|\d\s*an\b"
_MONTH_WORDS = r"(?<![a-z])(?:months?|mos?|mois)\b"
_OTHER_WORDS = r"(?<![a-z])(?:weeks?|wks?|days?|semaines?|jours?)\b"


def _unit(text: str) -> str | None:
    """The time unit a text names: "y", "m", "x" for weeks or days or two units at once, or None."""
    found = {
        unit
        for unit, pattern in (("y", _YEAR_WORDS), ("m", _MONTH_WORDS), ("x", _OTHER_WORDS))
        if re.search(pattern, text)
    }
    if len(found) > 1:
        return "x"
    return found.pop() if found else None


def _option_span(option: str, unit: str) -> tuple[float, float] | None:
    """The years an option covers, as [low, high), or None when it names no readable span.
    `unit` ("y" or "m") is what a bare number means; a unit written in the option wins."""
    text = fold(option)
    own = _unit(text) or unit
    if own == "x":
        return None
    scale = 12.0 if own == "m" else 1.0
    numbers = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text)]
    if len(numbers) == 2 and numbers[0] <= numbers[1]:
        low, high = numbers[0], numbers[1] + 1
    elif len(numbers) != 1:
        return None
    else:
        n = numbers[0]
        if re.search(r"less than|fewer than|under|below|moins de|inferieur|<", text):
            low, high = 0.0, n
        elif re.search(r"up to|at most|or less|ou moins|jusqu|maximum|max\b", text):
            low, high = 0.0, n + 1
        elif re.search(r"more than|over|plus de|superieur|>", text):
            low, high = n + 1e-9, float("inf")
        elif re.search(r"\+|or more|ou plus|et plus|at least|au moins|minimum", text):
            low, high = n, float("inf")
        else:
            low, high = n, n + 1
    return low / scale, high / scale


# What an option may say besides its numbers and its time unit to still be a time span (a level
# word such as Junior is fine). Anything else (a currency, k or M, people, employees, a percent sign, any other noun) is not one.
_SPAN_WORDS = re.compile(
    r"(?<![a-z])(?:less than|fewer than|under|below|moins de|inferieur(?:e)?(?: a)?|up to|at most"
    r"|or less|ou moins|jusqu'?(?: a)?|maximum|max|more than|over|plus de|superieur(?:e)?(?: a)?"
    r"|or more|ou plus|et plus|and more|and over|at least|au moins|minimum|min|between|entre|from"
    r"|to|and|et|de|d|a|of experience|d'experience|experience|years?|yrs?|ans?|annees?"
    r"|months?|mos?|mois"
    r"|junior|mid|senior|entry|level|intermediate|intermediaire|expert|beginner|debutant|confirme)"
    r"(?![a-z])"
)


def _is_time_span(option: str) -> bool:
    """Whether an option with a number speaks of nothing but a count of years or months."""
    rest = _SPAN_WORDS.sub(" ", re.sub(r"\d+(?:[.,]\d+)?", " ", fold(option)))
    return not re.sub(r"[\s\-\u2010-\u2015+<>\u2264\u2265.,/()'~]", "", rest)


def _range_option(options: tuple[str, ...], low: float, high: float, label: str) -> str | None:
    """The one option whose span holds the person's whole span. A numbered option counts only if
    it is a time span: it names years or months itself, or it is a bare number or range and the
    label names years and every numbered option is bare in the same way. A currency, a count of
    people, a percent, weeks, days, two units at once, or one bare option among named ones leave
    the whole list unread."""
    numbered = [o for o in options if re.search(r"\d", o)]
    if not all(_is_time_span(o) for o in numbered):
        return None
    named = [_unit(fold(o)) for o in numbered]
    if "x" in named or _unit(fold(label)) == "x":
        return None
    if None in named and (any(n is not None for n in named) or _unit(fold(label)) != "y"):
        return None
    hits = []
    for option in options:
        span = _option_span(option, "y")
        if span is not None and span[0] <= low and high < span[1] + (1e-9 if low != high else 0):
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _experience_years(
    field: FormField, ctx: Context, s: Sections, m: re.Match[str]
) -> Answer | Left:
    """Overall years of experience: a number or a range option for "how many", a yes or no for
    a threshold read with its operator; a span that straddles the threshold is left."""
    slot = m.groupdict().get("years")
    years = _years(s)
    if slot is not None:
        if not _boolean_form(field):
            return _left(field, "not_a_yes_or_no")
        if years is None:
            return _left(field, "years_not_stated")
        op, n = _comparison(slot)
        held = _holds(op, years[0], years[1], n)
        if held is None:
            return _left(field, "years_too_close_to_call")
        return _boolean(field, held, "skills.years_experience")
    if field.kind in ("yes_no", "checkbox"):
        return _left(field, "no_threshold")
    if years is None:
        return _left(field, "years_not_stated")
    if field.kind in _CHOICES and field.options:
        picked = _range_option(field.options, *years, field.label)
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


def degree_of(text: str | None) -> tuple[str, int, str] | None:
    """A degree as (level, rank, name), from its whole text: "MSc" reads ("master", 0, "MSc"),
    "Master's" ("master", 1, "master"), "Licence (Bac+3)" ("bachelor", 0, "Licence"). None when
    it is not a degree with a recognised academic field, or its two names disagree. Ambiguous
    MS and engineer qualifications establish no equivalence. Completion qualifiers stay unknown.
    """
    whole = _DEGREE_OF.get(option_key(text))
    if whole is None and (field := _degree_field(text)) is not None:
        return field[0]
    named = re.fullmatch(r"([^()]+)\(([^()]+)\)", fold(text))
    if whole is not None or named is None:
        return whole
    first, second = (_DEGREE_OF.get(option_key(part)) for part in named.groups())
    if first is None or second is None or first[0] != second[0]:
        return None
    if first[1] == second[1] and first[2] != second[2]:
        return None
    return min(first, second, key=lambda read: read[1])


# Field names are positive academic topics, rather than accepting every suffix after a degree
# ("MSc expected" and "Master 2 student" must not establish a completed qualification).
_DEGREE_FIELDS = frozenset(
    {
        "computer science",
        "computing",
        "informatics",
        "informatique",
        "mathematics",
        "mathematiques",
        "physics",
        "physique",
        "chemistry",
        "chimie",
        "biology",
        "biologie",
        "mechanical engineering",
        "electrical engineering",
        "software engineering",
        "civil engineering",
        "data science",
        "economics",
        "economie",
        "history",
        "histoire",
        "business administration",
        "psychology",
        "psychologie",
    }
)


def _degree_field(text: str | None) -> tuple[tuple[str, int, str], str] | None:
    key = option_key(text)
    for name in sorted(_DEGREE_OF, key=len, reverse=True):
        read = _DEGREE_OF[name]
        if read[1] == 2 or not key.startswith(name + " "):
            continue
        field = key[len(name) + 1 :]
        field = field.removeprefix("in ").removeprefix("en ")
        if field in _DEGREE_FIELDS:
            return read, field
    return None


def _degree_option(stored: str, options: tuple[str, ...]) -> str | None:
    """The option for a stored degree: the one that reads the same, else the one of its own
    rank and name, else the one naming its level (`DEGREE_TEXTS`); one of each, or none."""
    picked = match_option(stored, options)
    have = degree_of(stored)
    if picked is not None or have is None:
        return picked
    level, rank, name = have
    # An option with a field is narrower than the stored degree. Only the exact match above
    # can select it; sharing a degree title does not establish another academic discipline.
    read = [(o, degree_of(o)) for o in options if _degree_field(o) is None]
    for wanted in range(rank, 2):
        hits = [
            o
            for o, r in read
            if r is not None
            and r[0] == level
            and r[1] == wanted
            and (wanted > rank or r[2] == name)
        ]
        if hits:
            return hits[0] if len(hits) == 1 else None
    return None


def _education(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    degree, source = s.answers.highest_degree, "answers.highest_degree"
    if not degree:
        school = _latest_school(s)
        degree, source = (school.degree, "experience.education") if school else (None, "")
    if not degree:
        return _left(field, "no_degree")
    if field.kind in _CHOICES and field.options:
        picked = _degree_option(degree, field.options)
        if picked is None:
            return _left(field, "no_matching_option")
        return Answer((picked,) if field.kind == "multi" else picked, source, picked)
    return _text(field, degree, source)


def _graduation_year(
    field: FormField, ctx: Context, s: Sections, m: re.Match[str]
) -> Answer | Left:
    if s.answers.highest_degree_year:
        return _text(field, str(s.answers.highest_degree_year), "answers.highest_degree_year")
    school = _latest_school(s)
    year = re.match(r"\d{4}", school.end or "") if school else None
    if year:
        return _text(field, year.group(), "experience.education")
    return _left(field, "no_graduation_year")


def _school(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    school = _latest_school(s)
    return (
        _text(field, school.school, "experience.education") if school else _left(field, "no_school")
    )


def _heard(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    heard = s.answers.heard_about
    if not heard:
        return _left(field, "no_heard_about")
    if field.kind in _CHOICES and field.options:
        # The option that reads as the stored source; never "Other" by default, since the
        # person's source may be one of the options under another wording.
        picked = match_option(heard, field.options)
        if picked is None:
            return _left(field, "no_matching_option")
        return Answer((picked,) if field.kind == "multi" else picked, "answers.heard_about", picked)
    return _text(field, heard, "answers.heard_about")


def _previously_applied(
    field: FormField, ctx: Context, s: Sections, m: re.Match[str]
) -> Answer | Left:
    if ctx.applied_before:
        return _boolean(field, True, "applications")
    default = s.answers.previously_applied_default
    if default is None:
        return _left(field, "no_previously_applied_default")
    return _boolean(field, default, "answers.previously_applied_default")


def _document(field: FormField, ctx: Context, s: Sections, m: re.Match[str]) -> Answer | Left:
    docs = ctx.documents
    letter = bool(re.search(r"letter|lettre", m.string))
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


# --- the templates -------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    """One canonical question: anchored patterns over the normalised label, English and French,
    with slots (a country, a place, a language, a currency, a years comparison), and the rule
    that answers it. `negated` marks a template written for an exact negated form."""

    name: str
    patterns: tuple[str, ...]
    handler: Handler
    negated: bool = False


def _t(name: str, handler: Handler, *patterns: str, negated: bool = False) -> Template:
    expanded = tuple(
        p.replace("{C}", C_SLOT)
        .replace("{P}", P_SLOT)
        .replace("{L}", L_SLOT)
        .replace("{Y}", Y_SLOT)
        .replace("{CUR}", _CURRENCY)
        for p in patterns
    )
    return Template(name, expanded, handler, negated)


_YOUR = r"(?:(?:what is |what s )?your |votre |quel est votre |quelle est votre )?"
_IN = r"(?:in|en|au|aux|a|dans|pour)"
_EXPERIENCE = r"(?:(?:professional|work|working|total|overall|industry) )*experience"
_SPONSORSHIP = (
    r"(?:visa |work visa |employment visa |immigration |employer )?sponsorship"
    r"(?: for (?:an? )?(?:employment|work) visa(?: status)?| for employment visa status"
    r"| for employment| for (?:a |an )?work permit)?(?: e g h 1b(?: visa)?(?: status)?)?"
)
_SUBMIT = r"(?:have you |avez vous )"

TEMPLATES: tuple[Template, ...] = (
    _t(
        "first_name",
        _name_part("first"),
        rf"{_YOUR}(?:legal )?(?:first|given) name|forename|prenom",
    ),
    _t(
        "last_name",
        _name_part("last"),
        rf"{_YOUR}(?:legal )?(?:last|family) name|surname|{_YOUR}nom(?: de famille)?",
    ),
    _t(
        "full_name",
        _name_part("full"),
        rf"{_YOUR}(?:full |legal |full legal |complete )?name|first and last name"
        r"|nom complet|nom et prenom|prenom et nom|nom prenom|prenom nom",
    ),
    _t(
        "preferred_name",
        _answers_value("preferred_name", "no_preferred_name"),
        rf"{_YOUR}preferred (?:first )?name|nickname|prenom d usage|surnom",
    ),
    _t(
        "pronouns",
        _answers_value("pronouns", "no_pronouns"),
        rf"{_YOUR}(?:preferred )?pronouns|{_YOUR}pronoms",
    ),
    _t(
        "email",
        _identity_value("email"),
        rf"{_YOUR}(?:e mail|email|mail)(?: address)?"
        r"|(?:votre )?adresse (?:e mail|email|mail|electronique|courriel)|(?:votre )?courriel",
    ),
    _t(
        "phone_country_code",
        _phone_code,
        r"(?:phone |telephone |mobile )?country (?:calling )?code|(?:phone )?dial(?:l)?ing code"
        r"|indicatif(?: pays| telephonique| du pays| international)?|code pays(?: telephonique)?",
    ),
    _t(
        "phone",
        _identity_value("phone"),
        rf"{_YOUR}(?:mobile |cell |contact )?(?:phone|telephone)(?: number)?"
        rf"|{_YOUR}(?:mobile|cell)(?: phone)?(?: number)?|tel|{_YOUR}contact number"
        r"|(?:votre )?(?:(?:numero|num|no|n) de )?(?:telephone|portable|mobile)(?: portable| mobile)?",
    ),
    _t(
        "city",
        _identity_value("city"),
        rf"{_YOUR}(?:current )?city(?: of residence)?|location city|{_YOUR}ville(?: de residence| actuelle)?",
    ),
    _t(
        "location",
        _location,
        rf"{_YOUR}(?:current )?location|where are you (?:based|located)|where do you live"
        r"|localisation|lieu de residence|ou habitez vous|ou etes vous base",
    ),
    _t(
        "country_of_residence",
        _identity_value("country"),
        rf"{_YOUR}(?:current )?country(?: of residence)?|(?:in )?which country do you (?:live|reside) in"
        r"|(?:in )?which country do you (?:live|reside)|country you (?:live|reside) in"
        rf"|{_YOUR}pays(?: de residence)?(?: actuel)?|dans quel pays residez vous",
    ),
    _t(
        "linkedin",
        _link("linkedin"),
        rf"{_YOUR}linkedin(?: profile)?(?: url| link| page)?|(?:profil|lien|url|page) linkedin",
    ),
    _t("github", _link("github"), rf"{_YOUR}github(?: profile)?(?: url| link)?"),
    _t(
        "website",
        _link("website"),
        rf"{_YOUR}(?:personal )?(?:website|web site)(?: url| link)?"
        rf"|{_YOUR}personal (?:site|page)(?: url)?|{_YOUR}portfolio(?: website)?(?: url| link)?"
        r"|(?:votre )?site (?:web|internet)(?: personnel)?|(?:votre )?site personnel"
        r"|(?:portfolio|website) (?:/|or) (?:portfolio|website)(?: url| link)?"
        r"|website blog (?:/|or) portfolio",
    ),
    _t(
        "authorised_to_work",
        _work("authorised"),
        rf"(?:are you )?(?:legally |lawfully )?(?:authori[sz]ed|eligible|entitled|permitted) to work {_IN} {{C}}(?: for any employer)?",
        r"(?:are you )?legally able to work in {C}",
        r"do you have (?:the )?(?:legal )?right to work in {C}",
        r"do you have (?:valid )?(?:legal )?work authori[sz]ation (?:for|in) {C}",
        rf"(?:etes vous )?(?:legalement )?(?:autorise|eligible|habilite) a travailler {_IN} {{C}}",
        rf"avez vous le droit de travailler {_IN} {{C}}",
    ),
    _t(
        "authorised_without_sponsorship",
        _work("authorised"),
        r"(?:can you|are you (?:legally )?able to|are you (?:legally )?authori[sz]ed to"
        r"|do you have the right to|are you eligible to) work in {C} without"
        r" (?:needing |requiring |the need for )?(?:any |a |an )?(?:visa |work visa |employer |immigration )?sponsorship",
        rf"pouvez vous travailler {_IN} {{C}} sans (?:avoir besoin d un |besoin d un |besoin de )?"
        r"(?:parrainage|sponsoring)(?: de visa)?",
        negated=True,
    ),
    _t(
        "needs_sponsorship",
        _work("needs"),
        rf"(?:(?:will|would|do) you (?:now or in the future )?)?(?:require|need)s? {_SPONSORSHIP}"
        rf"(?: now or in the future)?(?: to work {_IN} {{C}})?(?: in the future)?",
        r"(?:will|would|do) you (?:now or in the future )?(?:require|need) (?:us |the company |an employer )?"
        r"to sponsor (?:a |an |your )?(?:work |employment )?visa(?: for you)?(?: to work in {C})?",
        r"(?:visa )?sponsorship (?:required|needed)(?: to work in {C})?|visa sponsorship",
        rf"(?:is {_SPONSORSHIP}|will {_SPONSORSHIP} be) (?:required|needed)"
        r"(?: to work in {C})?",
        rf"(?:avez|aurez) vous (?:maintenant ou a l avenir )?besoin (?:maintenant ou a l avenir )?d (?:un |une )?(?:parrainage|sponsoring)"
        rf"(?: de visa| pour un visa)?(?: maintenant ou a l avenir| maintenant ou dans le futur)?"
        rf"(?: pour travailler {_IN} {{C}})?",
        rf"(?:necessitez|necessiterez) vous un (?:parrainage|sponsoring)(?: de visa)?(?: pour travailler {_IN} {{C}})?",
    ),
    _t(
        "needs_work_permit",
        _work("needs"),
        r"(?:do|will|would) you (?:now or in the future )?(?:need|require) a work permit"
        r"(?: now or in the future| in the future)?(?: (?:to work )?(?:in|for) {C})?",
        rf"(?:avez|aurez) vous (?:maintenant ou a l avenir )?besoin d (?:un permis|une autorisation) de travail"
        rf"(?: maintenant ou a l avenir)?(?: (?:pour travailler )?{_IN} {{C}})?",
    ),
    _t(
        "work_authorisation_status",
        _work("status"),
        rf"{_YOUR}(?:work authori[sz]ation|visa status|immigration status)(?: status)?(?: (?:in|for) {{C}})?",
        rf"(?:votre )?autorisation de travail(?: {_IN} {{C}})?",
    ),
    _t(
        "notice_period",
        _answers_value("notice_period", "no_notice_period", free=True),
        rf"{_YOUR}(?:current )?notice period|when (?:can|could) you (?:start|join)|how soon (?:can|could) you (?:start|join)(?: us)?",
        r"(?:quel est votre |votre |duree de |delai de |duree du )?preavis",
    ),
    _t(
        "earliest_start",
        _start_date,
        rf"{_YOUR}(?:earliest |earliest possible |possible |expected )?start date"
        r"|when is your earliest start date|availability date|date (?:of availability|available)"
        r"|when are you available to start|available to start",
        rf"{_YOUR}date de (?:disponibilite|debut|demarrage|prise de poste)(?: souhaitee| possible)?"
        r"|disponibilite|a partir de quand etes vous disponible",
    ),
    _t(
        "relocation",
        _relocation,
        r"(?:are you |would you be )?(?:willing|open|ready) to relocate(?: to {P})?|willingness to relocate"
        r"|(?:are you |would you be )?(?:willing|open) to relocation",
        r"(?:etes vous )?(?:pret|ouvert|dispose)e? a demenager(?: (?:a|en|au|aux) {P})?"
        r"|mobilite geographique|etes vous mobile(?: geographiquement)?",
    ),
    _t(
        "travel",
        _answers_value("travel", "no_travel", free=True),
        r"(?:are you )?willing to travel|willingness to travel|travel requirements?",
        r"(?:etes vous )?(?:pret|dispose)e? a (?:vous deplacer|voyager)|deplacements",
    ),
    _t(
        "willing_onsite",
        _flag("willing_onsite"),
        r"(?:are you |would you be |i am )?(?:willing|open|able|comfortable|happy) "
        r"(?:to work|to working|working|with working) (?:on site|onsite|in (?:the )?office|in person"
        r"|from (?:the|our) office)",
        r"(?:etes vous )?(?:pret|ouvert|dispose)e? a travailler (?:sur site|en presentiel|au bureau)"
        r"|acceptez vous (?:de travailler|le travail) (?:sur site|en presentiel)",
    ),
    _t(
        "willing_hybrid",
        _flag("willing_hybrid"),
        r"(?:are you |would you be |i am )?(?:willing|open|able|comfortable|happy) "
        r"(?:to work in|to working in|working in|to) (?:an? )?hybrid"
        r"(?: (?:work|working|schedule|model|work model|working model|arrangement|setup|setting"
        r"|environment|role|basis))?",
        r"(?:etes vous )?(?:pret|ouvert|dispose)e? (?:a|au) (?:travailler en |un )?(?:mode |travail |rythme )?hybride",
    ),
    _t(
        "background_check",
        _flag("background_check_consent", CONSENT_OPTIONS),
        r"(?:do you |would you )?(?:consent|agree) to (?:a |undergo a )?background checks?"
        r"|background check consent|are you willing to undergo a background check",
        r"(?:acceptez vous|consentez vous a) (?:une |la )?verification des antecedents",
    ),
    _t(
        "reference_check",
        _flag("reference_check_consent", CONSENT_OPTIONS),
        r"(?:do you |would you )?(?:consent|agree) to (?:a )?reference checks?"
        r"|reference check consent|may we contact your references(?: reference check)?",
        r"acceptez vous que nous contactions vos references",
    ),
    _t(
        "expected_salary",
        _salary,
        r"(?:what (?:are|is) your |your )?(?:(?:annual|yearly|gross|base|total) )*"
        r"(?:salary expectations?|expected (?:(?:annual|yearly|gross|base) )*(?:salary|compensation|pay)"
        r"|desired (?:(?:annual|yearly|gross|base) )*(?:salary|compensation|pay)|salary requirements?"
        r"|compensation expectations?|pay expectations?)(?: (?:annual|gross|per year|per annum|annual gross))?"
        r"(?: (?:in )?{CUR})?",
        r"(?:vos |quelles sont vos )?(?:pretentions salariales|attentes salariales)"
        r"(?: brut(?:es)? annuel(?:les)?| annuel(?:les)?)?(?: (?:en )?{CUR})?",
        r"(?:salaire|remuneration) (?:souhaitee?|attendue?|envisagee?|demandee?)"
        r"(?: brut(?:e)? annuel(?:le)?| annuel(?:le)?)?(?: (?:en )?{CUR})?",
    ),
    _t(
        "eeo_gender",
        _eeo("gender"),
        rf"{_YOUR}(?:gender|gender identity|sex)|how do you (?:describe|identify) your gender",
        rf"{_YOUR}(?:sexe|genre)",
    ),
    _t("eeo_race", _eeo("race"), rf"{_YOUR}race(?: / ethnicity)?|race and ethnicity"),
    _t(
        "eeo_ethnicity",
        _eeo("ethnicity"),
        rf"{_YOUR}(?:ethnicity|ethnic (?:origin|background|group))|{_YOUR}origine ethnique"
        r"|(?:are you )?hispanic (?:/|or) latino",
    ),
    _t(
        "eeo_disability",
        _eeo("disability"),
        rf"{_YOUR}disability(?: status)?|do you have a disability|are you a person with a disability"
        r"|voluntary self identification of disability",
        r"(?:etes vous en )?situation de handicap|avez vous un handicap|handicap",
    ),
    _t(
        "eeo_veteran",
        _eeo("veteran"),
        rf"{_YOUR}(?:protected |military )?veteran status|are you a (?:protected )?veteran",
        r"statut d ancien combattant",
    ),
    _t(
        "eeo_sexual_orientation",
        _eeo("sexual_orientation"),
        rf"{_YOUR}sexual orientation|{_YOUR}orientation sexuelle",
    ),
    _t(
        "eeo_age",
        _eeo("age"),
        rf"{_YOUR}age(?: range| group| bracket)?|{_YOUR}(?:date of birth|birth date|birthdate)",
        rf"{_YOUR}(?:date de naissance|tranche d age|age)",
    ),
    _t(
        "how_heard",
        _heard,
        r"how did you (?:hear|find out|learn) (?:about|of) (?:us|this (?:job|role|position|opportunity"
        r"|offer|vacancy|posting)|the (?:job|role|position|opportunity)|our company|this company)"
        r"|how did you find (?:us|this (?:job|role|position|opportunity))"
        r"|where did you (?:hear|find out|learn) (?:about|of) (?:us|this (?:job|role|position|opportunity))"
        r"|where did you find (?:us|this (?:job|role|position))|(?:referral |application )?source",
        r"comment avez vous (?:connu|decouvert) (?:notre entreprise|notre societe|cette offre|ce poste|nous|l entreprise)"
        r"|comment avez vous entendu parler (?:de nous|de cette offre|de ce poste|de notre entreprise)"
        r"|ou avez vous (?:vu|trouve) cette offre",
    ),
    _t(
        "previously_applied",
        _previously_applied,
        rf"{_SUBMIT}(?:previously |ever |already )?applied (?:to|at|with|for a (?:job|role|position) (?:at|with))"
        r" (?:this company|us|our company|the company|this organi[sz]ation)(?: before| previously)?"
        r"|have you (?:previously |ever |already )?applied (?:here|before|here before)",
        r"avez vous deja postule (?:chez nous|dans notre (?:entreprise|societe)|aupres de nous)"
        r"|avez vous deja candidate (?:chez nous|aupres de nous)",
    ),
    _t(
        "years_of_experience",
        _experience_years,
        rf"how many years of {_EXPERIENCE} do you have(?: in total| overall)?",
        rf"(?:total |overall )?(?:number of )?years of {_EXPERIENCE}(?: in total| overall)?",
        rf"do you have {{Y}}(?: of)? {_EXPERIENCE}",
        r"combien d annees d experience(?: professionnelle)? avez vous(?: au total)?",
        r"(?:nombre d )?(?:annees|ans) d experience(?: professionnelle)?(?: au total)?"
        r"|experience(?: professionnelle)? (?:en )?annees",
        r"avez vous {Y} d experience(?: professionnelle)?",
    ),
    _t(
        "highest_degree",
        _education,
        rf"{_YOUR}highest (?:level of )?(?:education|degree|qualification)(?: obtained| completed)?"
        r"|level of education|education level|degree",
        r"niveau d etudes|niveau de (?:formation|diplome)|(?:votre )?(?:dernier |plus haut )?diplome(?: obtenu)?",
    ),
    _t(
        "graduation_year",
        _graduation_year,
        r"graduation (?:year|date)|year of graduation|(?:what )?year did you graduate",
        r"annee d obtention(?: du diplome)?|annee de (?:diplome|diplomation|fin d etudes)",
    ),
    _t(
        "school",
        _school,
        rf"{_YOUR}(?:name of (?:your )?)?(?:school|university|college|institution)(?: name| attended)?",
        r"(?:votre )?(?:ecole|universite|etablissement)(?: d enseignement)?",
    ),
    _t(
        "languages",
        _languages,
        rf"{_YOUR}languages(?: spoken| you speak)?|spoken languages|(?:which|what) languages do you speak"
        r"|language skills",
        r"(?:vos )?langues(?: parlees)?|quelles langues parlez vous|(?:vos )?competences linguistiques",
    ),
    _t(
        "language_level",
        _language_level,
        r"(?:what is )?(?:your )?{L} (?:language )?(?:level|proficiency)(?: level)?",
        r"(?:what is )?(?:your )?level (?:of|in) {L}",
        r"(?:quel est )?(?:votre )?niveau (?:d |de |en )?{L}",
    ),
    _t("speaks", _speaks(False), r"(?:do|can) you speak {L}", r"parlez vous {L}"),
    _t(
        "fluent_in",
        _speaks(True),
        r"are you fluent in {L}",
        r"parlez vous couramment {L}",
        r"etes vous courant en {L}",
    ),
    _t(
        "cover_letter",
        _document,
        r"(?:your |upload (?:your |a )?|attach (?:your |a )?)?(?:cover letter|motivation letter)",
        r"(?:votre )?lettre de motivation",
    ),
    _t(
        "resume",
        _document,
        r"(?:your |upload (?:your |a )?|attach (?:your |a )?)?(?:resume|cv|curriculum vitae|resume / cv|cv / resume)",
        r"(?:votre )?(?:cv|curriculum vitae)",
    ),
)
# One regex per pattern: two patterns of a template may hold the same slot.
_COMPILED = tuple((t, re.compile(rf"^(?:{p})$")) for t in TEMPLATES for p in t.patterns)


def match_template(label: str) -> tuple[Template, re.Match[str]] | str:
    """The template a label fully matches, or why none does: "two_questions", "negated_question"
    (a template matched, but the label is negated and the template is not written for that) or
    "not_a_known_question"."""
    text = normalise(label)
    if _two_questions(label, text):
        return "two_questions"
    for template, pattern in _COMPILED:
        found = pattern.match(text)
        if found is None:
            continue
        if not template.negated:
            rest = text
            for slot in ("years",):
                if found.groupdict().get(slot):
                    rest = rest.replace(found.group(slot), " ")
            if _NEGATED.search(rest):
                return "negated_question"
        return template, found
    return "not_a_known_question"


# --- policies first: what no source may answer, and the person's stated policies ------------

# A field about someone else: a referrer, a reference, an emergency contact, a manager.
_THIRD_PERSON = re.compile(
    r"\breferr|\breferee|\breferences?\b|emergency|next of kin|\bmanager'?s?\b|\bsupervisor"
    r"|\brecruiter|\bspouse|\bparrain(?:e|s)?\b|\bmarraine|personne a (?:contacter|prevenir)"
    r"|\brecommand|\bconjoint|\bresponsable\b|superieur hierarchique|\bguardian|\btuteur"
)
_HOW_HEARD = r"how did you (?:hear|find|learn)|where did you (?:hear|find)|source of application|comment avez-vous (?:entendu|connu|decouvert)|referral source"
_REFERENCE_CHECK = r"reference check|references? (?:check|contact)|contacter vos references|contactions vos references|^may we contact your references\??(?: \(reference check\))?$"
# A question about age: never read as years of experience, and the profile stores no age.
_AGE = re.compile(
    r"\byears? old\b|\bold\b|\byears? of age\b|\bage of\b|legal age|age legal|age minimum"
    r"|\bmajeure?s?\b|\bmajorite\b|\b(?:16|18|21)\s*(?:\+|ans\b(?!\s*d))"
    r"|\b(?:over|at least|above|au moins|plus de)\s+(?:16|18|21)\b(?!\s*\+?\s*(?:years?|ans)\s+(?:of|d))"
)
# Citizenship and birthplace: never the country of residence.
_CITIZENSHIP = re.compile(
    r"citizen|nationalit|citoyen|passport|passeport|\bborn\b|(?:country|place|city) of birth"
    r"|birth ?place|(?:pays|lieu|ville) de naissance|\bnee?\(?e?\)? (?:a|en|au)\b"
)
_CRIMINAL = re.compile(r"criminal|casier|convict|condamn|felon|offen[cs]e|infraction|delit")
# Pay received, not pay wanted: a pay word and a past or present qualifier, in either order.
_PAY = re.compile(
    r"\b(?:salary|salaries|compensation|pay|remuneration|salaire|package|ctc|wages?|earn\w*|gagne\w*)\b"
)
_PAID = re.compile(
    r"\b(?:current|currently|present|previous|last|prior|past|former|history|historique|today'?s?"
    r"|now|existing|drawn|earn\w*|gagne\w*|actuel(?:le)?s?|dernier|derniere|precedente?|ancien(?:ne)?)\b"
)
_PRONOUNS = r"pronoun|pronom"
_SALARY_WORDS = r"salary|compensation|remuneration|pay expectation|pretentions|salaire|expected pay|rate expectation|tarif"


# A long answer the question asks the person to write: the agent's, whatever keyword it holds.
_FREE_TEXT_OPENER = re.compile(
    r"^(?:why|pourquoi|tell us|describe|decrivez|explain|expliquez|racontez|parlez-nous|what makes|qu'est-ce qui)\b"
)


def _policies_first(field: FormField, s: Sections, text: str) -> Left | bool:
    """What is settled before the answer bank is read: the fields no source may fill (someone
    else's details, age, citizenship and birthplace, a criminal record, pay received, free text)
    are left; a label that touches an EEO topic or salary skips the bank, so a bank answer typed
    once never overrides a policy. Returns the Left, else whether the bank may be read."""
    if _THIRD_PERSON.search(text) and not re.search(f"{_HOW_HEARD}|{_REFERENCE_CHECK}", text):
        return _left(field, "third_person")
    if _AGE.search(text):
        return _left(field, "age_not_stored")
    if _CITIZENSHIP.search(text):
        return _left(field, "citizenship_not_stored")
    if _CRIMINAL.search(text):
        return _left(field, "criminal_record_not_stored")
    if _PAY.search(text) and _PAID.search(text):
        return _left(field, "current_salary_not_stored")
    if _FREE_TEXT_OPENER.search(text) and (
        field.kind == "textarea" or text.startswith(("why", "pourquoi"))
    ):
        # Written for one company: a banked answer is never replayed to another.
        return _left(field, "free_text_for_the_agent")
    if not re.search(_PRONOUNS, text) and eeo_topic(text):
        if s.answers.eeo_policy == "leave":
            return _left(field, "eeo_policy_leave")
        return False
    if re.search(_SALARY_WORDS, text):
        if s.answers.salary_stance_of == "leave_blank":
            return _left(field, "salary_leave_blank")
        return False
    return True


def _leave_blank_hit(text: str, s: Sections) -> bool:
    for item in s.answers.leave_blank:
        topic = fold(item.split(":", 1)[0])
        if 2 < len(topic) <= 40 and re.search(rf"\b{re.escape(topic)}\b", text):
            return True
    return False


# A checkbox that asks about the person (a yes or no in disguise) goes by the templates.
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
    settled = _policies_first(field, sections, text)
    if isinstance(settled, Left):
        return settled
    # The bank answers the exact question it was typed for (its normalised key), nothing else.
    bank = context.bank.lookup(field.label) if settled and context.bank else None
    if bank is not None and field.kind not in ("file", "checkbox"):
        if field.kind in _CHOICES and field.options and field.kind != "yes_no":
            picked = match_option(bank, field.options)
            if picked is None:
                return Left("no_matching_option", field.options, field.required)
            return Answer((picked,) if field.kind == "multi" else picked, "bank", picked)
        if field.kind == "yes_no" and option_key(bank) in YES_OPTIONS | NO_OPTIONS:
            return _boolean(field, option_key(bank) in YES_OPTIONS, "bank")
        return _fits_kind(field, Answer(bank, "bank"))
    matched = match_template(field.label)
    if isinstance(matched, str):
        if field.kind == "checkbox":
            return _checkbox(field)
        return Left(matched, field.options, field.required)
    template, found = matched
    return _fits_kind(field, template.handler(field, context, sections, found))


def _fits_kind(field: FormField, result: Answer | Left) -> Answer | Left:
    """The field's kind has the last word: a file field takes only a document, a date field only
    a stored date."""
    if isinstance(result, Left):
        return result
    if field.kind == "file" and not result.source.startswith("documents."):
        return Left("not_a_file", field.options, field.required)
    if field.kind == "date" and not (
        isinstance(result.value, str) and _ISO_DATE.match(result.value)
    ):
        return Left("not_a_date", field.options, field.required)
    return result
