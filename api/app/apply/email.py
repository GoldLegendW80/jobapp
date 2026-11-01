"""Applications by email: posting addresses only, fixed document versions, no send authority.

Which addresses of a posting's text an application by email may use, failing closed:

1. The text is folded before it is read (`_clean`, `_folded`): NFKC, quotes and apostrophes
   folded to `'`, hidden characters dropped (soft hyphens, zero width characters), and for the
   words, case, accents and lookalike letters folded as the never apply rules read them.
2. An address is usable only when it follows an explicit positive instruction to apply by email
   ("send your CV to", "apply by email to", "envoyez votre candidature à", "postulez par mail
   à"), and no negation or prohibition word appears in its block: the paragraph that holds it,
   the lead line of the list it belongs to (or the open line just above, one that ends without
   . ! ?), and the headings above it (every Markdown level still open, and the run of plain
   headings just above). "Do not hesitate" and "n'hésitez pas" invite, they do not negate.
   Pasted HTML is read as text first: block tags become line breaks, `<li>` a list item.
3. The global rule: if any sentence anywhere in the text holds both a negation or prohibition
   word and an e-mail word ("email", "e-mail", "mail", "courriel", "adresse électronique"),
   every instructed address of the text is unsure ("UPDATE: we no longer accept applications
   by email" on top of an old "Send your CV to ..."). A sentence ends at . ! ? or a blank line.
4. Anything else is unsure, never usable: an instructed address in a block with a negation is
   shown to the person to decide (Review first) and handed over in Full auto
   (`email_unsure`); an address outside any instruction (a signature, a footer, a contact line)
   is nothing. An address that appears in any negated block is unsure everywhere; addresses
   are compared casefolded.

Declared open points: negations outside English, French, German and Dutch, and wording with
no negation word ("This address is obsolete", "Only applications via the form are read") are
not read; an instruction quoted as a scam example stays usable (Full auto still needs the
company's own domain).
"""

import hashlib
import html
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import full_auto, gate, guardrails, run, states
from app.apply.models import AnswerSource, Application, ApplicationAnswer, Mode, State
from app.jobs.models import Job, JobStatus
from app.jobs.never_apply import fold
from app.mail import domains, drafts, hosts
from app.mail.draft_models import MailDraft
from app.mail.google import SCOPE_SEND
from app.mail.models import AccountStatus, MailAccount

TEXT_LIMIT = 100_000
PARAGRAPH_LIMIT = 4_000
HEADING_LIMIT = 100

_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
# Every quote and apostrophe a keyboard or a converter writes, read as `'`. Translated before
# NFKC, which would turn the acute accent into a space and a combining mark.
_QUOTES = str.maketrans(
    dict.fromkeys(
        "\u2018\u2019\u201a\u201b\u2032\u2035\u02b9\u02bb\u02bc\u02bd\u02c8\u055a\u05f3\u0060\u00b4\uff07",
        "'",
    )
)
# What a reader does not see; tab and line breaks stay.
_HIDDEN = frozenset({"Cf", "Cc", "Cs", "Co", "Cn"})

_DOCUMENT = r"(?:cv|resume|résumé|application|candidature)s?"
_LETTER = (
    r"(?:\s+(?:and|et|\+|&)\s+(?:your\s+|votre\s+|a\s+|une\s+)?"
    r"(?:cover\s+letter|motivation\s+letter|lettre(?:\s+de\s+motivation)?))?"
)
_BY_MAIL = r"(?:\s+(?:by|via|par)\s+(?:e-?mail|mail|courriel))?"
# The allow list of instructions, read on the cleaned text (case aside). The address must come
# right after it, behind an optional "to", "at", "à" or a colon.
_INSTRUCTION = re.compile(
    rf"\b(?:send|e-?mail|submit|forward|envoyez|envoyer|adresser|adressez)\b"
    rf"[^\n.!?]{{0,100}}\b{_DOCUMENT}\b{_LETTER}{_BY_MAIL}"
    rf"|\b(?:apply|postulez|postuler|candidatez){_BY_MAIL}\s+(?:to|at|à|a|par)\b",
    re.IGNORECASE,
)
_RECIPIENT_PREFIX = re.compile(r"\s*(?:(?:to|at|à|a)\b\s*:?\s*|:\s*)?<?", re.IGNORECASE)
_RECIPIENT_JOIN = re.compile(r"\s*>?\s*(?:[,;/]|\b(?:or|and|ou|et)\b)\s*<?", re.IGNORECASE)
# Read on folded text (lower case, no accents, `'` apostrophes, separators as spaces).
_NEGATION = re.compile(
    r"\b(?:not|no|nor|never|neither|none|nothing|nobody|nowhere|cannot|without|instead|rather"
    r"|unless|except\w*|avoid\w*|refrain\w*|dont|wont|cant|forbid\w*|prohibit\w*|ban|banned"
    r"|disallow\w*|ignor\w*|discard\w*|reject\w*|disregard\w*|refus\w*|delet\w*"
    r"|ne|pas|jamais|aucun\w*|sans|non|nul|nulle|ni|plutot|inutile|evit\w*|sauf|lieu"
    r"|interdi\w*|proscri\w*|defendu\w*|rejet\w*|supprim\w*"
    r"|nicht|kein\w*|niet|geen)\b"
    r"|\b(?:don|can|won|isn|aren|doesn|didn|shouldn|mustn|wouldn|couldn|haven|hasn|wasn"
    r"|weren)\s?'?\s?t\b"
    r"|n't\b|\bn\s?'"
)
# "Do not hesitate to send your CV to ..." and "N'hésitez pas à envoyer votre CV à ..." invite.
_INVITATION = re.compile(
    r"\b(?:do\s+not|don\s?'?\s?t|dont)\s+(?:ever\s+)?hesitate\b"
    r"|\bn\s?'?\s?hesit(?:ez|e|er)\s+(?:surtout\s+)?pas\b"
    r"|\bne\s+(?:pas\s+)?hesit(?:ez|er|e)\s+(?:pas\s+)?(?=a\b)"
)
# Read on folded text, addresses removed: the words that name applying by e-mail.
_EMAIL_WORD = re.compile(
    r"\b(?:e\s?)?mail\w*|\bcourriel\w*|\badresses?\b|\baddress(?:es)?\b|\binbox\w*|\bmailbox\w*"
    r"|\bboites?\b|\bmel\b|\bmessagerie\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]")
# Tags of pasted HTML: block tags end a line, a list item starts one, the rest are dropped.
_HTML_TAG = re.compile(
    r"<(/?)(p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|thead|tbody|section|article|header"
    r"|footer|blockquote|hr|span|strong|b|i|u|em|a|font|small)(?=[\s/>])[^<>]*>",
    re.IGNORECASE,
)
_HTML_BLOCK = frozenset(
    {
        "p",
        "div",
        "br",
        "ul",
        "ol",
        "tr",
        "table",
        "thead",
        "tbody",
        "hr",
        *(f"h{n}" for n in range(1, 7)),
    }
)
_LIST_ITEM = re.compile(
    r"[ \t]*(?:[-*+>\u2022\u2023\u2043\u25aa\u25cf\u25e6\u2013\u2014]"
    r"|\(?\d{1,3}[.)]|\(?[a-z][.)])\s",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Reading:
    """What a posting's text says about applying by email: `usable` addresses, and `unsure`
    ones that follow an instruction in a block that may say not to use them."""

    usable: tuple[str, ...] = ()
    unsure: tuple[str, ...] = ()


def _clean(text: str) -> str:
    """NFKC with quotes folded and hidden characters dropped; case and accents kept."""
    plain = unicodedata.normalize("NFKC", text.translate(_QUOTES))
    return "".join(c for c in plain if c in "\t\n\r" or unicodedata.category(c) not in _HIDDEN)


def _plain(text: str) -> str:
    """Pasted HTML read as text: block tags become line breaks, `<li>` a `- ` item."""
    if _HTML_TAG.search(text) is None:
        return html.unescape(text)

    def tag(match: re.Match[str]) -> str:
        closing, name = match.group(1), match.group(2).lower()
        if name == "li":
            return "\n" if closing else "\n- "
        if name in _HTML_BLOCK:
            return "\n"
        return " " if name in {"td", "th", "section", "article", "header", "footer"} else ""

    return html.unescape(_HTML_TAG.sub(tag, text))


def _folded(text: str) -> str:
    return fold(_ADDRESS.sub(" ", text).translate(_QUOTES))


def negated(text: str) -> bool:
    """Whether the text holds a negation or a prohibition, words inside an address and the
    "do not hesitate" invitation aside."""
    return _NEGATION.search(_INVITATION.sub(" ", _folded(text))) is not None


def _forbids_email(paragraphs: list[list[str]]) -> bool:
    """The global rule: whether any sentence of the text holds both a negation and an e-mail
    word. Addresses are removed first, so "jobs@mail.example.org" names nothing."""
    for lines in paragraphs:
        for sentence in _SENTENCE_END.split(_ADDRESS.sub(" ", " ".join(lines))):
            if _EMAIL_WORD.search(_folded(sentence)) and negated(sentence):
                return True
    return False


def _instructed(paragraph: str) -> list[str]:
    """The addresses listed right after an instruction to apply by email."""
    found: list[str] = []
    seen: set[str] = set()
    for instruction in _INSTRUCTION.finditer(paragraph):
        offset = _RECIPIENT_PREFIX.match(paragraph, instruction.end()).end()
        while match := _ADDRESS.match(paragraph, offset):
            try:
                value = drafts.address(match.group().rstrip("."))
            except ValueError:
                break
            if value.casefold() not in seen:
                seen.add(value.casefold())
                found.append(value)
            join = _RECIPIENT_JOIN.match(paragraph, match.end())
            if join is None:
                break
            offset = join.end()
    return found


def _mentioned(paragraph: str) -> set[str]:
    found = set()
    for match in _ADDRESS.finditer(paragraph):
        try:
            found.add(drafts.address(match.group().rstrip(".")).casefold())
        except ValueError:
            continue
    return found


def _paragraphs(text: str) -> list[list[str]]:
    paragraphs: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip():
            paragraphs[-1].append(line.strip())
        elif paragraphs[-1]:
            paragraphs.append([])
    return [p for p in paragraphs if p]


def _heading_level(line: str) -> int:
    """A Markdown heading's level (1 to 6); a plain heading line is 7, the lowest."""
    hashes = len(line) - len(line.lstrip("#"))
    return hashes if 1 <= hashes <= 6 else 7


def _is_heading(lines: list[str]) -> bool:
    line = lines[0]
    return (
        len(lines) == 1
        and _LIST_ITEM.match(line) is None
        and (line.startswith("#") or (len(line) <= HEADING_LIMIT and line[-1] not in ".!?"))
    )


def read(text: str) -> Reading:
    """The usable and the unsure addresses of a posting's text (module docstring). Linear in
    the size of the text."""
    paragraphs = _paragraphs(_clean(_plain(text[:TEXT_LIMIT])))
    forbidden = _forbids_email(paragraphs)
    instructed: list[tuple[str, bool]] = []
    tainted: set[str] = set()
    open_line = lead = ""
    # The headings still open above: each Markdown level, and the run of plain headings.
    headings: list[tuple[int, str]] = []
    after_heading = False
    for lines in paragraphs:
        listed = _LIST_ITEM.match(lines[0]) is not None
        block = [*lines, *(h for _, h in headings), lead if listed else open_line]
        negative = negated("\n".join(block))
        paragraph = " ".join(lines)[:PARAGRAPH_LIMIT]
        if negative:
            tainted |= _mentioned(paragraph)
        instructed += [(value, negative) for value in _instructed(paragraph)]
        # A paragraph whose last line ends without . ! ? leads into the next one ("Please do
        # not:" above a blank line); a list item never leads into a paragraph.
        open_line = "" if listed or lines[-1][-1] in ".!?" else lines[-1]
        if not listed:
            lead = lines[-1]
        heading = _is_heading(lines)
        if heading:
            level = _heading_level(lines[0])
            if not (after_heading and level == 7):
                headings = [h for h in headings if h[0] < level]
            headings = [*headings, (level, lines[0])][-12:]
        after_heading = heading
    usable: dict[str, str] = {}
    unsure: dict[str, str] = {}
    for value, negative in instructed:
        key = value.casefold()
        target = unsure if forbidden or negative or key in tainted else usable
        target.setdefault(key, value)
    usable = {k: v for k, v in usable.items() if k not in unsure}
    return Reading(tuple(usable.values())[:20], tuple(unsure.values())[:20])


def recipients(text: str) -> list[str]:
    """The usable addresses of a posting's text (`read`)."""
    return list(read(text).usable)


def has_own_form(url: str) -> bool:
    """Whether the posting's address is a page whose way to apply is a form: any http(s)
    address other than a company's own site (an ATS's job or form page, a job board, a shared
    host), or an address that is neither http(s) nor empty nor mailto."""
    value = url.strip()
    if not value or value.lower().startswith("mailto:"):
        return False
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return True
    return guardrails.ats_of(value) != "other" or not hosts.is_company_own(parts.hostname)


def addresses(posting: Job, url: str | None = None) -> list[str]:
    """The addresses an application by email may go to. A `mailto:` posting names its own;
    otherwise the posting's text, but only when its address shows no form of its own (empty,
    or a company's own site): a posting on an ATS, a job board or a shared host applies through
    that form, whatever its text says (SPEC-apply A14, "a posting whose way to apply is an
    address"). `url` is the address as read (the forms stub unwrapped in tests)."""
    value = (posting.url or "").strip() if url is None else url.strip()
    if value.lower().startswith("mailto:"):
        try:
            return [drafts.address(value[7:])]
        except ValueError:
            return []
    if has_own_form(value):
        return []
    return recipients(posting.posting_text or "")


def unsure(posting: Job, url: str | None = None) -> list[str]:
    """The addresses that follow an instruction in a block that may say not to use them, on a
    posting that `addresses` reads the text of. Never usable on their own: Review first shows
    them to the person to decide, Full auto hands the item over (`email_unsure`)."""
    value = (posting.url or "").strip() if url is None else url.strip()
    if value.lower().startswith("mailto:") or has_own_form(value):
        return []
    return list(read(posting.posting_text or "").unsure)


def choices(posting: Job, mode: Mode) -> list[str]:
    """The addresses the person may choose: the usable ones, and the unsure ones only when the
    person reviews (never in Full auto, never in Apply myself)."""
    usable = addresses(posting)
    return usable if usable or mode is not Mode.review else unsure(posting)


def own_company(posting: Job, recipient: str) -> bool:
    domain = recipient.rsplit("@", 1)[-1]
    host = urlsplit(posting.url or "").hostname
    return (
        host is not None
        and hosts.is_company_own(host)
        and not hosts.is_public_mailbox(domain)
        and domains.same_organisation(host, domain)
    )


def account_code(db: Session, user_id: uuid.UUID) -> str | None:
    account = db.get(MailAccount, user_id)
    if account is None or account.disconnecting_at is not None:
        return "not_connected"
    if account.status is not AccountStatus.ok:
        return "needs_reconnect"
    return None if SCOPE_SEND in account.scopes else "no_send_scope"


def recover(db: Session, application: Application, now: datetime, *, configured: bool) -> bool:
    from app.mail.locks import lock_send

    if application.ats != "email" or application.state is not State.needs_person:
        return False
    if application.needs not in {
        "not_connected",
        "no_send_scope",
        "needs_reconnect",
        "mail_send_unavailable",
    }:
        return False
    if account_code(db, application.user_id) or not configured:
        return False
    lock_send(db, application.user_id)
    application, _ = gate.lock(db, application.user_id, application.id)
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application.id))
    if draft is None or draft.status != "draft":
        return False
    states.move(application, State.ready, now)
    application.needs = application.error_code = None
    run.keep_reason(db, application)
    gate._event(db, application, "person_done", now, needs="email_connection")
    return True


def prepare(db: Session, application_id: uuid.UUID, now: datetime) -> str:
    plan = run.prepare(db, application_id, now)
    if isinstance(plan, str):
        return plan
    application = db.get(Application, application_id)
    posting = db.get(Job, application.posting_id)
    chosen = addresses(posting)
    doubtful = [] if chosen else unsure(posting)
    states.move(application, State.filling, now)
    application.ats = "email"
    application.document_versions = {
        kind: {
            **record,
            "pdf_sha256": hashlib.sha256(
                plan.pdfs[run.CV_NAME if kind == "cv" else run.LETTER_NAME]
            ).hexdigest(),
        }
        for kind, record in application.document_versions.items()
        if (run.CV_NAME if kind == "cv" else run.LETTER_NAME) in plan.pdfs
    }

    if len(plan.pdfs) != 2 or not plan.letter_text:
        states.move(application, State.needs_person, now)
        run._wait(db, application, "no_documents", now)
        return "no_documents"
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application_id))
    if draft is None:
        draft = MailDraft(
            id=uuid.uuid4(),
            user_id=application.user_id,
            application_id=application.id,
            kind="application",
            to_addrs=chosen if len(chosen) == 1 else [],
            cc_addrs=[],
            subject=posting.title.replace("\r", " ").replace("\n", " ")[:998],
            body=plan.letter_text,
            written_by="agent",
            version=1,
            status="draft",
            document_versions=application.document_versions,
            created_at=now,
            updated_at=now,
        )
        db.add(draft)
    for field, label, kind, value, source, options in (
        (
            "email_to",
            "Email recipient, new address from the posting"
            if chosen or not doubtful
            else "Email recipient: the posting may say not to email this address, read it first",
            "select",
            chosen[0] if len(chosen) == 1 else None,
            AnswerSource.document if len(chosen) == 1 else AnswerSource.left,
            chosen or doubtful,
        ),
        ("email_subject", "Email subject", "text", draft.subject, AnswerSource.document, []),
        ("email_body", "Email message", "textarea", draft.body, AnswerSource.agent, []),
    ):
        db.add(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=field,
                label=label,
                kind=kind,
                value=value,
                source=source,
                options=options,
                required=True,
                approved=False,
            )
        )
    if JobStatus(posting.status) is JobStatus.found:
        from app.jobs import service as jobs_service

        jobs_service.set_status(db, posting, JobStatus.staged, now, via="apply")
    code = account_code(db, application.user_id)
    if code or len(chosen) != 1:
        states.move(application, State.needs_person, now)
        run._wait(db, application, code or "required_left", now)
    else:
        states.move(application, State.ready, now)
    if application.mode is Mode.full_auto and (
        len(chosen) != 1 or not own_company(posting, chosen[0])
    ):
        reason = "email_company_domain" if chosen or not doubtful else "email_unsure"
        if application.state is State.needs_person:
            full_auto.hand_over(db, application, reason, now)
        else:
            full_auto.to_review(db, application, reason, now)
    run.keep_reason(db, application)
    gate._event(
        db,
        application,
        "email_prepared",
        now,
        recipients_count=len(chosen),
        unsure_count=len(doubtful),
    )
    return application.state.value


def field_issue(field_key: str, value: object) -> str | None:
    if field_key == "email_subject" and (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 998
        or any(c in value for c in "\r\n")
    ):
        return "email_subject"
    return None


def sync_draft(db: Session, application: Application, draft: MailDraft, now: datetime) -> None:
    values = {r.field_key: r for r in gate.rows_of(db, application)}
    recipient = values.get("email_to")
    allowed = choices(db.get(Job, application.posting_id), application.mode)
    if recipient is None or recipient.value not in allowed:
        raise gate.InvalidAnswerError("email_recipient")
    subject, body = values.get("email_subject"), values.get("email_body")
    if issue := field_issue("email_subject", subject.value if subject else None):
        raise gate.InvalidAnswerError(issue)
    candidate = MailDraft(
        to_addrs=[drafts.address(recipient.value)],
        cc_addrs=[],
        subject=subject.value,
        body=body.value if body else "",
    )
    try:
        drafts.validate(candidate)
    except ValueError:
        raise gate.InvalidAnswerError("email_message") from None
    draft.to_addrs, draft.cc_addrs = candidate.to_addrs, candidate.cc_addrs
    draft.subject, draft.body = candidate.subject, candidate.body
    draft.written_by = "user" if body and body.source is AnswerSource.person else "agent"
    draft.document_versions = application.document_versions
    draft.updated_at = now
