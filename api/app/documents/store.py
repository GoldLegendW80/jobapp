"""The storage service of the documents module (D5): every read and write of its tables.

Every function takes the user and reads or writes that user's rows only: a document, a version,
a generation, a message or a template of another person is "not found" (`NotFoundError`, the
api's 404), never read and never changed. The schema says the same thing again: every reference
between two rows of the module is a composite key `(<ref>_id, user_id)` in 0015, and the legacy
file of a version is checked by a trigger against the version's own posting, so a bug here
cannot cross users either.

**Versions are append only.** `snapshot` adds one; nothing rewrites a version's content.
`freeze` is the one function that sets `frozen_at`. A frozen version is never changed or
deleted by any function here (`FrozenVersionError`), and the trigger of 0015 refuses it below
the code too. A frozen version goes only with its document.

**Lock order**, the same in every function, so two transactions never wait on each other in a
circle:

1. the person's row (`users`, `FOR NO KEY UPDATE`), only when the list of base CVs changes
   (create, make default, delete): it serializes the count, the default and the promotion;
2. the posting row (`jobs`), only when a status moves (the jobs service takes it `FOR NO KEY
   UPDATE`; the freeze of D14 runs under it), or shared (`lock_posting`, `FOR SHARE`) by a write
   that inserts a row referencing the posting (a posting document, a writer run) while it holds
   lock 3;
3. the document row (`documents`, `FOR UPDATE`): every write of a draft, a version, a
   generation or a message takes it first. Between two documents, the posting documents come
   before the base CV they name: `create_generation` holds its document and then needs a key
   share lock on the base CV it names, so `delete_base`, whose `SET NULL` rewrites the posting
   documents and generations written from the base, locks those documents first (by id) and
   the base CV last;
4. a version row (`document_versions`, `FOR UPDATE`).

Every lock waits at most `LOCK_TIMEOUT_MS` (the jobs module's lock timeout, `SET LOCAL`), then
raises `DocumentBusyError`, a `JobBusyError`: the caller rolls back and answers 409, as the jobs
and ingestion modules do. A deadlock Postgres breaks (SQLSTATE 40P01) is the same answer: every
write here runs under `_busy`, so a wait at a statement, at a flush (a cascade, a `SET NULL`)
or at the caller's `commit` (use `commit` below) raises `DocumentBusyError`, never a raw
database error. Two saves on one revision: the second waits for the first, then finds
the revision moved and gets `StaleDraftError` with the winner's draft. Two generations for one
document: the second finds the first open (`GenerationOpenError`), and the partial unique index
refuses it below the code. Two `get_or_create` at once insert with `ON CONFLICT DO NOTHING`, so
one row is made and both return it.

Logs carry ids, counts and codes, never a document's text.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, undefer

from app.config import get_settings
from app.documents import checks as doc_checks
from app.documents import content as c
from app.documents.corrections import Correction
from app.documents.models import (
    OPEN_GENERATION,
    Author,
    BaseSource,
    Channel,
    Document,
    DocumentConsent,
    DocumentGeneration,
    DocumentMessage,
    DocumentSettingsRow,
    DocumentUserTemplate,
    DocumentVersion,
    FitResult,
    FreezeReason,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
    Language,
    Origin,
    Role,
    Scope,
)
from app.documents.settings import DEFAULT as DEFAULT_SETTINGS
from app.documents.settings import DocumentSettings
from app.ingestion.models import IngestionDocument
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobFile, JobStatus
from app.mail.factcheck_rules import Sources
from app.profile.models import Profile
from app.profile.schemas import PreferencesSection

logger = logging.getLogger("app.documents")

CLASSIC = "classic"  # the default template (D4a)
LOCK_TIMEOUT_MS = jobs_service.LOCK_TIMEOUT_MS
BUSY = "This document is being changed. Try again."
NAME_MAX = 100
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_USER_SLUG = re.compile(r"^upload-[a-z0-9]{8}$")
# lock_not_available (a lock wait over LOCK_TIMEOUT_MS) and deadlock_detected
_BUSY_STATES = frozenset({"55P03", "40P01"})


# --- errors -------------------------------------------------------------------------------------


class DocumentBusyError(jobs_service.JobBusyError):
    """A row lock was not free within LOCK_TIMEOUT_MS. The caller rolls back and answers 409."""


class NotFoundError(LookupError):
    """No such row for this user (another user's row included): the api's 404."""


class StaleDraftError(Exception):
    """The draft moved since `if_match`: the api's 409, with the current draft."""

    def __init__(self, document: Document) -> None:
        super().__init__("stale draft revision")
        self.document = document
        self.draft = document.draft
        self.revision = document.draft_revision


class FrozenVersionError(Exception):
    """The version is frozen (sent): it is never changed or deleted on its own."""


class BaseLimitError(Exception):
    """The person has the most base CVs allowed (`documents_base_cvs_max`)."""


class GenerationOpenError(Exception):
    """The document has a waiting, queued or running generation already."""

    def __init__(self, generation: DocumentGeneration) -> None:
        super().__init__("a generation is open")
        self.generation = generation


class PdfTooLargeError(ValueError):
    """The PDF is over `documents_max_pdf_bytes`."""


class MessageInvalidError(ValueError):
    """A chat message empty or over `documents_chat_max_chars`."""


class NameInvalidError(ValueError):
    """A base CV's name empty or over NAME_MAX characters."""


class TemplateUnknownError(LookupError):
    """A template slug that is not a slug, or a user template the person does not own."""


# --- locks --------------------------------------------------------------------------------------


def _wait(db: Session) -> None:
    """A wait for a row lock fails after LOCK_TIMEOUT_MS (transaction scoped, as in jobs)."""
    db.execute(
        sa.text("select set_config('lock_timeout', :ms, true)"), {"ms": f"{LOCK_TIMEOUT_MS}ms"}
    )


def is_busy(exc: BaseException) -> bool:
    """Whether `exc` is Postgres giving up on a row lock: a wait over the lock timeout (55P03)
    or a deadlock it broke (40P01). Every other database error keeps its behaviour."""
    return isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) in _BUSY_STATES


def _busy[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Every wait of `function`, at a statement or at a flush, ends in DocumentBusyError."""

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except DBAPIError as exc:
            if is_busy(exc):
                raise DocumentBusyError(BUSY) from exc
            raise

    return wrapper


def _execute(db: Session, statement: Any, params: Mapping[str, Any] | None = None) -> Any:
    try:
        return db.execute(statement, params) if params is not None else db.execute(statement)
    except DBAPIError as exc:
        if is_busy(exc):
            raise DocumentBusyError(BUSY) from exc
        raise


@_busy
def commit(db: Session) -> None:
    """The caller's commit, with a lock wait or a deadlock mapped to DocumentBusyError."""
    db.commit()


def _lock_person(db: Session, user_id: uuid.UUID) -> None:
    """Lock 1: the person's row, while the list of base CVs changes. NO KEY UPDATE, so inserts
    that only reference the user (their key share locks) are never blocked by it."""
    _wait(db)
    _execute(db, sa.text("SELECT 1 FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": user_id})


@_busy
def lock_posting(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> JobStatus | None:
    """Lock 2, shared: the user's posting `FOR SHARE`, taken before lock 3 by a write that
    inserts a row referencing the posting. It waits for a status move, which holds the posting
    `FOR NO KEY UPDATE` (jobs.service, issue #341), so the status read here is the one after the
    move; the foreign key check of the insert (`KEY SHARE`) then waits for nothing. Returns the
    posting's status, None when the posting is not the user's."""
    _wait(db)
    query = (
        sa.select(Job.status)
        .where(Job.user_id == user_id, Job.id == posting_id)
        .with_for_update(read=True)
    )
    return _execute(db, query).scalar_one_or_none()


@_busy
def lock_postings(db: Session, user_id: uuid.UUID, posting_ids: Collection[uuid.UUID]) -> None:
    """Lock 2 for many postings at once: the user's postings `FOR SHARE`, in id order, the order
    every other transaction that locks several job rows follows (jobs.service lock order). A
    pass that will lock several postings takes them here first; `lock_posting` later finds them
    held."""
    if not posting_ids:
        return
    _wait(db)
    query = (
        sa.select(Job.id)
        .where(Job.user_id == user_id, Job.id.in_(list(posting_ids)))
        .order_by(Job.id)
        .with_for_update(read=True)
    )
    _execute(db, query).all()


@_busy
def lock_document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    """Lock 3: the user's document, fresh from the database, locked until the transaction ends."""
    _wait(db)
    query = (
        documents_of(user_id)
        .where(Document.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    document = _execute(db, query).scalar_one_or_none()
    if document is None:
        raise NotFoundError(document_id)
    return document


def _lock_version(db: Session, document: Document, number: int) -> DocumentVersion:
    """Lock 4: one version of a document already locked."""
    query = (
        sa.select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.user_id == document.user_id,
            DocumentVersion.number == number,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    version = _execute(db, query).scalar_one_or_none()
    if version is None:
        raise NotFoundError(number)
    return version


# --- reads --------------------------------------------------------------------------------------


def documents_of(user_id: uuid.UUID) -> sa.Select[tuple[Document]]:
    """The base query of every document read: the user's documents, nothing else."""
    return sa.select(Document).where(Document.user_id == user_id)


def get(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document | None:
    return db.execute(documents_of(user_id).where(Document.id == document_id)).scalar_one_or_none()


def posting_documents(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> list[Document]:
    query = documents_of(user_id).where(
        Document.scope == Scope.posting, Document.posting_id == posting_id
    )
    return list(db.execute(query.order_by(Document.kind)).scalars())


def base_cvs(db: Session, user_id: uuid.UUID) -> list[Document]:
    """The person's base CVs, oldest first."""
    query = documents_of(user_id).where(Document.scope == Scope.base)
    return list(db.execute(query.order_by(Document.created_at, Document.id)).scalars())


def default_base(db: Session, user_id: uuid.UUID) -> Document | None:
    query = documents_of(user_id).where(Document.scope == Scope.base, Document.is_default)
    return db.execute(query).scalar_one_or_none()


def versions(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> list[DocumentVersion]:
    """A document's versions, newest first, without their content and PDF (deferred)."""
    query = sa.select(DocumentVersion).where(
        DocumentVersion.user_id == user_id, DocumentVersion.document_id == document_id
    )
    return list(db.execute(query.order_by(DocumentVersion.number.desc())).scalars())


def version(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, number: int, *, pdf: bool = False
) -> DocumentVersion | None:
    query = sa.select(DocumentVersion).where(
        DocumentVersion.user_id == user_id,
        DocumentVersion.document_id == document_id,
        DocumentVersion.number == number,
    )
    query = query.options(undefer(DocumentVersion.content))
    if pdf:
        query = query.options(undefer(DocumentVersion.pdf))
    return db.execute(query).scalar_one_or_none()


def user_templates(db: Session, user_id: uuid.UUID) -> list[DocumentUserTemplate]:
    """The person's own templates, newest first (the picker lists them first)."""
    query = sa.select(DocumentUserTemplate).where(DocumentUserTemplate.user_id == user_id)
    return list(db.execute(query.order_by(DocumentUserTemplate.created_at.desc())).scalars())


def user_template(db: Session, user_id: uuid.UUID, slug: str) -> DocumentUserTemplate | None:
    query = sa.select(DocumentUserTemplate).where(
        DocumentUserTemplate.user_id == user_id, DocumentUserTemplate.slug == slug
    )
    return db.execute(query).scalar_one_or_none()


def messages(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, channel: Channel = Channel.content
) -> list[DocumentMessage]:
    query = sa.select(DocumentMessage).where(
        DocumentMessage.user_id == user_id,
        DocumentMessage.document_id == document_id,
        DocumentMessage.channel == channel,
    )
    return list(db.execute(query.order_by(DocumentMessage.id)).scalars())


def settings(db: Session, user_id: uuid.UUID) -> DocumentSettings:
    """The person's writing rules, the defaults when no row was ever saved."""
    row = db.get(DocumentSettingsRow, user_id)
    if row is None:
        return DEFAULT_SETTINGS
    return DocumentSettings(
        no_dash_separators=row.no_dash_separators, banned_phrases=list(row.banned_phrases)
    )


@_busy
def save_settings(
    db: Session, user_id: uuid.UUID, value: DocumentSettings, at: datetime
) -> DocumentSettings:
    values = {
        "no_dash_separators": value.no_dash_separators,
        "banned_phrases": list(value.banned_phrases),
        "updated_at": at,
    }
    statement = pg_insert(DocumentSettingsRow).values(user_id=user_id, **values)
    db.execute(statement.on_conflict_do_update(index_elements=["user_id"], set_=values))
    db.expire_all()
    return value


@_busy
def accept_consent(db: Session, user_id: uuid.UUID, text_version: int, at: datetime) -> None:
    statement = pg_insert(DocumentConsent).values(
        user_id=user_id, text_version=text_version, accepted_at=at
    )
    db.execute(statement.on_conflict_do_nothing())


def consented(db: Session, user_id: uuid.UUID, text_version: int) -> bool:
    return db.get(DocumentConsent, (user_id, text_version)) is not None


# --- the language of a posting ------------------------------------------------------------------

# Frequent function words of each language, none shared: the text's language is the one whose
# words it uses more. A tie (or no text) is English.
_FRENCH_WORDS = (
    "le la les des du une et est pour avec dans nous vous sur au aux ce cette ces qui que sont "
    "votre vos notre nos ou mais sera chez être avoir poste profil entreprise équipe"
)
_FRENCH = frozenset(_FRENCH_WORDS.split())
_ENGLISH_WORDS = (
    "the and of to for with is are you we our your will this that on be an as role team who "
    "what have has from at"
)
_ENGLISH = frozenset(_ENGLISH_WORDS.split())
_WORD = re.compile(r"[a-zà-öø-ÿœ]+")


def language_of(text: str) -> Language:
    words = _WORD.findall(text.lower())
    french = sum(word in _FRENCH for word in words)
    english = sum(word in _ENGLISH for word in words)
    return Language.fr if french > english else Language.en


def preferences(db: Session, user_id: uuid.UUID) -> PreferencesSection:
    """The person's `preferences` section (opportunities O4): the language and the tone of the
    documents. Its defaults (`auto`, `plain`) when the section is empty or no longer
    validates, so a stored section the code refuses never stops a document."""
    stored = db.scalar(sa.select(Profile.sections["preferences"]).where(Profile.user_id == user_id))
    try:
        return PreferencesSection.model_validate(stored or {})
    except ValidationError:
        return PreferencesSection()


def language_for(text: str, chosen: PreferencesSection) -> Language:
    """The language of a new posting document: the person's `documents_language`, or the
    posting text's (`language_of`) when it is `auto`."""
    if chosen.documents_language == "auto":
        return language_of(text)
    return Language(chosen.documents_language)


# --- base CVs -----------------------------------------------------------------------------------


def _name(name: str) -> str:
    text = " ".join(name.split())
    if not text or len(text) > NAME_MAX:
        raise NameInvalidError("name")
    return text


def _limit(value: int | None, field: str) -> int:
    return value if value is not None else int(getattr(get_settings(), field))


def _own_upload(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID) -> None:
    query = sa.select(IngestionDocument.id).where(
        IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id
    )
    if db.execute(query).scalar_one_or_none() is None:
        raise NotFoundError(upload_id)


def _kind(document: Document) -> c.Kind:
    return "base" if document.scope == Scope.base else document.kind.value  # type: ignore[return-value]


def _model(kind: c.Kind, data: object) -> c.CvContent | c.LetterContent:
    """The validated content (`ContentInvalidError` with every field error otherwise)."""
    return c.parse(kind, c.dump(data) if isinstance(data, BaseModel) else data)


@_busy
def create_base(
    db: Session,
    user_id: uuid.UUID,
    name: str,
    source: BaseSource,
    *,
    at: datetime,
    content: object | None = None,
    language: Language = Language.en,
    template_slug: str = CLASSIC,
    written_by: Author = Author.writer,
    source_document_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> Document:
    """A new base CV. It becomes the default when the person has none, or when it is an upload
    and the default is one built from the profile. Raises BaseLimitError at the limit."""
    name = _name(name)
    model = _model("base", content if content is not None else {})
    _check_slug(db, user_id, template_slug)
    if source_document_id is not None:
        _own_upload(db, user_id, source_document_id)
    _lock_person(db, user_id)
    count = db.execute(
        sa.select(sa.func.count())
        .select_from(Document)
        .where(Document.user_id == user_id, Document.scope == Scope.base)
    ).scalar_one()
    if count >= _limit(limit, "documents_base_cvs_max"):
        raise BaseLimitError(count)
    current = default_base(db, user_id)
    make_default = current is None or (
        source == BaseSource.uploaded and current.base_source == BaseSource.profile
    )
    if make_default and current is not None:
        current.is_default = False
        current.updated_at = at
        db.flush()
    document = Document(
        user_id=user_id,
        scope=Scope.base,
        name=name,
        is_default=make_default,
        base_source=source,
        kind=Kind.cv,
        language=language,
        template_slug=template_slug,
        draft=c.dump(model),
        draft_schema=c.SCHEMA,
        draft_revision=1,
        draft_written_by=written_by,
        draft_updated_at=at,
        source_document_id=source_document_id,
        created_at=at,
        updated_at=at,
    )
    db.add(document)
    db.flush()
    _log("documents.store.base_created", document, source=source.value)
    return document


def _own_base(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    query = documents_of(user_id).where(Document.id == document_id, Document.scope == Scope.base)
    document = _execute(
        db, query.with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError(document_id)
    return document


@_busy
def rename_base(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, name: str, at: datetime
) -> Document:
    name = _name(name)
    _wait(db)
    document = _own_base(db, user_id, document_id)
    document.name = name
    document.updated_at = at
    db.flush()
    return document


@_busy
def set_default(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, at: datetime) -> Document:
    """Make this base CV the default; the one before loses the mark."""
    _lock_person(db, user_id)
    document = _own_base(db, user_id, document_id)
    if document.is_default:
        return document
    current = default_base(db, user_id)
    if current is not None:
        current.is_default = False
        current.updated_at = at
        db.flush()  # the partial unique index allows one default: the old mark goes first
    document.is_default = True
    document.updated_at = at
    db.flush()
    return document


def _lock_written_from(db: Session, user_id: uuid.UUID, base_id: uuid.UUID) -> None:
    """Lock 3 on every document the delete of a base CV rewrites (its `SET NULL`): those written
    from it and those with a generation written from it, by id, before the base CV itself."""
    named = sa.select(DocumentGeneration.document_id).where(
        DocumentGeneration.user_id == user_id, DocumentGeneration.base_document_id == base_id
    )
    query = (
        sa.select(Document.id)
        .where(
            Document.user_id == user_id,
            Document.id != base_id,
            sa.or_(Document.base_document_id == base_id, Document.id.in_(named)),
        )
        .order_by(Document.id)
        .with_for_update()
    )
    _wait(db)
    _execute(db, query)


@_busy
def delete_base(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, at: datetime
) -> Document | None:
    """Delete a base CV. The posting documents written from it stay (their `base_document_id`
    becomes null). Deleting the default makes the oldest other one the default; deleting the
    last one is allowed. Returns the default after the delete."""
    _lock_person(db, user_id)
    _lock_written_from(db, user_id, document_id)
    document = _own_base(db, user_id, document_id)
    was_default = document.is_default
    db.delete(document)
    db.flush()
    _log("documents.store.base_deleted", document)
    if not was_default:
        return default_base(db, user_id)
    others = base_cvs(db, user_id)
    if not others:
        return None
    oldest = others[0]
    oldest.is_default = True
    oldest.updated_at = at
    db.flush()
    return oldest


# --- the documents of a posting -----------------------------------------------------------------


def _posting_document(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, kind: Kind
) -> Document | None:
    query = documents_of(user_id).where(
        Document.scope == Scope.posting, Document.posting_id == posting_id, Document.kind == kind
    )
    return db.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()


@_busy
def get_or_create(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    kind: Kind,
    *,
    at: datetime,
    base_document_id: uuid.UUID | None = None,
) -> Document:
    """The posting's CV or letter, made empty the first time. Its language is the person's
    `preferences.documents_language`, the posting text's (`language_of`) when it is `auto`
    (`language_for`). A CV takes the template of the base CV it is written from (the one named,
    else the default), a letter the template of the posting's CV; else `classic`. Two calls at
    once make one row: the second insert waits for the first and does nothing."""
    existing = _posting_document(db, user_id, posting_id, kind)
    if existing is not None:
        return existing
    posting = db.execute(
        sa.select(Job.posting_text).where(Job.user_id == user_id, Job.id == posting_id)
    ).one_or_none()
    if posting is None:
        raise NotFoundError(posting_id)
    template, base_id = CLASSIC, None
    if kind == Kind.cv:
        base = (
            default_base(db, user_id)
            if base_document_id is None
            else get(db, user_id, base_document_id)
        )
        if base_document_id is not None and (base is None or base.scope != Scope.base):
            raise NotFoundError(base_document_id)
        if base is not None:
            template, base_id = base.template_slug, base.id
    else:
        cv = _posting_document(db, user_id, posting_id, Kind.cv)
        if cv is not None:
            template = cv.template_slug
    model = _model(kind.value, {})  # type: ignore[arg-type]
    statement = (
        pg_insert(Document)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            scope=Scope.posting,
            posting_id=posting_id,
            base_document_id=base_id,
            kind=kind,
            language=language_for(posting.posting_text or "", preferences(db, user_id)),
            template_slug=template,
            draft=c.dump(model),
            draft_schema=c.SCHEMA,
            draft_revision=1,
            draft_written_by=Author.writer,
            draft_updated_at=at,
            created_at=at,
            updated_at=at,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "posting_id", "kind"],
            index_where=sa.text("scope = 'posting'"),
        )
    )
    _wait(db)
    _execute(db, statement)
    document = _posting_document(db, user_id, posting_id, kind)
    if document is None:  # never: inserted now, or by the transaction waited for
        raise NotFoundError(posting_id)
    return document


# --- the draft ----------------------------------------------------------------------------------


def _write_draft(
    db: Session,
    document: Document,
    model: c.CvContent | c.LetterContent,
    written_by: Author,
    at: datetime,
    sources: Sources | None,
    today: date | None,
) -> Document:
    document.draft = c.dump(model)
    document.draft_schema = c.SCHEMA
    document.draft_revision += 1
    document.draft_written_by = written_by
    document.draft_updated_at = at
    document.updated_at = at
    if sources is None:
        document.checks, document.checks_revision = None, None
    else:
        found = doc_checks.run(
            _kind(document), model, sources, settings(db, document.user_id), today=today
        )
        document.checks = [asdict(issue) for issue in found.issues]
        document.checks_revision = document.draft_revision
    db.flush()
    return document


@_busy
def save_draft(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    content: object,
    if_match: int,
    written_by: Author,
    *,
    at: datetime,
    sources: Sources | None = None,
    today: date | None = None,
) -> Document:
    """Replace the draft, if its revision is still `if_match` (else StaleDraftError with the
    current draft). The content is validated by D2's model, size caps included
    (`ContentInvalidError`); its notes are recomputed by D2's checks when `sources` are given,
    else cleared until they are."""
    document = lock_document(db, user_id, document_id)
    if document.draft_revision != if_match:
        raise StaleDraftError(document)
    model = _model(_kind(document), content)
    return _write_draft(db, document, model, written_by, at, sources, today)


@_busy
def set_language(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, language: Language, at: datetime
) -> Document:
    document = lock_document(db, user_id, document_id)
    document.language = language
    document.updated_at = at
    db.flush()
    return document


@_busy
def restore(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    number: int,
    *,
    at: datetime,
    sources: Sources | None = None,
    today: date | None = None,
) -> Document:
    """Copy a version's content into the draft (a new draft revision); the version is read
    only, frozen or not."""
    document = lock_document(db, user_id, document_id)
    found = version(db, user_id, document_id, number)
    if found is None:
        raise NotFoundError(number)
    model = _model(_kind(document), found.content)
    return _write_draft(db, document, model, Author.restore, at, sources, today)


# --- versions -----------------------------------------------------------------------------------


def _correction(item: Correction | Mapping[str, Any]) -> dict[str, Any]:
    return item.as_dict() if isinstance(item, Correction) else dict(item)


def _latest(db: Session, document: Document) -> DocumentVersion | None:
    query = (
        sa.select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.number.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return _execute(db, query).scalar_one_or_none()


@_busy
def snapshot(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    origin: Origin,
    *,
    at: datetime,
    generation_id: uuid.UUID | None = None,
    corrections: Iterable[Correction | Mapping[str, Any]] = (),
    template_version: int = 1,
    template_slug: str | None = None,
    profile_revision: int | None = None,
    legacy_file_id: uuid.UUID | None = None,
    pdf_sha256: bytes | None = None,
    keep: int | None = None,
    content: object | None = None,
) -> tuple[DocumentVersion, bool]:
    """The draft as a version: `(version, made)`. `content`, given, is stored in place of the
    draft, which stays as it is (D7: a generation whose draft was edited during its run), and
    always makes a version. `template_slug`, given, is the template the version is printed
    with when it is not the document's own (a template no longer served prints with `classic`).
    Without a generation, when the latest version has the draft's content hash, language,
    template and template version and is not frozen, it is reused (`made` is False); a
    generation always makes one. A legacy version points at its job file (`legacy_file_id`, with
    the file's `pdf_sha256`). The others beyond `keep` are pruned."""
    document = lock_document(db, user_id, document_id)
    printed_with = template_slug if template_slug is not None else document.template_slug
    kind = _kind(document)
    model = _model(kind, document.draft if content is None else content)
    digest = c.content_hash(kind, model)
    latest = _latest(db, document)
    if (
        content is None
        and generation_id is None
        and legacy_file_id is None
        and latest is not None
        and latest.frozen_at is None
        and latest.content_hash == digest
        and latest.language == document.language
        and latest.template_slug == printed_with
        and latest.template_version == template_version
    ):
        return latest, False
    if generation_id is not None:
        _own_generation(db, document, generation_id)
    if legacy_file_id is not None:
        _own_legacy_file(db, document, legacy_file_id, pdf_sha256)
    made = DocumentVersion(
        document_id=document.id,
        user_id=user_id,
        number=(latest.number if latest is not None else 0) + 1,
        content=c.dump(model),
        content_schema=c.SCHEMA,
        content_hash=digest,
        language=document.language,
        template_slug=printed_with,
        template_version=template_version,
        origin=origin,
        generation_id=generation_id,
        profile_revision=profile_revision,
        corrections=[_correction(item) for item in corrections],
        legacy_file_id=legacy_file_id,
        pdf_sha256=pdf_sha256 if legacy_file_id is not None else None,
        created_at=at,
    )
    db.add(made)
    db.flush()
    prune(db, user_id, document_id, keep=keep)
    return made, True


def _own_generation(db: Session, document: Document, generation_id: uuid.UUID) -> None:
    query = sa.select(DocumentGeneration.id).where(
        DocumentGeneration.id == generation_id,
        DocumentGeneration.document_id == document.id,
        DocumentGeneration.user_id == document.user_id,
    )
    if db.execute(query).scalar_one_or_none() is None:
        raise NotFoundError(generation_id)


def _own_legacy_file(
    db: Session, document: Document, file_id: uuid.UUID, pdf_sha256: bytes | None
) -> None:
    """The legacy file is one of this posting's files, and the hash given is its own."""
    query = sa.select(JobFile.sha256).where(
        JobFile.id == file_id, JobFile.job_id == document.posting_id
    )
    found = db.execute(query).scalar_one_or_none() if document.posting_id is not None else None
    if found is None:
        raise NotFoundError(file_id)
    if pdf_sha256 is not None and pdf_sha256 != found:
        raise ValueError("pdf_sha256 is not the file's")


@_busy
def attach_pdf(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    number: int,
    pdf: bytes,
    *,
    fit: FitResult,
    fit_step: int | None,
    pages: int,
    max_bytes: int | None = None,
) -> DocumentVersion:
    """Store a render's PDF on a version, with its SHA-256 and its fit. Never on a frozen one."""
    if len(pdf) > _limit(max_bytes, "documents_max_pdf_bytes"):
        raise PdfTooLargeError(len(pdf))
    document = lock_document(db, user_id, document_id)
    found = _lock_version(db, document, number)
    if found.frozen_at is not None:
        raise FrozenVersionError(found.id)
    found.pdf = pdf
    found.pdf_sha256 = hashlib.sha256(pdf).digest()
    found.legacy_file_id = None
    found.fit, found.fit_step, found.pages = fit, fit_step, pages
    db.flush()
    return found


def _check_slug(db: Session, user_id: uuid.UUID, slug: str) -> DocumentUserTemplate | None:
    if not _SLUG.match(slug):
        raise TemplateUnknownError(slug)
    if not _USER_SLUG.match(slug):
        return None
    owned = user_template(db, user_id, slug)
    if owned is None:
        raise TemplateUnknownError(slug)
    return owned


@_busy
def approve(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, number: int, at: datetime
) -> DocumentVersion:
    """Mark one version approved, the one before loses the mark. A frozen version is never
    changed: approving it raises, and a frozen one keeps its own mark."""
    document = lock_document(db, user_id, document_id)
    found = _lock_version(db, document, number)
    if found.frozen_at is not None:
        raise FrozenVersionError(found.id)
    db.execute(
        sa.update(DocumentVersion)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.number != number,
            DocumentVersion.approved_at.is_not(None),
            DocumentVersion.frozen_at.is_(None),
        )
        .values(approved_at=None)
        .execution_options(synchronize_session=False)
    )
    found.approved_at = at
    db.flush()
    return found


@_busy
def prune(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, *, keep: int | None = None
) -> int:
    """Delete the versions beyond the latest `keep` that are neither frozen nor approved.
    Returns how many went."""
    document = lock_document(db, user_id, document_id)
    keep = _limit(keep, "documents_versions_kept")
    others = (
        sa.select(DocumentVersion.id)
        .where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.frozen_at.is_(None),
            DocumentVersion.approved_at.is_(None),
        )
        .order_by(DocumentVersion.number.desc())
        .offset(keep)
    )
    gone = db.execute(
        sa.delete(DocumentVersion)
        .where(DocumentVersion.id.in_(others.scalar_subquery()))
        .execution_options(synchronize_session=False)
    ).rowcount
    if gone:
        db.expire_all()
        logger.info(
            "documents.store.versions_pruned",
            extra={"document_id": str(document.id), "user_id": str(user_id), "count": gone},
        )
    return int(gone or 0)


@_busy
def freeze(
    db: Session, user_id: uuid.UUID, version_id: uuid.UUID, reason: FreezeReason, at: datetime
) -> DocumentVersion:
    """Freeze a version: the one function that sets `frozen_at`. Never refuses on content
    grounds. Freezing a frozen version changes nothing (the first freeze is the fact kept)."""
    query = sa.select(DocumentVersion.document_id, DocumentVersion.number).where(
        DocumentVersion.id == version_id, DocumentVersion.user_id == user_id
    )
    found = db.execute(query).one_or_none()
    if found is None:
        raise NotFoundError(version_id)
    document = lock_document(db, user_id, found.document_id)
    target = _lock_version(db, document, found.number)
    if target.frozen_at is None:
        target.frozen_at, target.frozen_reason = at, reason
        db.flush()
        logger.info(
            "documents.store.frozen",
            extra={
                "document_id": str(document.id),
                "user_id": str(user_id),
                "version": target.number,
                "reason": reason.value,
            },
        )
    return target


@_busy
def delete_version(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, number: int) -> None:
    """Delete one version that is not frozen."""
    document = lock_document(db, user_id, document_id)
    found = _lock_version(db, document, number)
    if found.frozen_at is not None:
        raise FrozenVersionError(found.id)
    db.delete(found)
    db.flush()


# --- generations --------------------------------------------------------------------------------


def open_generation(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID
) -> DocumentGeneration | None:
    query = sa.select(DocumentGeneration).where(
        DocumentGeneration.user_id == user_id,
        DocumentGeneration.document_id == document_id,
        DocumentGeneration.status.in_(OPEN_GENERATION),
    )
    return db.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()


@_busy
def create_generation(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    mode: GenerationMode,
    trigger: GenerationTrigger,
    *,
    at: datetime,
    estimate: Mapping[str, Any],
    token_ceiling: int,
    status: GenerationStatus = GenerationStatus.queued,
    wait_reason: GenerationWait | None = None,
    ceiling_day: date | None = None,
    base_document_id: uuid.UUID | None = None,
    message_id: int | None = None,
) -> DocumentGeneration:
    """A new generation of a document, under its row lock. One open per document: a second
    raises GenerationOpenError with the open one (the partial unique index stands behind it)."""
    document = lock_document(db, user_id, document_id)
    current = open_generation(db, user_id, document_id)
    if current is not None:
        raise GenerationOpenError(current)
    if base_document_id is not None:
        base = get(db, user_id, base_document_id)
        if base is None or base.scope != Scope.base:
            raise NotFoundError(base_document_id)
    made = DocumentGeneration(
        user_id=user_id,
        document_id=document.id,
        mode=mode,
        trigger=trigger,
        message_id=message_id,
        base_draft_revision=document.draft_revision,
        base_document_id=base_document_id,
        estimate=dict(estimate),
        token_ceiling=token_ceiling,
        status=status,
        wait_reason=wait_reason,
        ceiling_day=ceiling_day,
        created_at=at,
    )
    db.add(made)
    db.flush()
    return made


# --- the chat -----------------------------------------------------------------------------------


@_busy
def add_message(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    role: Role,
    text: str,
    *,
    at: datetime,
    channel: Channel = Channel.content,
    generation_id: uuid.UUID | None = None,
    version_number: int | None = None,
    max_chars: int | None = None,
    keep: int | None = None,
) -> DocumentMessage:
    """Add a chat message (at most `documents_chat_max_chars`), then keep the latest `keep` of
    the document."""
    if not text.strip() or len(text) > _limit(max_chars, "documents_chat_max_chars"):
        raise MessageInvalidError("text")
    document = lock_document(db, user_id, document_id)
    made = DocumentMessage(
        document_id=document.id,
        user_id=user_id,
        channel=channel,
        role=role,
        text=text,
        generation_id=generation_id,
        version_number=version_number,
        at=at,
    )
    db.add(made)
    db.flush()
    prune_messages(db, user_id, document_id, keep=keep)
    return made


@_busy
def prune_messages(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, *, keep: int | None = None
) -> int:
    """Delete a document's messages beyond the latest `keep` (all channels)."""
    document = lock_document(db, user_id, document_id)
    keep = _limit(keep, "documents_messages_kept")
    older = (
        sa.select(DocumentMessage.id)
        .where(DocumentMessage.document_id == document.id)
        .order_by(DocumentMessage.id.desc())
        .offset(keep)
    )
    gone = db.execute(
        sa.delete(DocumentMessage)
        .where(DocumentMessage.id.in_(older.scalar_subquery()))
        .execution_options(synchronize_session=False)
    ).rowcount
    return int(gone or 0)


def _log(event: str, document: Document, **extra: object) -> None:
    logger.info(
        event,
        extra={
            "document_id": str(document.id),
            "user_id": str(document.user_id),
            "scope": document.scope.value,
            "kind": document.kind.value,
            **extra,
        },
    )
