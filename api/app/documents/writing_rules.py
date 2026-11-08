"""Keep a chat correction as a writing rule (SPEC-documents.md, "The chat, and keeping a rule").

Adapted from `modes/retro.md` of career-ops (MIT, NOTICE): a rule is proposed from the person's
own correction, quoted, nothing is written until the person accepts it, and a fact is never a
rule. Code, no model, on the folded text (`rules.fold`), English and French:

- `offer(text)`: the rule the chat offers to keep for a message of the person, else None. It
  fails closed: a message is offered only when it states no fact (`is_fact`) and each of its
  clauses has a shape the code recognises (`_recognised`): never, avoid, stop or do not, then a
  verb and a quoted or short phrase ("Never say leveraged"); words of style only ("Shorter
  summary", "Le ton est trop formel"); quoted words swapped ("Use 'built' instead of
  'developed'"); a quoted word to avoid ("'Synergy' is banned"). Whatever else gets no offer:
  the person can always write a rule in the profile's facts by hand. The rule offered is the
  message itself, its white space collapsed; the person reads it, may edit it, and keeps it or
  not.
- `is_fact(text)`: a word that names the person (I, you, my, me, je, tu, ton, votre...), a
  number in digits or in words, or a verb that puts words in the document (say, write, mention,
  call, add, include, replace, dire, écrire, ajouter...) not negated and followed by anything
  but quoted words and words of style. A rule says how to write; the writers read the facts
  section as the source of claims, so a fact kept there as a rule would make it pass.
- `keep(db, user_id, text, now)`: the person's click on Keep, which is the acceptance. The rule
  is checked again as `offer` reads it (a fact, or a message the offer would not make, is
  refused), then appended as one line of the block "Writing rules" of the profile's facts
  section (made when absent), through a profile item proposal of the agents module accepted by
  that click (`proposals.create`, then `proposals.accept`): one profile revision, recorded in
  the proposals, undoable as every accepted item is.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents import proposals
from app.documents import rules
from app.ingestion import items
from app.profile import service as profile_service

RULE_MAX = 300
BLOCK_TITLE = "Writing rules"
# The words of the short phrase after never, avoid or do not, its articles left out.
OBJECT_WORDS_MAX = 4


def _words(*groups: str) -> frozenset[str]:
    return frozenset(word for group in groups for word in group.split())


# Words that name the person, in English and French: such a message is a fact.
_PERSON = _words(
    "i me my mine myself you your yours yourself yourselves we us our ours ourselves",
    "je j' me m' moi mon ma mes mien mienne miens miennes tu te t' toi ton ta tes tien tienne",
    "tiens tiennes vous votre vos votres nous notre nos notres",
)
# "ton" is the tone after one of these ("le ton"), the person's otherwise ("ton titre").
_TONE_BEFORE = _words("le du au un ce quel")
_NUMBERS = _words(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
    "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty",
    "ninety hundred hundreds thousand thousands million millions billion billions dozen dozens",
    "first second third fourth fifth sixth seventh eighth ninth tenth",
    "deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize",
    "vingt vingts trente quarante cinquante soixante cent cents mille milliard milliards",
    "douzaine douzaines dizaine dizaines centaine centaines premier premiere deuxieme",
    "troisieme quatrieme cinquieme sixieme septieme huitieme neuvieme dixieme",
)
# Verbs that put words in the document.
_VERBS = _words(
    "say says saying write writes writing use uses using call calls calling put puts putting",
    "mention mentions mentioning add adds adding include includes including state states",
    "stating note noting list listing start starts starting begin begins beginning open opens",
    "opening end ends ending close closes closing describe describes describing claim claims",
    "claiming highlight highlights highlighting stress stressing emphasise emphasize",
    "emphasising emphasizing repeat repeats repeating refer refers referring spell spells",
    "spelling replace replaces replacing change changes changing present presents presenting",
    "show shows showing insert inserts inserting keep keeps keeping make makes making talk",
    "talks talking speak speaks speaking tell tells telling name names naming give gives giving",
    "dis dites dire ecris ecrivez ecrire mets mettez mettre utilise utilisez utiliser emploie",
    "employez employer mentionne mentionnez mentionner ajoute ajoutez ajouter inclus incluez",
    "inclure precise precisez preciser indique indiquez indiquer appelle appelez appeler",
    "remplace remplacez remplacer cite citez citer parle parlez parler commence commencez",
    "commencer termine terminez terminer decris decrivez decrire presente presentez presenter",
    "souligne soulignez souligner insiste insistez insister change changez changer garde gardez",
    "garder fais faites faire rends rendez rendre repete repetez repeter nomme nommez nommer",
    "donne donnez donner montre montrez montrer",
)
# Words of style: how a text reads and the parts it has.
_STYLE = _words(
    "short shorter shortest long longer lengthy simple simpler simplest plain plainer clear",
    "clearer crisp crisper tight tighter concise brief briefer succinct terse formal informal",
    "formality wordy verbose vague generic salesy flowery stiff casual dense direct friendly",
    "warm warmer cold colder dry drier punchy punchier humble modest bold bolder confident",
    "active passive polite neutral natural human robotic readable lighter softer stronger sober",
    "enthusiastic excited emotional pompous fancy fluffy cheesy corporate repetitive cliched",
    "british american personality energy",
    "tone tones length register style voice wording phrasing word words sentence sentences",
    "paragraph paragraphs line lines bullet bullets point points summary intro introduction",
    "opening closing conclusion ending letter cv resume text headline heading headings title",
    "titles subject salutation greeting question questions punctuation dash dashes hyphen",
    "hyphens comma commas exclamation exclamations mark marks semicolon semicolons colon colons",
    "ellipsis emoji emojis capital capitals caps adjective adjectives adverb adverbs verb verbs",
    "superlative superlatives buzzword buzzwords jargon cliche cliches filler fluff acronym",
    "acronyms abbreviation abbreviations contraction contractions spelling grammar repetition",
    "repetitions tense tenses",
    "court courte courts courtes concis concise concises formel formelle formels formelles",
    "informel informelle simple simples directe directs directes sobre sobres longue longs",
    "longues vagues generique generiques pompeux pompeuse familier familiere clair claire",
    "clairs claires precis leger legere lourd lourde chaleureux chaleureuse neutre naturel",
    "naturelle fluide percutant percutante modeste poli polie",
    "ton longueur formulation formulations tournure tournures registre mot mots phrase phrases",
    "paragraphe paragraphes ligne lignes puce puces introduction accroche lettre texte titre",
    "titres ponctuation tiret tirets virgule virgules adjectif adjectifs adverbe adverbes",
    "superlatif superlatifs anglicisme anglicismes majuscule majuscules orthographe",
)
# Words that carry no content of their own.
_FUNCTION = _words(
    "a an the it it's its this that that's these those there there's is are be being been was",
    "do does sounds sound feels feel reads read looks look seems seem too very so quite more",
    "most less least fewer much bit little lot lots way enough not no off overly rather really",
    "just only always all any each every everywhere throughout whole entire in on of for with",
    "to at by from into about and or but nor as than instead ok okay now again also here",
    "overall them they some same like kind sort slightly maybe still anymore ever even well",
    "better worse",
    "le la les l' un une des de d' du au aux a en et ou ni mais pour par avec sur dans sans",
    "est sont etre sois soyez soit c' ce cet cette ces ca cela ceci il elle ils elles y trop",
    "plus moins tres assez peu bien toujours seulement ne n' pas jamais partout tout toute",
    "tous toutes encore aussi beaucoup vraiment juste donc alors puis ensuite desormais",
    "dorenavant maintenant mieux sonne semble",
)
# What may open a clause before its negation ("Also, from now on, never say...").
_LEAD = _words(
    "just also and but so ok okay oh again now always then from on",
    "et aussi mais alors donc puis ensuite desormais dorenavant toujours maintenant a partir de",
)
# A verb right after one of these is negated ("never say", "do not use", "jamais écrire").
_NOT_BEFORE = _words("never don't dont not stop quit avoid avoiding jamais sans")
_BETWEEN = _words("ever again even really just")
_FRENCH_NOT = _words("pas jamais plus")
_STOP_FR = _words("evite evitez eviter arrete arretez arreter cesse cessez cesser")
# What takes a phrase to avoid with no verb ("Avoid buzzwords", "Pas de jargon").
_AVOID = _words("avoid no not without never stop evite evitez eviter sans jamais bannis bannissez")
# What turns the phrase after a verb into a statement ("Never say that...", "...it is small").
_STATEMENT = _words(
    "that que qu' because since which who qui car parce when quand if si",
    "is are was were be been has have had will est sont etait etaient ont avait avaient sera",
)
# What a short phrase is not counted by.
_OBJECT_FILLER = _words(
    "the a an word words term terms phrase in on of or and nor again anymore ever any more",
    "le la les l' un une des de d' du mot mots terme termes expression ou et ni plus dans en",
)
# The words of a swap of quoted words ("Use 'built' instead of 'developed'").
_SWAP = _words(
    "instead of rather than with by for the a word words term terms everywhere always",
    "au lieu de d' plutot que qu' par le la les l' mot mots terme termes toujours partout a",
)
_SWAP_MARKERS = _words(
    "instead rather replace replaces replacing swap lieu plutot remplace remplacez remplacer"
)
# A quoted word to avoid ("'Synergy' is banned").
_BANNED = _words(
    "banned forbidden overused out never no not interdit interdits interdite interdites banni",
    "bannis bannie bannies eviter jamais non",
)
_CONJUNCTIONS = _words("and but or nor then et mais ou ni puis")
_FRENCH_CLITICS = _words("moi toi le la les lui leur nous vous y en")
_POLITE = re.compile(
    r"\b(?:please|pls|plz|thanks|thank you|thx|cheers|merci(?: beaucoup| bien)?"
    r"|s'il (?:te|vous) plait|stp|svp)\b"
)
_TOKEN = re.compile(
    '(?P<q>"[^"]{1,80}"|\u201c[^\u201d]{1,80}\u201d|\u201e[^\u201c\u201d]{1,80}[\u201c\u201d]'
    "|\u00ab[^\u00bb]{1,80}\u00bb|\u2039[^\u203a]{1,80}\u203a|(?<![\\w'])'[^']{1,80}'(?!\\w))"
    r"|(?P<e>\b(?:qu|[cdjlmnst])'(?=\w))"
    r"|(?P<w>\w+(?:[-']\w+)*)"
    "|(?P<p>[,;:.!?()\\[\\]/\u2026-])"
)
QUOTED = "\u2018q\u2019"  # the token a quoted phrase stands as, never a folded word


@dataclass(frozen=True, slots=True)
class _Reading:
    clauses: tuple[tuple[str, ...], ...]  # words, a quoted phrase as QUOTED
    words: tuple[str, ...]  # every word, those inside quotes included


def _split(word: str) -> list[str]:
    """`appelle-moi` as two words: a French verb with its pronoun."""
    parts = word.split("-")
    return parts if len(parts) > 1 and parts[-1] in _FRENCH_CLITICS else [word]


def _read(text: str) -> _Reading:
    folded = _POLITE.sub(" ", rules.fold(text).text)
    clauses: list[tuple[str, ...]] = []
    current: list[str] = []
    words: list[str] = []
    for found in _TOKEN.finditer(folded):
        if found.lastgroup == "p":
            clauses.append(tuple(current))
            current = []
        elif found.lastgroup == "q":
            current.append(QUOTED)
            inner = found.group()[1:-1]
            words += [w for m in _TOKEN.finditer(inner) if m.lastgroup in ("e", "w")
                      for w in _split(m.group())]  # fmt: skip
        else:
            parts = _split(found.group())
            current += parts
            words += parts
    clauses.append(tuple(current))
    return _Reading(tuple(c for c in clauses if c), tuple(words))


# --- what the message says ----------------------------------------------------------------------


def _negated(clause: tuple[str, ...], i: int) -> bool:
    """Is the verb at `i` negated: "never say", "do not use", "n'écris jamais", "évite de dire"."""
    before = clause[i - 1] if i >= 1 else ""
    before2 = clause[i - 2] if i >= 2 else ""
    after = clause[i + 1] if i + 1 < len(clause) else ""
    return (
        before in _NOT_BEFORE
        or (before in _BETWEEN and before2 in _NOT_BEFORE)
        or (before == "longer" and before2 == "no")
        or (before in ("ne", "n'") and after in ("pas", "jamais", "plus"))
        or (before in ("pas", "plus") and before2 == "ne")
        or (before in ("de", "d'") and before2 in _STOP_FR)
    )


def _puts_words(clause: tuple[str, ...]) -> bool:
    """A verb not negated whose phrase holds a word neither quoted nor of style ("Always mention
    Kubernetes", "Replace developer with architect", "Dis que...")."""
    for i, word in enumerate(clause):
        if word not in _VERBS or _negated(clause, i):
            continue
        for j in range(i + 1, len(clause)):
            later = clause[j]
            if later in _VERBS or later in _NOT_BEFORE or later in _CONJUNCTIONS:
                break
            if later in ("que", "qu'"):
                if clause[j - 1] != "plutot":
                    return True
            elif not (later == QUOTED or later in _STYLE or later in _SWAP or later in _FUNCTION):
                return True
    return False


# A removal verb said not to happen asks to always include what follows: "Never omit X" is a fact.
_REMOVE = frozenset(
    {"omit", "omits", "forget", "forgets", "drop", "drops", "remove", "removes", "skip", "skips",
     "cut", "cuts", "delete", "deletes", "exclude", "excludes", "leave", "oublier", "oublie",
     "oublies", "oubliez", "omettre", "omets", "omet", "omettez", "retirer", "retire", "retirez",
     "enlever", "enleve", "enlevez", "supprimer", "supprime", "supprimez"}
)  # fmt: skip


def _keeps_in(clause: tuple[str, ...]) -> bool:
    return any(w in _REMOVE and _negated(clause, i) for i, w in enumerate(clause))


def _states_fact(reading: _Reading) -> bool:
    if any(_keeps_in(clause) for clause in reading.clauses):
        return True
    words = reading.words
    for i, word in enumerate(words):
        parts = {word, *word.split("-")}
        if "'" in word[:-1]:
            parts.add(word.split("'", 1)[0])  # "i'm", "you're"
        if any(ch.isdigit() for ch in word) or parts & _NUMBERS:
            return True
        if parts & _PERSON and not (word == "ton" and i and words[i - 1] in _TONE_BEFORE):
            return True
    return any(_puts_words(clause) for clause in reading.clauses)


def is_fact(text: str) -> bool:
    return _states_fact(_read(clean(text)))


# --- the shapes of a rule -----------------------------------------------------------------------


def _avoided(clause: tuple[str, ...]) -> tuple[str, ...] | None:
    """The phrase a clause says not to write, None when it says no such thing."""
    start = 0
    while start < len(clause) - 1 and clause[start] in _LEAD:
        start += 1
    words = clause[start:]
    n = len(words)
    first, second = (*words, "", "")[:2]
    verb_from: list[int] = []
    if first in ("never", "don't", "dont", "not", "stop", "quit", "avoid", "avoiding", "jamais"):
        verb_from.append(1)
    if (first, second) in (("do", "not"), ("does", "not"), ("no", "longer"), ("plus", "jamais")):
        verb_from.append(2)
    if first == "ne" and second in ("pas", "jamais", "plus"):
        verb_from.append(2)
    if first in _STOP_FR and second in ("de", "d'"):
        verb_from.append(2)
    for k in verb_from:
        while k < n and words[k] in _BETWEEN:
            k += 1
        if k < n and words[k] in _VERBS:
            return words[k + 1 :]
    if first in ("ne", "n'") and second in _VERBS and n > 2 and words[2] in _FRENCH_NOT:
        return words[3 + (n > 3 and words[3] in ("jamais", "plus")) :]
    if (first, second) in (("no", "more"), ("plus", "jamais")):
        return words[2:]
    if first in _AVOID or first == "pas":
        return words[1:]
    return None


def _short(phrase: tuple[str, ...]) -> bool:
    """A quoted or short phrase, no statement ("that...", "que...") and no verb first."""
    if not phrase or len(phrase) > 2 * OBJECT_WORDS_MAX + 2 or phrase[0] in _VERBS:
        return False
    if any(word in _STATEMENT for word in phrase):
        return False
    counted = [word for word in phrase if word not in _OBJECT_FILLER]
    return 1 <= len(counted) <= OBJECT_WORDS_MAX


def _style_only(clause: tuple[str, ...]) -> bool:
    return any(word in _STYLE for word in clause) and all(
        word in _STYLE
        or word in _FUNCTION
        or word in _VERBS
        or word in _AVOID
        or word in _NOT_BEFORE
        or word in _STOP_FR
        or word in _LEAD
        for word in clause
    )


def _swap(clause: tuple[str, ...]) -> bool:
    return QUOTED in clause and all(
        word == QUOTED or word in _SWAP or word in _VERBS or word in _LEAD for word in clause
    )


def _quoted_banned(clause: tuple[str, ...]) -> bool:
    return (
        QUOTED in clause
        and any(word in _BANNED for word in clause)
        and all(word == QUOTED or word in _BANNED or word in _FUNCTION for word in clause)
    )


def _recognised(reading: _Reading) -> bool:
    """Every clause has the shape of a rule on the wording, and one at least says something."""
    swapped = any(word in _SWAP_MARKERS for clause in reading.clauses for word in clause)
    said = False
    for clause in reading.clauses:
        if QUOTED not in clause and all(w in _FUNCTION or w in _LEAD for w in clause):
            continue  # "ok", "also"
        avoided = _avoided(clause)
        if not (
            (avoided is not None and _short(avoided))
            or _style_only(clause)
            or (swapped and _swap(clause))
            or _quoted_banned(clause)
        ):
            return False
        said = True
    return said


def clean(text: str) -> str:
    return " ".join(text.split())


def offer(text: str) -> str | None:
    """The rule offered for a message of the person, None when it is no wording correction."""
    rule = clean(text)
    if not rule or len(rule) > RULE_MAX:
        return None
    reading = _read(rule)
    if _states_fact(reading) or not _recognised(reading):
        return None
    return rule


class RuleRefusedError(ValueError):
    """`reason`: `empty`, `long`, `fact` or `shape` (not a rule the offer would make)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FactsFullError(Exception):
    """The facts section cannot take the rule as it is now (`message`, the section model's)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class Kept:
    rule: str
    proposal_id: uuid.UUID | None  # None when the block held the rule already


def _block(facts: dict[str, Any] | None) -> dict[str, Any] | None:
    for block in (facts or {}).get("blocks") or ():
        if isinstance(block, dict) and str(block.get("title", "")).casefold() == "writing rules":
            return block
    return None


def keep(db: Session, user_id: uuid.UUID, text: str, now: datetime) -> Kept:
    """Append the rule to the person's writing rules (module docstring). The caller commits."""
    rule = clean(text)
    if not rule:
        raise RuleRefusedError("empty")
    if len(rule) > RULE_MAX:
        raise RuleRefusedError("long")
    reading = _read(rule)
    if _states_fact(reading):
        raise RuleRefusedError("fact")
    if not _recognised(reading):
        raise RuleRefusedError("shape")
    profile = profile_service.get_profile(db, user_id, now, lock=True)
    block = _block((profile.sections or {}).get("facts"))
    title = str(block["title"]) if block is not None else BLOCK_TITLE
    body = str(block.get("body") or "") if block is not None else ""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if rules.fold(rule).text in {rules.fold(line).text for line in lines}:
        return Kept(rule, None)
    value = {"title": title, "body": "\n".join([*lines, rule])}
    key = items.entry_key(items.SLOTS["facts"]["blocks"], value)
    payload = {"section": "facts", "key": key, "action": "add", "value": value, "citations": []}
    try:
        proposal = proposals.create(
            db,
            user_id,
            target=proposals.TARGET_PROFILE,
            target_id=None,
            kind=proposals.KIND_PROFILE_ITEM,
            payload=payload,
            run_id=None,
            now=now,
        )
        proposals.accept(db, user_id, proposal.id, now, profile_revision=profile.revision)
    except proposals.NotStoredError:
        return Kept(rule, None)
    except proposals.InvalidValueError as refused:
        raise FactsFullError(str(refused)) from None
    except proposals.ProfileConflictError as conflict:
        raise FactsFullError(conflict.message) from None
    return Kept(rule, proposal.id)
