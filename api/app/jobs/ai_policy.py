"""What a posting says about the candidates' own use of AI (jobs J40; SPEC-jobs, "Before the
click"): `says(said)`, the rule of the signal `ai_policy`, on one sentence as `signals.words` reads
it. A pure function of the sentence, linear in its length (every gap is bounded).

A sentence is the signal when it holds all three, each a closed list of words in English, French
and German (the lists are read out in tasks/jobs/todo.md, J40):

1. an AI use: an AI term within five words of a word of use or of rule ("use AI", "AI tools",
   "AI policy", "l'utilisation de l'IA", "KI ... nutzen"); a bare AI term is a topic;
2. the candidate's own path: a step the person owns ("your application", "leur CV", "Ihre
   Bewerbung"), a step after a word of time ("during the interview", "pendant l'entretien", "im
   Vorstellungsgespräch"), a process of applying ("the hiring process", "processus de
   candidature", "Bewerbungsprozess"), a rule for candidates ("AI policy for candidates"), the
   candidates' use as a noun ("candidates' use of AI") or a document made by AI ("AI generated
   cover letters");
3. nobody else's use: not the employer, its people or its tools using AI ("we use", "our
   platform screens", "the review of your application"), not its product for candidates ("the AI
   interviewer", "helps applicants use AI"), not its calls and messages, not a skill ("experience
   using AI") and not a process the role works on ("improve the hiring process").

A sentence that negates is silent unless it states the rule ("do not use", "interdit", "nicht
erlaubt"). Whatever the lists do not name is silent: precision first, a miss is a sentence the
person reads herself.
"""

import re

_GAP = r"(?: [a-z0-9+]+)"
_AI = (
    r"(?:(?:generative[nr]?|gen) )?(?:ai|a i|genai|artificial intelligence|llms?"
    r"|large language models?|chatgpt|chat gpt|gpt(?: ?[0-9]+o?)?|copilot|claude|gemini"
    r"|ia(?: generative)?|intelligence artificielle(?: generative)?|ki"
    r"|kunstliche[nr]? intelligenz)"
)
_USE = (
    r"(?:use|uses|used|using|usage|utili[sz](?:e|es|ed|ing|ation)|rely|relying|relied|leverage"
    r"|leveraging|help|helps|helped|helping|tools?|tooling|assistance|assistants?|assisted"
    r"|generated|written|created|drafted|produced|authored|policy|policies|guidance|guidelines?"
    r"|rules|disclose|disclosed|disclosure"
    r"|utilis(?:er|ez|ent|ons|ant|ation|e|es|ee|ees)|recours|recourir|recourez|recourent|aide de"
    r"|aide d|outils?|assistante?s?|assistee?s?|generee?s?|redigee?s?|ecrite?s?|politique|charte"
    r"|regles|consignes|lignes directrices|recommandations|declarer|indiquer|preciser|signaler"
    r"|nutzen|nutzt|nutzung|genutzt|benutzen|benutzt|benutzung|verwenden|verwendet|verwendung"
    r"|einsetzen|eingesetzt|einsatz|einzusetzen|gebrauch|umgang|hilfe von|mithilfe|gestutzte?n?"
    r"|werkzeuge?n?|hilfsmittel|generierte?n?|erstellte?n?|geschrieben|verfasste?n?"
    r"|richtlinien?|leitfaden|regeln|regelungen|hinweise|angeben|offenlegen)"
)

_CAND_EN = r"(?:candidates?|applicants?|job seekers?|jobseekers?)"
_CAND_FR = r"(?:candidate?s?|postulante?s?)"
_CAND_DE = r"(?:bewerber(?:n|in|innen)?|bewerbende[nr]?|kandidat(?:en|innen|in)?)"

# "application" as software is not the person's application.
_SOFTWARE = (
    r"(?:development|dev|code|security|performance|layers?|servers?|stack|architecture|logic|data"
    r"|software|platform|framework|engineering|programming|features?|support|testing"
    r"|infrastructure|monitoring|migration|modernization|modernisation|owners?|engineers?"
    r"|developers?|teams?|portfolio|landscape|lifecycle)"
)
_RULE_EN = (
    r"(?:policy|policies|guidance|guidelines?|guide|rules|principles|expectations|advice|tips|faq"
    r"|statement)"
)
_RULE_FR = (
    r"(?:politique|charte|regles|consignes|recommandations|lignes directrices|guide|conseils)"
)
_RULE_DE = (
    r"(?:richtlinien?|regeln|regelungen|leitfaden|hinweise|grundsatze|empfehlungen|informationen)"
)


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# List 1: an AI term and a word of use or of rule, at most five words apart.
_AI_USE = re.compile(rf"\b{_AI}{_GAP}{{0,5}}? {_USE}\b|\b{_USE}{_GAP}{{0,5}}? {_AI}\b")

# List 2: the candidate's own path, each part in English, French and German.
_OWN_STEP = _compile(
    r"\b(?:your|their) (?:(?:own|first|written|technical|job|final|online|video|live|next"
    r"|whole|entire) )?(?:applications?|cover letters?|motivation letters?|resumes?|cvs?"
    r"|curriculum vitae|interviews?|assessments?|answers|responses|take home (?:exercises?"
    rf"|assignments?|tests?|tasks?)|coding (?:tests?|challenges?|exercises?))\b"
    rf"(?! (?:of|{_SOFTWARE})\b)",
    r"\b(?:votre|vos|leur|leurs|ta|tes|ton) (?:(?:propre|premier|premiere|prochain|prochaine) )?"
    r"(?:candidatures?|cv|curriculum vitae|lettres? de motivation|entretiens?|reponses"
    r"|dossiers? de candidature|tests? techniques?)\b(?! (?:de|des|du)\b)",
    r"\b(?:ihre|ihrer|ihren|ihrem|ihr|deine|deiner|deinen|deinem|dein)(?: eigene[nmrs]?)?"
    r" (?:bewerbung\w*|lebenslauf\w*|anschreiben|motivationsschreiben|vorstellungsgesprach\w*"
    r"|antworten)\b(?! (?:unserer|unseres|der|des|von)\b)",
)
_TIMED_STEP = _compile(
    r"\b(?:(?:during|throughout|in|at)(?: (?:the|your|our|any|all|each|every|this|these|their|a"
    r"|an))?|for (?:the|your|this|each|every|any))(?: (?:technical|coding|live|final|first|second"
    r"|onsite|remote|video|online|virtual|behavioral|behavioural|job))? (?:interviews?"
    r"|assessments?|(?:coding|technical) (?:tests?|challenges?|exercises?|assessments?"
    r"|interviews?)|take home (?:exercises?|assignments?|tests?|tasks?|challenges?|projects?)"
    r"|live coding(?: (?:sessions?|interviews?|exercises?))?)\b(?! (?:of|with)\b)",
    rf"\b(?:in|during|throughout|for) the application\b(?! (?:of|{_SOFTWARE})\b)",
    r"\b(?:pendant|durant|lors|au cours|tout au long|dans|pour)(?: (?:de|du|des|d))?(?: (?:l|la"
    r"|le|les|vos|votre|nos|notre|cet|ce|chaque|ces))? (?:entretiens?|evaluations?"
    r"|tests? techniques?|exercices? techniques?|etudes? de cas|candidature)\b"
    r"(?! (?:de|des|du)\b)",
    r"\b(?:im|beim|wahrend des|wahrend der|wahrend eines|in der|in den|in einem|bei der|fur die"
    r"|fur das|fur den)(?: (?:technischen|personlichen|ersten|zweiten))? (?:interviews?"
    r"|bewerbung|coding test|programmiertest|assessments?|fallstudie|probearbeit\w*"
    r"|vorstellungsgesprach\w*)\b(?! (?:unserer|unseres|der|des|von)\b)",
)
_PROCESS = _compile(
    r"\b(?:(?:application|applying) (?:process(?:es)?|stages?|phases?)|(?:the|your|this|any"
    r"|every|each|whole|entire)(?: (?:whole|entire|full))? (?:hiring|recruitment|recruiting"
    r"|selection|interview|interviewing) (?:process(?:es)?|stages?|phases?))\b"
    r"(?! (?:of|for (?:our|their) (?:clients|customers|partners))\b)",
    r"\b(?:processus de candidature|phase de candidature|entretiens? d embauche|(?:le|du|au|ce"
    r"|un|votre|tout le|l ensemble du) processus de (?:recrutement|selection))\b"
    r"(?! (?:de|des|du|d)\b)",
    r"\b(?:vorstellungsgesprach\w*|bewerbungsgesprach\w*|bewerbungsprozess\w*"
    r"|bewerbungsverfahren\w*|auswahlverfahren\w*|einstellungsverfahren\w*|bewerbungsphase\w*)\b"
    r"(?! (?:unserer|unseres|der|des|von|ihrer)\b)",
)
# A rule that addresses candidates, and not candidates together with the staff.
_RULE_FOR = _compile(
    rf"\b{_AI}(?: tools?)?(?: (?:use|usage))? {_RULE_EN} (?:for|to) (?:all |our |the )?"
    rf"{_CAND_EN}\b(?! (?:and|or) (?:employees|staff|team members|colleagues|contractors"
    r"|partners|vendors|suppliers|customers|clients)\b)",
    rf"\b{_RULE_EN} (?:on|about|regarding|around|concerning|for) (?:the )?(?:(?:use|usage) of"
    rf" |using )?{_AI}(?: tools?)?(?: (?:use|usage))? (?:for|to|by) (?:all |our |the )?"
    rf"{_CAND_EN}\b",
    rf"\b{_RULE_EN} (?:for|to) (?:all |our |the )?{_CAND_EN} (?:on|about|regarding|around"
    rf"|concerning|for) (?:the )?(?:(?:use|usage) of |using )?{_AI}\b",
    rf"\b{_RULE_FR}(?: (?:de l|de|d|du|sur l|sur|concernant l|en matiere d|relative a l))?"
    rf"(?: (?:utilisation|usage) (?:de l|d|de))? {_AI} (?:pour|aux|a l intention des"
    rf"|destinee aux|destinees aux) (?:les |nos |tous les )?{_CAND_FR}\b",
    rf"\b{_AI} {_RULE_DE} (?:fur|an) (?:alle |unsere |die )?{_CAND_DE}\b",
    rf"\b{_RULE_DE} (?:zur|zum|zu|fur|uber)(?: (?:nutzung|verwendung|einsatz|umgang))?"
    rf"(?: (?:von|mit))? {_AI}(?: (?:nutzung|verwendung|einsatz))? (?:fur|an|durch)"
    rf" (?:alle |unsere |die )?{_CAND_DE}\b",
)
_THEIR_USE = _compile(
    rf"\b{_CAND_EN}(?: s)? (?:{_AI}(?: tools?)? (?:use|usage)|use of|usage of|usage)\b",
    rf"\b(?:use|usage) of {_AI}(?: tools?)? (?:by|from|among|amongst) (?:our |all |the |any )?"
    rf"{_CAND_EN}\b",
    rf"\b(?:utilisation|usage|emploi|recours) (?:de l |de |d |a l |a )?(?:outils d )?{_AI}"
    rf"(?: [a-z]+)? (?:par|chez) (?:les |nos |tous les )?{_CAND_FR}\b",
    rf"\b(?:nutzung|verwendung|einsatz|gebrauch) (?:von |der )?{_AI}(?: (?:tools|werkzeugen))?"
    rf" (?:durch|seitens|von|bei) (?:unsere |alle |den |die )?{_CAND_DE}\b",
    rf"\b{_AI}(?: tools)? (?:nutzung|einsatz|verwendung) (?:durch|von|bei|seitens)"
    rf" (?:unsere |alle |den |die )?{_CAND_DE}\b",
)
_DOCS_EN = (
    r"(?:applications?|cover letters?|motivation letters?|resumes?|cvs?|answers|responses"
    r"|application (?:materials|documents|answers))"
)
_AI_MADE = _compile(
    rf"\b{_AI}(?: tools?)? (?:generated|written|created|drafted|produced|authored|assisted)"
    rf" {_DOCS_EN}\b(?! {_SOFTWARE}\b)",
    rf"\b{_DOCS_EN}(?: (?:that|which))?(?: (?:are|were|is|was|have been|has been|been))?"
    r"(?: (?:clearly|obviously|entirely|fully|partly|largely|mostly|wholly))? (?:generated"
    rf"|written|created|drafted|produced|authored) (?:by|with|using) (?:an? |the )?{_AI}\b",
    r"\b(?:candidatures?|cv|lettres? de motivation|reponses|dossiers? de candidature)"
    r"(?: [a-z]+)? (?:generee?s?|redigee?s?|ecrite?s?|produite?s?|creee?s?|assistee?s?)"
    rf" (?:par|avec|a l aide de|a l aide d|grace a)(?: (?:une?|l|la|des|d))?(?: outils? d)?"
    rf" {_AI}\b",
    rf"\b(?:(?:von|mit|durch) )?{_AI}(?: tools)? (?:generierte|erstellte|geschriebene"
    r"|verfasste)\w* (?:bewerbung\w*|anschreiben|lebenslauf\w*|motivationsschreiben|unterlagen"
    r"|antworten)\b",
)
_PATH = (_OWN_STEP, _TIMED_STEP, _PROCESS, _RULE_FOR, _THEIR_USE, _AI_MADE)

# List 3: someone else's use. The employer, its people or its tools as the one who uses AI.
_EMPLOYER = _compile(
    r"\b(?:we|we ll|we re|we ve|our(?: [a-z]+)? (?:team|teams|recruiters?|recruiting"
    r"|recruitment|talent|people|company|platform|product|system|systems|tool|tools|software"
    r"|ats|models?|process|engine|algorithms?))(?: (?:may|might|will|can|could|also|sometimes"
    r"|currently|do|does|now|increasingly|already|are|is|have|has|routinely|often|actively|not"
    r"|never|always|only|responsibly|carefully|partly|occasionally)){0,4} (?:use|uses|used|using"
    r"|leverage|leverages|leveraging|employ|employs|utili[sz]e|utili[sz]es|rely|relies|deploy"
    r"|deploys|analy[sz]e|analy[sz]es|screen|screens|process|processes)\b",
    r"\b(?:nous|notre(?: [a-z]+)? (?:equipe|plateforme|outil|produit|systeme|solution|logiciel"
    r"|ats|processus|ia)|nos(?: [a-z]+)? (?:equipes|recruteurs|outils|systemes|algorithmes))"
    r"(?: (?:pouvons|pourrons|pourrions|allons|avons|sommes|ne|n|y|en|aussi|egalement|parfois))"
    r"{0,4} (?:utilisons|utiliser|utilise|utilisent|exploitons|exploiter|exploite|analysons"
    r"|analyser|analyse|analysent|appuyons|recourons|recourir|trions|evaluons|deployons|aide"
    r"|aident)\b",
    r"\b(?:wir|unser\w*(?: [a-z]+)? (?:team|plattform|system|tool|tools|software|produkt"
    r"|recruiting|recruiter\w*|personalabteilung|ki|ats))(?: (?:konnen|werden|durfen|mochten"
    r"|auch|ggf|gegebenenfalls|teilweise|bereits|nicht)){0,3} (?:nutzen|verwenden|benutzen"
    r"|setzen|einsetzen|analysieren|prufen|sichten|werten|hilft|helfen|unterstutzt"
    r"|unterstutzen)\b",
    r"\b(?:nutzen|verwenden|benutzen|setzen|analysieren|prufen|sichten) wir\b",
)
# The employer acts on the applications with AI: a review, a screening, a passive.
_ACTS_ON = _compile(
    r"\b(?:review|reviewing|screening|assessment|evaluation|analysis|processing|ranking|scoring"
    r"|sorting|reading|matching|triage|shortlisting|filtering) of (?:your |the |all |our"
    r" |incoming |each |every |submitted |candidates )?(?:applications?|cvs?|resumes?|answers"
    r"|responses|cover letters?|profiles?|application materials)\b",
    r"\bto (?:screen|assess|evaluate|analy[sz]e|process|rank|score|sort|match|parse|triage"
    r"|shortlist|filter) (?:your |the |all |incoming |each |every |submitted |candidates )?"
    r"(?:applications?|cvs?|resumes?|answers|responses|cover letters?|profiles?)\b",
    r"\b(?:processed|screened|reviewed|analy[sz]ed|ranked|scored|sorted|parsed|evaluated"
    rf"|assessed|transcribed|recorded|matched|shortlisted|filtered) (?:by|with|using|through"
    rf"|via){_GAP}{{0,3}}? {_AI}\b",
    r"\b(?:analyse|examen|tri|evaluation|traitement|lecture|selection|preselection) (?:de|des"
    r"|du|d)(?: (?:votre|vos|la|les|l))? (?:candidatures?|cv|dossiers?)\b",
    r"\bpour (?:analyser|examiner|trier|evaluer|traiter|lire|preselectionner|selectionner)"
    r" (?:votre |vos |les |la |des |l )?(?:candidatures?|cv|dossiers?)\b",
    r"\b(?:traitee?s?|analysee?s?|examinee?s?|triee?s?|evaluee?s?|lue?s?|preselectionnee?s?)"
    rf" (?:par|avec|a l aide de|a l aide d|au moyen de|au moyen d|grace a){_GAP}{{0,3}}? {_AI}\b",
    r"\b(?:prufung|sichtung|analyse|bewertung|auswertung|bearbeitung|vorauswahl|verarbeitung)"
    r" (?:ihrer|ihre|der|von|aller|eingehender)(?: [a-z]+)? (?:bewerbung\w*|unterlagen"
    r"|lebenslauf\w*)\b",
    rf"\b(?:bewerbung\w*|unterlagen|lebenslauf\w*){_GAP}{{0,2}}? (?:zu )?(?:prufen|sichten"
    r"|analysieren|bewerten|auswerten|bearbeiten|vorsortieren|sortieren|filtern|verarbeiten)\b",
    rf"\b(?:mithilfe von|mit hilfe von|mit|durch|von)(?: [a-z]+)? {_AI}{_GAP}{{0,3}}?"
    r" (?:gepruft|gesichtet|analysiert|bewertet|ausgewertet|bearbeitet|verarbeitet|vorsortiert"
    r"|sortiert|gefiltert)\b",
)
# The employer's product: "our AI", "the AI interviewer", "l'IA de notre plateforme".
_NOT_PRODUCT = (
    rf"(?:{_RULE_EN}|{_RULE_FR}|{_RULE_DE}|approach|stance|position|use|usage|utilisation|nutzung"
    r"|einsatz)"
)
_PRODUCT = _compile(
    rf"\b(?:our|notre|nos|unsere?[mnrs]?)(?: (?!{_NOT_PRODUCT}\b)[a-z]+)? {_AI}\b"
    rf"(?! {_NOT_PRODUCT}\b)",
    rf"\b{_AI}(?: (?:powered|driven|based))? (?:interviewers?|coach(?:es)?|recruiters?|screeners?"
    r"|avatars?|chatbots?|bots?|recruteurs?|interviewerin)\b",
    r"\b(?:recruteurs?|coach|chatbot|avatar|interviewer|recruiter) (?:virtuel )?(?:ia|ki)\b",
    rf"\b{_AI} (?:of our|de notre|de nos|de la plateforme|unserer|der plattform|of the platform)\b",
)
# The employer's product for candidates: it helps, lets or enables them.
_HELPS = _compile(
    r"\b(?:help|helps|helping|helped|let|lets|letting|enable|enables|enabling|empower|empowers"
    r"|empowering)(?: (?:our|the|all|more|millions of|thousands of))? (?:candidates|applicants"
    r"|job seekers|jobseekers|users|talent)\b",
    r"\b(?:aide|aident|aider|aidons|accompagne|accompagnent|accompagnons)(?: (?:les|nos|des|aux))?"
    r" (?:candidate?s|utilisateurs|chercheurs d emploi)\b",
    r"\b(?:hilft|helfen|unterstutzt|unterstutzen)(?: (?:den|unseren|allen|vielen))?"
    r" (?:bewerber\w*|kandidat\w*|nutzer\w*|jobsuchende\w*)\b",
)
_MESSAGES = _compile(
    rf"\b{_AI}(?: (?:generated|powered|driven|assisted))? (?:calls?|phone calls?|voice calls?"
    r"|messages?|text messages?|texts|sms|e mails?|emails?|voicemails?|outreach)\b",
    r"\b(?:calls?|messages?|texts|sms|e mails?|emails?)(?: (?:that are|which are|are))?"
    rf" (?:generated|made|sent|placed|written) (?:by|with|using) (?:an? |the )?{_AI}\b",
    r"\b(?:appels?|messages?|sms|e mails?|courriels?) (?:generes?|automatises?|passes?|envoyes?"
    rf"|ecrits?|rediges?)(?: [a-z]+)? (?:par|avec|a l aide de|a l aide d)(?: (?:l|une|des|d))?"
    rf" {_AI}\b",
    rf"\b{_AI}(?: (?:generierte\w*|gestutzte\w*))? (?:anruf\w*|nachricht\w*|e mails?|sms)\b",
)
# AI as the person's skill: a skill word, then at most two words, then the use or the AI.
_SKILL = _compile(
    r"\b(?:experience|experienced|skilled|skills?|proficient|proficiency|ability|abilities"
    r"|expertise|knowledge|background|familiar|familiarity|comfortable|fluent|fluency|savvy"
    r"|track record|competence|competency|hands on|experiences|competences|maitrise|connaissances?"
    rf"|aisance|savoir faire|erfahrung\w*|kenntnis\w*|kompetenz\w*|vertraut\w*|versiert)"
    rf"{_GAP}{{0,2}}? (?:{_USE}|{_AI})\b",
)
# AI as a topic the person talks about in the interview or the letter, not a use in it.
_TOPIC = _compile(
    r"\b(?:discuss|discussing|talk about|talk through|talking about|chat about|conversation about"
    r"|ask how|ask about|ask you how|ask you about|tell us how|tell us about|describe|describing"
    r"|walk us through|show us how|demonstrate|demonstrating|examples? of|projects? where"
    r"|parler|parlerons|parlons|discuter|discuterons|echanger|echangerons|aborder|aborderons"
    r"|decrire|decrivez|montrez nous|racontez nous|sprechen|besprechen|diskutieren|unterhalten"
    rf"|erzahlen sie|beschreiben sie|zeigen sie uns){_GAP}{{0,5}}? (?:{_USE}|{_AI})\b",
)
# A hiring process the role improves is a task of the role, not the person's own path.
_TASK = _compile(
    r"\b(?:improv|optimi[sz]|streamlin|automat|moderni[sz]|transform|enhanc|redesign|reinvent"
    r"|revolutioni[sz]|digiti[sz]|simplif|accelerat)\w* (?:the |our |their |its |your |a )?"
    r"(?:[a-z]+ )?(?:hiring|recruitment|recruiting|selection|application|interview|interviewing)"
    r" (?:process(?:es)?|experience|stages?|workflows?|funnel|pipeline)\b",
    r"\b(?:amelior|optimis|automatis|modernis|transform|fluidifi|digitalis|simplifi|acceler"
    r"|repens|revolution)\w* (?:le |la |les |nos |notre |leurs |leur |des |du |vos |votre )?"
    r"(?:processus de (?:recrutement|selection|candidature)|recrutement|parcours candidats?"
    r"|experience candidats?)\b",
    rf"\b(?:bewerbungsprozess\w*|auswahlverfahren\w*|bewerbungsverfahren\w*|recruiting\w*"
    rf"|einstellungsprozess\w*){_GAP}{{0,3}}? (?:zu )?(?:verbessern|optimieren|automatisieren"
    r"|modernisieren|digitalisieren|vereinfachen|beschleunigen|transformieren)\b",
    r"\b(?:verbesser|optimier|automatisier|modernisier|digitalisier|vereinfach|beschleunig"
    r"|revolutionier)\w* (?:den |die |das |unsere[nm]? |ihre[nm]? )?(?:[a-z]+ )?"
    r"(?:bewerbungsprozess\w*|auswahlverfahren\w*|bewerbungsverfahren\w*|recruiting\w*)\b",
)
_NOT_THEIRS = (_EMPLOYER, _ACTS_ON, _PRODUCT, _HELPS, _MESSAGES, _SKILL, _TOPIC, _TASK)

# A negated sentence states a rule only through these: "do not use", "interdit", "nicht erlaubt".
_NEGATED = re.compile(
    r"\b(?:not|no|never|cannot|without|unable|ne|n|pas|aucun|aucune|sans|non|jamais|t|nicht"
    r"|keine?|kein\w*|nie|niemals|ohne)\b"
)
_KEEPS = re.compile(
    r"\b(?:(?:do|does|did|will|would|must|should|may|can|are|is|were) not|don t|doesn t|won t"
    r"|can t|cannot|never) (?:be )?(?:allow|allowed|permit|permitted|use|using|utilize|utilise"
    r"|rely on|accept|accepted|tolerate|tolerated|used|consider|considered|reviewed)\b"
    r"|\b(?:not|never) (?:to use|to rely|allowed|permitted|welcome|accepted|expected|considered"
    r"|tolerated|acceptable|permissible|tolerable)\b"
    r"|\b(?:refrain from|avoid|abstain from)(?: [a-z]+)? (?:use|using)\b"
    r"|\b(?:prohibit\w*|forbid\w*|forbidden|banned?|bans|interdit\w*|interdire|interdisons"
    r"|verboten|untersag\w*|verbieten)\b"
    r"|\b(?:ne|n)(?: [a-z]+){0,4}? pas(?: [a-z]+){0,2}? (?:utiliser|utilisez|utilisent|recourir"
    r"|recourez|faire appel|avoir recours)\b"
    r"|\bn (?:utilisez|utilisent|utilisons) pas\b"
    r"|\bpas (?:acceptee?s?|admise?s?|prise?s? en compte|autorisee?s?|permise?s?|toleree?s?)\b"
    r"|\b(?:nicht|kein|keine) (?:zu )?(?:nutzen|verwenden|benutzen|einsetzen|erlaubt|gestattet"
    r"|berucksichtigt|akzeptiert|angenommen|zugelassen|erwunscht)\b"
    rf"|\bkeine? {_AI}(?: [a-z]+){{0,3}}? (?:zu )?(?:nutzen|verwenden|benutzen|einsetzen)\b"
    r"|\b(?:nutzen|verwenden|benutzen|setzen) sie(?: [a-z]+){0,4}? (?:keine?|nicht)\b"
)


def says(said: str) -> bool:
    """Whether a sentence, as `signals.words` reads it, says something about the candidates'
    own use of AI in the application, the assessments or the interviews."""
    if _AI_USE.search(said) is None:
        return False
    if not any(p.search(said) for part in _PATH for p in part):
        return False
    if any(p.search(said) for part in _NOT_THEIRS for p in part):
        return False
    return _NEGATED.search(said) is None or _KEEPS.search(said) is not None
