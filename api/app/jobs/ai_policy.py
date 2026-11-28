"""What a posting says about the candidates' own use of AI (jobs J40; SPEC-jobs, "Before the
click"): `says(said)`, the rule of the signal `ai_policy`, on one sentence as `signals.words` reads
it. A pure function of the sentence, linear in its length (every gap is bounded).

A sentence is the signal when its AI term is used by the candidate in the application, the
assessments or the interviews, or when it names a policy about that: a policy that forbids it, one
that allows or encourages it, one that only points to a page of guidance. English, French, German.
It requires three things together:

1. who: a candidate word (candidates, applicants, candidats, Bewerber ...), or "you" with a modal;
2. what: the AI term used by them (a use verb, or a noun: usage, policy, guidance), or a document
   of the application written by AI ("AI generated cover letters");
3. where: either an employer's stance verb ("we encourage candidates to use AI", "interdit aux
   candidats"), which needs no more, or a word of the hiring path (an application, an interview,
   an assessment) in the same sentence.

It skips what is not that: the employer's own use of AI (no candidate before the use verb, and a
gap of words never holds "we", "our" or "who"), AI as a skill or a task of the role (no stance,
no path, and a gap never holds "experience", "skills", "proficient"), the employer's messages, a
product's name ("our AI assistant" is the employer's), a sentence that negates (no policy, not
required) unless it names the prohibition itself, and a heading that names AI in the process with
no candidate and no policy word ("Using AI in our hiring process"). Whatever no rule names gives
nothing.
"""

import re

_TOK = r"[a-z0-9]+"
_AI = (
    r"(?:ai|a i|genai|gen ai|generative ai|artificial intelligence|llms?|chatgpt|chat gpt"
    r"|large language models?|ia|ia generative|intelligence artificielle|ki"
    r"|kunstliche intelligenz)"
)
# The AI a person uses is not the employer's own product ("our AI assistant").
_AI_USED = (
    r"(?<!\bour )(?<!\bits )(?<!\btheir )(?<!\bnotre )(?<!\bnos )(?<!\bunsere )(?<!\bunser )" + _AI
)
_AI_TOOLS = _AI + r"(?: (?:tools?|assistants?|assisted|tooling|chatbots?|outils?|werkzeuge))?"

_CAND_EN = r"(?:candidates?|applicants?|job seekers?|jobseekers?)"
_CAND_FR = r"(?:candidats?|candidates?|postulants?|postulantes?)"
_CAND_DE = r"(?:bewerber(?:n|in|innen)?|bewerbende[nr]?|kandidat(?:en|innen|in)?)"
_CAND = rf"(?:{_CAND_EN}|{_CAND_FR}|{_CAND_DE})"

# Words a gap between the candidate and the AI never holds: the employer as the subject, a relative
# clause ("candidates who use AI daily" is a skill) and the words that make AI a competence.
_BLOCK = (
    r"(?:we|our|us|who|that|which|whose|with|qui|que|die|der|welche|nous|wir|unser\w*|notre|nos"
    r"|recruiters?|employers?|compan(?:y|ies)|teams?|experience\w*|skills?|skilled|knowledge"
    r"|proficien\w*|background|expertise|familiar\w*|comfortable|fluent|savvy|passionate"
    r"|competences?|maitrise\w*|connaissances?|erfahrung\w*|kenntnisse|vertraut\w*)"
)
_G = rf"(?: (?!{_BLOCK}\b){_TOK})"
_GA = rf"(?: {_TOK})"

_USE_EN = r"(?:use|using|uses|utili[sz]e|utili[sz]ing|rely on|relying on|lean on)"
_USE_FR = (
    r"(?:utiliser|utilisent|utilisez|utilisant|utilise|recourir a|recourez a|recourent a"
    r"|faire appel a|avoir recours a)"
)
_USE_DE = r"(?:zu nutzen|zu verwenden|zu benutzen|einzusetzen|nutzen|verwenden|benutzen|einsetzen)"

_STANCE_EN = (
    r"(?:encourage|encourages|encouraged|encouraging|allow|allows|allowed|permit|permits"
    r"|permitted|welcome|welcomes|expect|expects|expected|ask|asks|asked|invite|invites|invited"
    r"|require|requires|required|prohibit|prohibits|prohibited|forbid|forbids|forbidden|ban|bans"
    r"|banned|discourage|discourages|discouraged|request|requests|requested|urge|urges|advise"
    r"|advises|advised|free|permission)"
)
_STANCE_FR = (
    r"(?:encourageons|encourage|encouragent|encourages|autorisons|autorise|autorisent|autorises"
    r"|permettons|permet|acceptons|invitons|invites|demandons|demande|interdisons|interdit"
    r"|interdits|exigeons|decourageons|souhaitons|attendons|recommandons|libres|pries)"
)
_STANCE_DE = (
    r"(?:ermutigen|ermuntern|ermutigt|ermuntert|erlauben|erlaubt|gestatten|gestattet|begrussen"
    r"|begrusst|erwarten|erwartet|bitten|gebeten|untersagen|untersagt|verbieten|verboten"
    r"|wunschen|erwunscht|akzeptieren|dulden|empfehlen|empfohlen)"
)

# The hiring path: where candidates use AI. An "application" is the person's own or the process'.
_NOT_SOFTWARE = (
    r"(?! (?:development|dev|security|code|software|layer|server|logic|performance|architecture"
    r"|stack|platform|framework|programming|engineering|features?|data|support|tracking"
    r"|monitoring|testing|infrastructure|migration|modernization|modernisation|owners?"
    r"|engineers?|developers?))"
)
_OWN = r"(?:your|our|the|their|each|every|this|votre|notre|vos|ihre|unsere)"
_APPLICATION = rf"(?:{_OWN} applications?{_NOT_SOFTWARE}|when you appl(?:y|ying))"
_PROCESS = re.compile(
    rf"\b(?:{_APPLICATION}|application process|applying|interviews?|assessments?"
    r"|coding (?:tests?|challenges?|exercises?)|take home|technical tests?|cover letters?|cvs?"
    r"|resumes?|selection process|hiring process|recruitment process|recruiting process"
    r"|candidatures?|candidater|postuler|entretiens?|evaluations?|tests? techniques?"
    r"|processus de (?:recrutement|selection|candidature)|lettres? de motivation"
    r"|bewerbung\w*|vorstellungsgesprach\w*|auswahlverfahren|einstellungsverfahren"
    r"|einstellungsprozess|lebenslauf\w*|anschreiben)\b"
)
# The narrower path of a policy heading: the employer's own recruiting is not it.
_PROCESS_APP = re.compile(
    r"\b(?:application process(?:es)?|application stage|applying|interviews?|interview process"
    r"|assessments?|selection process|candidatures?|processus de candidature|entretiens?"
    r"|evaluations?|bewerbung\w*|vorstellungsgesprach\w*|auswahlverfahren)\b"
)

_DOCS = (
    rf"(?:(?:{_OWN} )?applications?{_NOT_SOFTWARE}|applications? (?:materials|documents)"
    r"|cover letters?"
    r"|cvs?|resumes?|motivation letters?|candidatures?|lettres? de motivation|bewerbungen"
    r"|bewerbungsunterlagen|anschreiben|lebenslaufe?)"
)

# An explicit prohibition keeps a negated sentence: "do not use AI", "interdit", "nicht erlaubt".
_PROHIBITS = re.compile(
    r"\b(?:(?:do|does|did|will|would|must|should|may|can|are|is|were) not|don t|doesn t|won t"
    r"|can t|cannot|never) (?:allow|permit|use|using|utilize|utilise|rely on|accept|tolerate"
    r"|be allowed|be permitted|be used)\b"
    r"|\b(?:not|never) (?:to use|to rely|allowed|permitted|welcome|accepted|expected)\b"
    r"|\b(?:refrain from|avoid|abstain from)(?: [a-z]+)? (?:use|using)\b"
    r"|\b(?:prohibit\w*|forbid\w*|forbidden|banned?|bans|interdit\w*|interdire|interdisons"
    r"|verboten|untersag\w*|verbieten)\b"
    r"|\b(?:ne|n)(?: [a-z]+){0,4}? pas(?: [a-z]+){0,2}? (?:utiliser|utilisez|utilisent|recourir"
    r"|recourez|faire appel|avoir recours)\b"
    r"|\bn (?:utilisez|utilisent) pas\b"
    r"|\b(?:nicht|kein|keine) (?:zu )?(?:nutzen|verwenden|benutzen|einsetzen|erlaubt|gestattet"
    r"|berucksichtigt|akzeptiert|angenommen|zugelassen)\b"
    rf"|\bkeine? {_AI}(?: [a-z]+){{0,2}}? (?:zu )?(?:nutzen|verwenden|benutzen|einsetzen)\b"
    r"|\bpas (?:acceptees?|admises?|prises? en compte|autorisees?|permises?)\b"
)
_NEGATED = re.compile(
    r"\b(?:not|no|never|cannot|without|unable|ne|n|pas|aucun|aucune|sans|non|jamais|t|nicht"
    r"|keine?|kein\w*|nie|niemals|ohne)\b"
)
_HAS_AI = re.compile(rf"\b{_AI}\b")

_PP_EN = (
    r"(?:encouraged|allowed|permitted|welcome|expected|asked|invited|required|prohibited"
    r"|forbidden|banned|discouraged|free|urged|requested|advised)"
)
_PP_FR = r"(?:encourages?|autorises?|invites?|interdits?|tenus|libres|pries|decourages?|bienvenus?)"
_PP_DE = r"(?:ermutigt|ermuntert|erlaubt|gestattet|erwunscht|untersagt|verboten|gebeten|empfohlen)"
_DOC_WRITTEN = r"(?:generated|written|authored|created|produced|drafted)"


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# Sure with no word of the path: the stance of the employer on the candidate's use, a noun that
# names it, or a document of the application written by AI.
_STANCED = _compile(
    (
        rf"\b{_STANCE_EN}{_GA}{{0,3}}? {_CAND_EN}{_G}{{0,3}}? {_USE_EN}{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_CAND_EN}{_GA}{{0,2}}? (?:are|is|will be)(?: not)? {_PP_EN}{_G}{{0,3}}? {_USE_EN}"
        rf"{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_STANCE_FR}{_GA}{{0,3}}? {_CAND_FR}{_G}{{0,3}}? {_USE_FR}{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_CAND_FR}{_GA}{{0,2}}? (?:sont|est|seront)(?: pas)? {_PP_FR}{_G}{{0,3}}? {_USE_FR}"
        rf"{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_STANCE_DE}{_GA}{{0,3}}? {_CAND_DE}{_G}{{0,3}}? {_AI_USED}{_G}{{0,3}}? {_USE_DE}\b",
        rf"\b{_CAND_DE}{_GA}{{0,2}}? (?:werden|sind|wird|ist) {_PP_DE}{_G}{{0,3}}? {_AI_USED}"
        rf"{_G}{{0,3}}? {_USE_DE}\b",
    )
)
_NAMED = _compile(
    (
        rf"\b{_CAND}(?: s)? {_AI_TOOLS} (?:usage|use|policy|policies|guidelines|guidance|rules)\b",
        rf"\b{_CAND_EN}(?: s)? (?:use|usage) of {_AI}\b",
        rf"\b(?:use|usage|using) of {_AI_TOOLS} (?:by|from|among|amongst) (?:our |all |the )?"
        rf"{_CAND_EN}\b",
        rf"\b{_AI_TOOLS} (?:usage|use) (?:by|from|among|amongst|of) (?:our |all |the )?"
        rf"{_CAND_EN}\b",
        rf"\b{_AI_TOOLS} (?:policy|policies|guidelines|guidance|rules|expectations) (?:for|by|of"
        rf"|from) (?:our |all |the )?{_CAND_EN}\b",
        rf"\b(?:policy|policies|guidance|guidelines|rules|expectations|principles|advice|tips"
        rf"|information|statement|stance) (?:for|to|on|about) (?:our |all |the )?{_CAND_EN} (?:on"
        rf"|about|regarding|around|concerning|for) (?:the )?(?:use of |using |usage of )?{_AI}\b",
        rf"\b(?:{_AI_TOOLS} {_DOC_WRITTEN}|{_DOC_WRITTEN} (?:by|with|using) {_AI_TOOLS})"
        rf" {_DOCS}\b",
        rf"\b{_DOCS} (?:(?:that |which )?(?:are|were|is|was) )?{_DOC_WRITTEN} (?:by|with|using)"
        rf" {_AI_TOOLS}\b",
        rf"\b(?:utilisation|usage|emploi|recours) (?:de l |de |d |a l )?{_AI}(?: [a-z]+)?"
        rf" (?:par|chez) (?:les |nos |tous les )?{_CAND_FR}\b",
        rf"\b(?:politique|charte|regles|consignes|recommandations|lignes directrices|guide"
        rf"|conseils){_GA}{{0,6}}? {_AI}{_GA}{{0,1}}? (?:pour|aux|a l intention des|destinees? aux)"
        rf" (?:les )?{_CAND_FR}\b",
        rf"\b(?:candidatures?|cv|lettres? de motivation) (?:generee?s?|redigee?s?|ecrite?s?"
        rf"|produite?s?|creee?s?) (?:par|avec) (?:une? |l |la )?{_AI}\b",
        rf"\b(?:nutzung|verwendung|einsatz|gebrauch) (?:von |der )?{_AI}(?: tools| werkzeugen)?"
        rf" (?:durch|seitens|von|bei|fur) (?:unsere |alle )?{_CAND_DE}\b",
        rf"\b{_AI}(?: tools| werkzeuge)? (?:nutzung|richtlinie|richtlinien|regeln|leitfaden"
        rf"|einsatz|verwendung)(?: (?:fur|von|durch|bei|im))? {_CAND_DE}\b",
        rf"\b(?:(?:von |mit |durch )?{_AI} (?:generierte|erstellte|geschriebene|verfasste)\w*)"
        rf" (?:bewerbung(?:en)?|bewerbungsunterlagen|anschreiben|lebenslaufe?)\b",
    )
)
# A policy heading: a word of policy, then AI, and the narrow path of the application.
_POLICIES = _compile(
    (
        r"\b(?:policy|policies|guidance|guidelines|rules|expectations|principles|stance|position"
        r"|approach|statement|guide)(?: (?:for|on|about|regarding|around|concerning|to))"
        rf"(?: (?:the|our|using|use|usage|of|responsible|responsibly|ethical|candidates"
        rf"|applicants|your))*? {_AI}\b",
        rf"\b{_AI_TOOLS} (?:policy|policies|guidelines|guidance|rules|expectations)"
        r" (?:for|in|during|on|about|regarding|around)\b",
        r"\b(?:politique|charte|regles|consignes|recommandations|lignes directrices|guide"
        r"|conseils)(?: (?:sur|concernant|relative a|relatives a|relatif a|pour|en matiere d"
        r"|a propos de|autour de))(?: (?:l|d|de|du|la|le|les|notre|votre|utilisation|usage|emploi"
        rf"|recours|a))*? {_AI}\b",
        r"\b(?:richtlinie|richtlinien|regeln|leitfaden|hinweise|grundsatze|haltung|umgang"
        r"|position|regelung|informationen|empfehlungen)(?: (?:zur|zum|zu|fur|uber|beim|bei|im"
        r"|mit|der|die|den|dem|nutzung|verwendung|einsatz|von|unserer|unsere|unseren"
        rf"))*? {_AI}\b",
    )
)
# Sure with a word of the path in the sentence: the candidate uses AI, or AI is allowed or not in
# a step of it.
_USERS = _compile(
    (
        rf"\b{_CAND_EN}{_G}{{0,4}}? {_USE_EN}{_G}{{0,4}}? {_AI_USED}\b",
        r"\byou(?: (?:may|can|could|should|must|are|re|do not|don t|cannot|can not|can t"
        r"|may not|must not|should not|will be)){1,2}(?: not| also| freely| responsibly| allowed"
        rf"| permitted| welcome| free| encouraged| expected| asked| to)*{_G}{{0,2}}? {_USE_EN}"
        rf"{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b(?:^|please |that you )(?:do not|don t|never|avoid|refrain from) (?:ever |please )?"
        rf"{_USE_EN}{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_AI_USED}(?: (?:tools?|assistants?))?{_G}{{0,2}}? (?:is|are|may be|can be|will be"
        r"|must not be|will not be|should not be|cannot be)"
        r" (?:not )?(?:allowed|permitted|prohibited|forbidden|banned|welcome|encouraged"
        r"|discouraged|accepted)\b",
        rf"\b{_CAND_FR}{_G}{{0,4}}? {_USE_FR}{_G}{{0,4}}? {_AI_USED}\b",
        r"\bvous (?:pouvez|devez|ne pouvez|ne devez|pourrez|etes autorises a|etes libres de"
        rf"|avez le droit de){_G}{{0,2}}? (?:pas )?{_USE_FR}{_G}{{0,4}}? {_AI_USED}\b",
        rf"\b{_CAND_DE}{_G}{{0,6}}? {_AI_USED}{_G}{{0,3}}? {_USE_DE}\b",
        rf"\bsie (?:durfen|konnen|sollten|sollen|mussen){_G}{{0,2}}? {_AI_USED}{_G}{{0,5}}?"
        rf" {_USE_DE}\b",
    )
)


def says(said: str) -> bool:
    """Whether a sentence, as `signals.words` reads it, says something about the candidates'
    own use of AI in the application, the assessments or the interviews."""
    if _HAS_AI.search(said) is None:
        return False
    if _NEGATED.search(said) and _PROHIBITS.search(said) is None:
        return False
    if any(p.search(said) for p in _STANCED) or any(p.search(said) for p in _NAMED):
        return True
    if _PROCESS_APP.search(said) and any(p.search(said) for p in _POLICIES):
        return True
    return _PROCESS.search(said) is not None and any(p.search(said) for p in _USERS)
