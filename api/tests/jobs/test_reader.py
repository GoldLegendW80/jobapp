"""The posting reader (app/jobs/reader.py, reader_tools.py; jobs J19, issue #764), against real
Postgres: the words, quotes and cut tables; what a save stores, drops and keeps (hand set
fields, duplicates, the VIE website's contract, the fit again, a text not shown whole never
cut); a save in a fresh worker registers no agent; a sweep that reads the postings it stored
with the fake agent backend, one without a credential, one out of time, one stopped by a
provider failure, two sharing the worker's one reading slot; the backlog read once, given up
after three attempts failed for their own content, an attempt given back for any other
failure, a pause after one, never during the person's sweep. Postings, employers and places
are invented."""

import asyncio
import dataclasses
import hashlib
import json
import logging
import secrets
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import failures, runs
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentRun, RunStatus
from app.agents.runner import RunContext, frozen
from app.browser.words import words as _words
from app.jobs import reader, service
from app.jobs.models import ContractType, Job, JobEvent, JobEventKind, Sweep, SweepStatus
from tests.agents.conftest import new_key
from tests.agents.run_helpers import context, execute, save_credential
from tests.conftest import make_settings
from tests.jobs import sweep_helpers as helpers
from tests.jobs.conftest import new_user

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
VIE_URL = "https://mon-vie-via.businessfrance.test/offres/8812"
OTHER_URL = "https://jobs.example-robotics.test/jobs/data-analyst"

TOP = "Rechercher dans le contenu du site\nAccueil Offres Mon espace\n"
HEAD = (
    "CHARGE DE PROJETS DATA H/F\nBARCELONE\nEXEMPLE ROBOTIQUE IBERICA\nESPAGNE (BARCELONE)\n"
    "Publiée le 20/10/2026\nPostuler\nRetour\n"
)
BODY = (
    "Description de la mission\nAu sein de l'équipe data d'Exemple Robotique Iberica, vous "
    "participez à la mise en place des outils de pilotage des robots de l'atelier de Barcelone. "
    "Vous analysez les données de production, construisez des tableaux de bord et accompagnez "
    "les équipes locales dans leurs usages quotidiens.\nProfil recherché\nDiplômé d'une école "
    "d'ingénieur, vous maîtrisez Python et SQL. L'espagnol courant est un atout.\n"
    "Informations complémentaires\nLieu : Barcelone\nDurée : 12 mois\n"
    "REMUNERATION MENSUELLE : 2688.86 €\nTélétravail : Oui"
)
TAIL = (
    "\nPostuler\nRetour\nMentions légales Plan du site Contact\n"
    "Offres similaires : Analyste financier H/F, Madrid\n"
)
POSTING = TOP + HEAD + BODY + TAIL
CHROME = ("Rechercher dans le contenu du site", "Postuler", "Retour", "Mentions légales")


def facts(**over: object) -> dict[str, object]:
    """What the fake stream saves for posting 0 (app/agents/fake_streams/posting_reader.json)."""
    body: dict[str, object] = {
        "index": 0,
        "location": {"value": "Barcelona, Spain", "quote": "ESPAGNE (BARCELONE)"},
        "pay": {
            "min": 2689,
            "max": 2689,
            "currency": "EUR",
            "period": "month",
            "quote": "REMUNERATION MENSUELLE : 2688.86 €",
        },
        "published_on": {"value": "2026-10-20", "quote": "Publiée le 20/10/2026"},
        "company": {"value": "Exemple Robotique Iberica", "quote": "d'Exemple Robotique Iberica"},
        "description": {
            "start": "Description de la mission Au sein",
            "end": "MENSUELLE : 2688.86 € Télétravail : Oui",
        },
    }
    body.update(over)
    return body


def add_posting(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    text: str | None = POSTING,
    url: str | None = VIE_URL,
    company: str = "Employer not stated",
    location: str = "",
    title: str = "Chargé de projets data H/F",
) -> uuid.UUID:
    with factory() as db, db.begin():
        job = service.create_job(
            db,
            user_id,
            service.NewJob(
                company=company,
                title=title,
                location=location,
                url=url,
                source=urlsplit(url).hostname if url else None,
                description=(text or "")[:300] or None,
                posting_text=text,
                contract_type=ContractType.VIE if url == VIE_URL else ContractType.CDI,
            ),
            NOW,
        )
        return job.id


def job_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        found = db.execute(
            sa.select(Job).where(Job.id == job_id).options(undefer(Job.posting_text))
        ).scalar_one()
        db.expunge(found)
        return found


def reader_events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[dict[str, object]]:
    with factory() as db:
        return [
            data
            for data in db.scalars(
                sa.select(JobEvent.data)
                .where(JobEvent.job_id == job_id, JobEvent.kind == JobEventKind.edit)
                .order_by(JobEvent.id)
            )
            if data.get("via") == reader.VIA
        ]


def bound(factory: sessionmaker[Session], user_id: uuid.UUID, *job_ids: uuid.UUID) -> Mapping:
    """The binding a run of these postings gets, as the runner makes it at its start."""
    with factory() as db:
        run = AgentRun(user_id=user_id, input={"job_ids": [str(one) for one in job_ids]})
        binding = reader.bind_postings(db, run)
    assert binding is not None
    return frozen(dict(binding))


def save(factory: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID, **over: object):
    return reader.save(factory, user_id, bound(factory, user_id, job_id), facts(**over), NOW)


# --- words, quotes and the cut ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        POSTING,
        "Ünïcödé  ÉTÉ (Barcelone) 2688.86 € x",
        "ﬁnance ＡＢＣ straße İstanbul",
        "",
        "   ",
        "été    a​b",
        "مهندس بيانات في برشلونة",
    ],
)
def test_the_fold_map_folds_as_the_finder(text: str) -> None:
    folded, origin = reader.fold_map(text)
    assert folded == _words(text)
    assert len(origin) == len(folded)
    assert origin == sorted(origin)


@pytest.mark.parametrize(
    ("quote", "found"),
    [
        ("ESPAGNE (BARCELONE)", True),
        ("espagne barcelone", True),
        ("Espagne, Barcelone", True),
        ("REMUNERATION MENSUELLE : 2688.86 €", True),
        ("Rémunération mensuelle 2688,86", True),
        ("Publiée le 20/10/2026", True),
        ("publiee le 20.10.2026", True),
        ("Lieu : Barcelone", True),
        ("Barcelona, Spain", False),  # a translation is a value, never a quote
        ("Publiée le 21/10/2026", False),
        ("REMUNERATION ANNUELLE : 2688.86 €", False),
        ("2688", True),
        ("268", False),  # a number inside a longer one
        ("Barcelon", False),  # a word cut short
        ("BARCELONES", False),
        ("", False),
        ("   ", False),
        ("€ : ()", False),  # no word at all
        ("Lieu Barcelone Durée", True),  # a line break is spacing, as for the finder
        ("Lieu Barcelone Madrid", False),
        ("x" * (reader.QUOTE_MAX + 1), False),
    ],
)
def test_a_quote_is_found_as_a_run_of_whole_words(quote: str, found: bool) -> None:
    assert reader.quoted(quote, _words(POSTING)) is found


def cut_of(start: str, end: str, text: str = POSTING) -> str | None:
    folded, origin = reader.fold_map(text)
    return reader.cut(text, folded, origin, start, end)


def test_the_cut_keeps_the_postings_own_text() -> None:
    piece = cut_of("Description de la mission Au sein", "MENSUELLE : 2688.86 € Télétravail : Oui")
    assert piece == BODY
    assert not any(chrome in piece for chrome in CHROME)


def test_the_cut_runs_to_the_last_match_of_its_end_and_keeps_closing_punctuation() -> None:
    ending = "Apply before Friday. Apply before Friday!) Footer"
    text = "Start of the posting here. " + "Body words. " * 40 + ending
    piece = cut_of("Start of the posting", "Apply before Friday", text)
    assert piece is not None
    assert piece.endswith("Apply before Friday!)")
    assert piece.startswith("Start of the posting")


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("Description de la mission", "words that are not there at all"),
        ("words that are not there", "Télétravail : Oui en plus"),
        ("MENSUELLE : 2688.86 € Télétravail", "Description de la mission"),  # the end first
        ("Description de", "MENSUELLE : 2688.86 € Télétravail : Oui"),  # two words
        ("Description de la mission", "Télétravail Oui"),
        ("Profil recherché Diplômé d'une", "MENSUELLE : 2688.86 €"),  # keeps too little
        (" ".join(["mot"] * 31), "MENSUELLE : 2688.86 € Télétravail : Oui"),
        ("", ""),
    ],
)
def test_a_cut_without_both_ends_or_that_keeps_too_little_is_refused(start: str, end: str) -> None:
    assert cut_of(start, end) is None


# --- a save ----------------------------------------------------------------------------------


def test_a_save_stores_each_grounded_value_and_the_postings_own_text(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader@example.org")
    job_id = add_posting(factory, user)
    done = save(factory, user, job_id, work_mode={"value": "remote", "quote": "Full remote"})
    assert (done.read, done.error, done.refused) == (True, False, 1)
    job = job_of(factory, job_id)
    assert job.location == "Barcelona, Spain"
    assert (job.pay_min, job.pay_max, job.pay_currency, job.pay_period) == (
        2689,
        2689,
        "EUR",
        "month",
    )
    assert job.published_on == date(2026, 10, 20)
    assert job.company == "Exemple Robotique Iberica"
    assert job.work_mode is None  # its quote is not in the text: dropped, never stored
    assert job.posting_text == BODY
    assert job.description == BODY[:300]
    assert job.read_at == NOW
    [event] = reader_events(factory, job_id)
    assert set(event["changes"]) == {  # type: ignore[arg-type]
        "location",
        "pay_min",
        "pay_max",
        "pay_currency",
        "pay_period",
        "published_on",
        "company",
        "description",
    }
    assert "work_mode" in done.text
    assert "quote is not in the posting text" in done.text
    assert "Barcelona" not in done.text
    assert "2689" not in done.text  # names, never values


def test_the_vie_websites_contract_is_kept(factory: sessionmaker[Session]) -> None:
    user = new_user(factory, "e2e-reader-vie@example.org")
    job_id = add_posting(factory, user)
    other = add_posting(factory, user, url=OTHER_URL, title="Data analyst")
    contract = {"value": "CDD", "quote": "Durée : 12 mois"}
    save(factory, user, job_id, contract_type=contract)
    reader.save(factory, user, bound(factory, user, other), facts(contract_type=contract), NOW)
    assert job_of(factory, job_id).contract_type is ContractType.VIE
    assert job_of(factory, other).contract_type is ContractType.CDD


def test_a_field_set_by_hand_stays_and_the_readers_own_edit_does_not_count(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-hand@example.org")
    job_id = add_posting(factory, user, location="Somewhere")
    with factory() as db:
        job = service.lock_job(db, user, job_id)
        assert job is not None
        service.edit_job(db, job, service.JobChanges(fields={"location": "Valencia, Spain"}), NOW)
        db.commit()
    done = save(factory, user, job_id)
    job = job_of(factory, job_id)
    assert job.location == "Valencia, Spain"
    assert job.pay_min == 2689
    assert "Kept as the person set them by hand: location" in done.text
    with factory() as db:
        assert reader.hand_set(db, job_id) == {"location"}
    # The reader's own edit of the company is not a hand set field for the next read.
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == job_id).values(posting_text=POSTING))
        db.commit()
    again = reader.save(
        factory,
        user,
        bound(factory, user, job_id),
        facts(company={"value": "Exemple Iberica", "quote": "EXEMPLE ROBOTIQUE IBERICA"}),
        NOW,
    )
    assert "company" not in again.text.split("by hand")[-1]
    assert job_of(factory, job_id).company == "Exemple Iberica"


def test_an_identity_that_makes_another_of_the_persons_postings_is_kept(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-dup@example.org")
    add_posting(
        factory,
        user,
        url=OTHER_URL,
        company="Exemple Robotique Iberica",
        location="Barcelona, Spain",
    )
    job_id = add_posting(factory, user)
    done = save(factory, user, job_id)
    job = job_of(factory, job_id)
    assert (job.company, job.location) == ("Employer not stated", "")
    assert job.pay_min == 2689
    assert "Kept as they are: company, location" in done.text


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("published_on", {"value": "2026-11-11", "quote": "Publiée le 20/10/2026"}),
        ("published_on", {"value": "1999-12-31", "quote": "Publiée le 20/10/2026"}),
        ("published_on", {"value": "20/10/2026", "quote": "Publiée le 20/10/2026"}),
        ("published_on", {"value": "2026-10-20T00:00:00", "quote": "Publiée le 20/10/2026"}),
        ("location", {"value": "Barcelona\nSpain", "quote": "Lieu : Barcelone"}),
        ("location", {"value": "x" * 301, "quote": "Lieu : Barcelone"}),
        ("location", {"value": "<b>Barcelona</b>", "quote": "Lieu : Barcelone"}),
        ("company", {"value": "E" * 201, "quote": "EXEMPLE ROBOTIQUE IBERICA"}),
        ("work_mode", {"value": "sometimes", "quote": "Télétravail : Oui"}),
        ("contract_type", {"value": "Permanent", "quote": "Durée : 12 mois"}),
        ("pay", {"min": 2688.86, "max": 2688.86, "currency": "EUR", "period": "month",
                 "quote": "REMUNERATION MENSUELLE : 2688.86 €"}),
        ("pay", {"min": 2689, "max": 2689, "currency": "ZZZ", "period": "month",
                 "quote": "REMUNERATION MENSUELLE : 2688.86 €"}),
        ("pay", {"min": 3000, "max": 2689, "currency": "EUR", "period": "month",
                 "quote": "REMUNERATION MENSUELLE : 2688.86 €"}),
        ("pay", {"min": 2689, "max": 2689, "currency": "EUR", "period": "month",
                 "quote": "REMUNERATION MENSUELLE"}),
        ("pay", {"min": 2689, "max": 2689, "currency": "EUR", "period": "week",
                 "quote": "REMUNERATION MENSUELLE : 2688.86 €"}),
        ("description", {"start": "Description de la mission Au sein"}),
    ],
)  # fmt: skip
def test_a_value_that_fails_its_check_is_dropped_and_counted(
    factory: sessionmaker[Session], name: str, value: dict[str, object]
) -> None:
    user = new_user(factory, f"e2e-reader-{uuid.uuid4().hex[:8]}@example.org")
    job_id = add_posting(factory, user)
    before = job_of(factory, job_id)
    done = save(factory, user, job_id, **{name: value})
    assert done.refused == 1
    assert "not a valid value" in done.text
    after = job_of(factory, job_id)
    column = {"pay": "pay_min", "description": "posting_text"}.get(name, name)
    assert getattr(after, column) == getattr(before, column)


def test_a_malformed_call_stores_nothing_and_says_what_to_fix(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-bad@example.org")
    job_id = add_posting(factory, user)
    for raw, words in (
        ({**facts(), "salary": 1}, "salary"),
        ({**facts(), "index": "0"}, "index"),
        (facts(index=1), "No posting has this index"),
    ):
        done = reader.save(factory, user, bound(factory, user, job_id), raw, NOW)
        assert done.error
        assert not done.read
        assert words in done.text
    assert job_of(factory, job_id).read_at is None
    assert reader_events(factory, job_id) == []


def test_a_new_text_scores_again_and_a_fit_set_by_hand_stays(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-fit@example.org")
    helpers.set_profile(factory, user, None)
    scored = add_posting(factory, user)
    manual = add_posting(factory, user, url=OTHER_URL, title="Data analyst")
    with factory() as db:
        for job_id, fit in ((scored, 3), (manual, 97)):
            job = service.lock_job(db, user, job_id)
            assert job is not None
            if job_id == manual:
                service.set_manual_fit(db, job, fit, NOW)
            else:
                job.fit = fit
        db.commit()
    save(factory, user, scored)
    reader.save(factory, user, bound(factory, user, manual), facts(), NOW)
    assert job_of(factory, scored).fit != 3
    assert job_of(factory, manual).fit == 97


def test_the_binding_holds_the_persons_postings_with_a_text(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-bind@example.org")
    other = new_user(factory, "e2e-reader-other@example.org")
    mine = add_posting(factory, user)
    empty = add_posting(factory, user, text=None, url=OTHER_URL, title="Data analyst")
    theirs = add_posting(factory, other)
    with factory() as db:
        run = AgentRun(user_id=user, input={"job_ids": [str(theirs), str(empty), str(mine)]})
        assert reader.bind_postings(db, run) == {
            "jobs": [str(mine)],
            "texts": [hashlib.sha256(POSTING.encode()).hexdigest()],
        }
        run = AgentRun(user_id=user, input={"job_ids": [str(theirs)]})
        assert reader.bind_postings(db, run) is None


# --- a cut only of the text the agent was shown whole ----------------------------------------


FIRST_PARAGRAPH_END = "accompagnez les équipes locales dans leurs usages quotidiens"


def long_posting() -> str:
    """A page whose chrome fills most of what read_postings shows: the bound (TEXT_SHOWN_MAX)
    falls in the posting's body just after its first paragraph, its profile and pay after it."""
    paragraph_end = BODY.index("quotidiens.") + len("quotidiens.")
    filler = "Offres similaires : Analyste financier H/F, Madrid\n"
    room = reader.TEXT_SHOWN_MAX - len(TOP) - len(HEAD) - paragraph_end - 5
    chrome = (filler * (room // len(filler) + 1))[:room]
    text = TOP + chrome + HEAD + BODY + TAIL
    shown_up_to = len(TOP + chrome + HEAD)
    assert shown_up_to + paragraph_end < reader.TEXT_SHOWN_MAX < shown_up_to + len(BODY)
    return text


def test_a_text_shown_shortened_stays_whole_and_its_other_facts_are_stored(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-long@example.org")
    text = long_posting()
    job_id = add_posting(factory, user, text=text)
    binding = bound(factory, user, job_id)
    [listed] = json.loads(reader.postings(factory, user, binding, lambda value: value))["postings"]
    assert listed["posting_text_truncated"] is True
    assert len(listed["untrusted_posting_text"]) == reader.TEXT_SHOWN_MAX
    assert FIRST_PARAGRAPH_END in listed["untrusted_posting_text"]
    assert "Profil recherché" not in listed["untrusted_posting_text"]
    asked = {"start": "Description de la mission Au sein", "end": FIRST_PARAGRAPH_END}
    # What the cut alone would keep: the first paragraph, the profile and the pay gone.
    piece = cut_of(asked["start"], asked["end"], text)
    assert piece is not None
    assert "Profil recherché" not in piece
    done = reader.save(factory, user, binding, facts(description=asked), NOW)
    job = job_of(factory, job_id)
    assert job.posting_text == text
    assert job.location == "Barcelona, Spain"
    assert (job.pay_min, job.pay_period) == (2689, "month")
    assert job.read_at == NOW
    assert (done.read, done.refused) == (True, 1)
    assert "Not cut, you were not shown this posting's whole text" in done.text
    [event] = reader_events(factory, job_id)
    assert "description" not in event["changes"]  # type: ignore[operator]


def at_length(length: int) -> str:
    """POSTING with chrome after it up to `length` characters."""
    return POSTING + ("Plan du site " * length)[: length - len(POSTING)]


@pytest.mark.parametrize(
    ("length", "whole"),
    [
        (len(POSTING), True),
        (reader.TEXT_SHOWN_MAX - 1, True),
        (reader.TEXT_SHOWN_MAX, True),
        (reader.TEXT_SHOWN_MAX + 1, False),
        (reader.TEXT_SHOWN_MAX * 3, False),
    ],
)
def test_a_text_is_cut_up_to_the_bound_shown_and_never_past_it(
    factory: sessionmaker[Session], length: int, whole: bool
) -> None:
    user = new_user(factory, f"e2e-reader-bound-{length}@example.org")
    text = at_length(length)
    job_id = add_posting(factory, user, text=text)
    done = save(factory, user, job_id)
    assert job_of(factory, job_id).posting_text == (BODY if whole else text)
    assert ("Not cut" in done.text) is not whole


def test_a_cut_needs_the_text_the_run_was_bound_to_shown_whole(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-reader-seen@example.org")
    job_id = add_posting(factory, user)

    def lengthens(value: Any) -> Any:  # a redaction that makes the text shown pass the bound
        return value + " " * reader.TEXT_SHOWN_MAX if isinstance(value, str) else value

    redacted = reader.save(factory, user, bound(factory, user, job_id), facts(), NOW, lengthens)
    assert "Not cut" in redacted.text
    assert job_of(factory, job_id).posting_text == POSTING
    # The text changed after the run was bound: the agent never saw the new words.
    binding = bound(factory, user, job_id)
    changed = POSTING + "\nAvantages : mutuelle et tickets restaurant."
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == job_id).values(posting_text=changed))
        db.commit()
    later = reader.save(factory, user, binding, facts(), NOW)
    assert "Not cut" in later.text
    assert job_of(factory, job_id).posting_text == changed
    # A binding that names no text, and one whose texts are another shape, cut nothing.
    for binding in ({"jobs": [str(job_id)]}, {"jobs": [str(job_id)], "texts": "abc"}):
        assert "Not cut" in reader.save(factory, user, binding, facts(), NOW).text
    assert job_of(factory, job_id).posting_text == changed
    # The same text bound again is cut.
    reader.save(factory, user, bound(factory, user, job_id), facts(), NOW)
    assert job_of(factory, job_id).posting_text == BODY


# --- the reader's code registers no agent where it runs --------------------------------------

API_DIR = Path(__file__).resolve().parents[2]
FRESH_SAVE = """
import json, os, sys, uuid
from datetime import UTC, datetime
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from app import worker
from app.agents import definitions
from app.agents.models import AgentRun
worker.agents_queue()
def state():
    return {'longest': worker.longest_agent_s(), 'stale': str(worker.agent_stale_running()),
        'agents': sorted(definitions.AGENTS),
        'modules': sorted(m for m in sys.modules if m.startswith('app.browser'))}
before = state()
from app.jobs import models, reader
user, job = uuid.UUID(sys.argv[1]), uuid.UUID(sys.argv[2])
engine = sa.create_engine(os.environ['DATABASE_URL'], poolclass=sa.pool.NullPool)
sessions = sessionmaker(bind=engine, expire_on_commit=False)
with sessions() as db:
    binding = reader.bind_postings(db, AgentRun(user_id=user, input={'job_ids': [str(job)]}))
now = datetime(2026, 11, 9, 9, tzinfo=UTC)
saved = reader.save(sessions, user, binding, json.loads(sys.argv[3]), now)
nature = reader._nature(models.Job(url=sys.argv[4], source=None))
folded, _ = reader.fold_map('Lieu : Barcelone')
print(json.dumps({'before': before, 'after': state(), 'read': saved.read, 'nature': nature,
    'quoted': reader.quoted('lieu barcelone', folded)}))
"""


def test_a_save_in_a_fresh_worker_leaves_its_agents_and_their_bounds_as_they_were(
    factory: sessionmaker[Session],
) -> None:
    """The agents worker's longest wall time (its job timeout and stale bound) is computed from
    the agents registered in its process: a save there must import none (a sweep agent's day)."""
    user = new_user(factory, "e2e-reader-fresh@example.org")
    job_id = add_posting(factory, user)
    asked = facts(contract_type={"value": "CDD", "quote": "Durée : 12 mois"})
    ran = subprocess.run(
        [sys.executable, "-c", FRESH_SAVE, str(user), str(job_id), json.dumps(asked), VIE_URL],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found = json.loads(ran.stdout.strip().splitlines()[-1])
    assert (found["read"], found["nature"], found["quoted"]) == (True, "VIE", True)
    assert found["after"] == found["before"]
    assert found["before"]["longest"] == 3600
    assert "browser_discovery" not in found["after"]["agents"]
    assert "app.browser.finder" not in found["after"]["modules"]
    assert "app.browser.sources.interactive" not in found["after"]["modules"]
    job = job_of(factory, job_id)
    assert (job.read_at, job.contract_type, job.posting_text) == (NOW, ContractType.VIE, BODY)


# --- a sweep with the fake agent backend -----------------------------------------------------


@pytest.fixture
def run_context(
    factory: sessionmaker[Session], migrated_url: str, tmp_path: Path
) -> tuple[RunContext, Keyring]:
    key = new_key()
    settings = make_settings(migrated_url, credentials_key=key, agents_scratch_dir=str(tmp_path))
    keyring = Keyring.from_text(key)
    rc = dataclasses.replace(context(settings, factory, keyring), clock=lambda: NOW)
    return rc, keyring


def vie_finding(text: str = POSTING) -> object:
    return helpers.finding(
        "Chargé de projets data H/F", company=None, location="", url=VIE_URL, text=text
    )


def test_a_sweep_reads_the_postings_it_stored(
    factory: sessionmaker[Session],
    run_context: tuple[RunContext, Keyring],
    caplog: pytest.LogCaptureFixture,
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-reader-sweep@example.org")
    save_credential(factory, keyring, user)
    helpers.set_profile(factory, user, helpers.search())
    canary = f"canary{secrets.token_hex(8)}"
    sweep_id = helpers.new_sweep(factory, user)
    caplog.set_level(logging.DEBUG)
    row = helpers.run(
        factory, sweep_id, vie_finding(POSTING + canary), reader=reader.SweepReader(lambda: rc)
    )
    assert row.status is SweepStatus.done
    assert row.stats["reading"] == {"read": 1, "refused": 1, "failed": 0, "left": 0}
    [job] = helpers.jobs_of(factory, user)
    assert job.location == "Barcelona, Spain"
    assert (job.pay_min, job.pay_currency, job.pay_period) == (2689, "EUR", "month")
    assert job.published_on == date(2026, 10, 20)
    assert job.company == "Exemple Robotique Iberica"
    assert job.contract_type is ContractType.VIE
    assert job.work_mode is None
    assert job.posting_text == BODY
    assert job.description == BODY[:300]
    assert job.read_at == NOW
    assert job.read_attempts == 1
    assert canary not in caplog.text
    assert "Barcelona" not in caplog.text
    with factory() as db:
        stored = [str(data) for data in db.scalars(sa.select(AgentEvent.data))]
    assert not any(canary in data or "Barcelona" in data for data in stored)


def test_a_sweep_without_a_credential_stores_its_postings_as_before(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    rc, _ = run_context
    user = new_user(factory, "e2e-reader-nokey@example.org")
    helpers.set_profile(factory, user, helpers.search())
    sweep_id = helpers.new_sweep(factory, user)
    row = helpers.run(factory, sweep_id, vie_finding(), reader=reader.SweepReader(lambda: rc))
    assert row.status is SweepStatus.done
    assert row.stats["reading"]["skipped"] == "no_credential"
    [job] = helpers.jobs_of(factory, user)
    assert job.posting_text == POSTING
    assert job.read_at is None
    assert job.read_attempts == 0
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0


def test_a_sweep_out_of_reading_time_ends_done_and_leaves_its_postings(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-reader-late@example.org")
    save_credential(factory, keyring, user)
    helpers.set_profile(factory, user, helpers.search())
    sweep_id = helpers.new_sweep(factory, user)
    late = reader.SweepReader(lambda: rc, reading_s=0.0)
    row = helpers.run(factory, sweep_id, vie_finding(), reader=late)
    assert row.status is SweepStatus.done
    assert row.stats["reading"]["left"] == 1
    [job] = helpers.jobs_of(factory, user)
    assert job.read_at is None
    # The run the bound cut gave its attempt back: the backlog reads the posting as new.
    assert (job.read_attempts, job.read_run_id) == (0, None)
    assert ended_runs(factory, user) == [(RunStatus.failed, runs.STOPPED)]


def ended_runs(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[tuple[object, ...]]:
    with factory() as db:
        return [
            tuple(row)
            for row in db.execute(
                sa.select(AgentRun.status, AgentRun.error)
                .where(AgentRun.user_id == user_id)
                .order_by(AgentRun.created_at, AgentRun.id)
            )
        ]


def test_a_sweep_stops_reading_at_a_provider_failure_and_keeps_every_attempt(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-reader-refused@example.org")
    save_credential(factory, keyring, user)
    helpers.set_profile(factory, user, helpers.search())
    sweep_id = helpers.new_sweep(factory, user)
    found = [
        helpers.finding(
            f"Chargé de projets data {n} H/F",
            company=None,
            location="",
            url=f"{VIE_URL}{n}",
            text=f"{POSTING}Référence {n}",
        )
        for n in range(reader.BATCH_MAX + 1)
    ]
    refused = reader.SweepReader(lambda: rc, script="refused")
    row = helpers.run(factory, sweep_id, *found, reader=refused)
    assert row.status is SweepStatus.done
    reading = row.stats["reading"]
    assert reading["skipped"] == "provider_failure"
    assert (reading["failed"], reading["left"]) == (reader.BATCH_MAX, 1)
    assert ended_runs(factory, user) == [(RunStatus.failed, failures.REFUSED)]  # no second batch
    jobs = helpers.jobs_of(factory, user)
    assert len(jobs) == reader.BATCH_MAX + 1
    assert {(job.read_attempts, job.read_run_id, job.read_at) for job in jobs} == {(0, None, None)}


def test_the_sweeps_reading_runs_share_one_slot_in_the_worker(
    factory: sessionmaker[Session],
    run_context: tuple[RunContext, Keyring],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents import runner

    rc, keyring = run_context
    people = [new_user(factory, f"e2e-reader-slot-{n}@example.org") for n in range(2)]
    for person in people:
        save_credential(factory, keyring, person)
    jobs = [add_posting(factory, person) for person in people]
    holding: list[uuid.UUID] = []
    most: list[int] = []
    seen: list[list[int]] = []

    async def execute(context: RunContext, run_id: uuid.UUID) -> RunStatus:
        holding.append(run_id)
        most.append(len(holding))
        seen.append(sorted(job_of(factory, job_id).read_attempts for job_id in jobs))
        for _ in range(5):  # yields: the other sweep's reading runs meanwhile
            await asyncio.sleep(0)
        with factory() as db:
            db.execute(
                sa.update(AgentRun)
                .where(AgentRun.id == run_id)
                .values(status=RunStatus.done, finished_at=NOW)
            )
            db.commit()
        holding.remove(run_id)
        return RunStatus.done

    monkeypatch.setattr(runner, "execute", execute)

    async def two_sweeps() -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = [
            {"read": 0, "refused": 0, "failed": 0, "left": 1} for _ in people
        ]
        await asyncio.gather(
            *(
                reader.SweepReader(lambda: rc)._read(rc, person, [job_id], reading)
                for person, job_id, reading in zip(people, jobs, readings, strict=True)
            )
        )
        return readings

    readings = asyncio.run(two_sweeps())
    assert max(most) == reader.SWEEP_READS_AT_ONCE == 1
    assert seen == [[0, 1], [1, 1]]  # the batch waiting for the slot counted no attempt yet
    assert [reading["failed"] for reading in readings] == [1, 1]


def test_a_reading_that_raises_never_fails_the_sweep(factory: sessionmaker[Session]) -> None:
    user = new_user(factory, "e2e-reader-raise@example.org")
    helpers.set_profile(factory, user, helpers.search())
    sweep_id = helpers.new_sweep(factory, user)

    async def broken(plan: object, stats: object, deadline: float) -> None:
        raise RuntimeError("boom")

    row = helpers.run(factory, sweep_id, vie_finding(), reader=broken)
    assert row.status is SweepStatus.done
    assert len(helpers.jobs_of(factory, user)) == 1


# --- the backlog -----------------------------------------------------------------------------


def backlog(factory: sessionmaker[Session], now: datetime = NOW) -> list[uuid.UUID]:
    with factory() as db:
        started = reader.start_backlog(db, now)
        db.commit()
        return started


def end_run(factory: sessionmaker[Session], run_id: uuid.UUID, error: str) -> None:
    with factory() as db:
        assert runs.fail_queued(db, run_id, error, NOW)
        db.commit()


def test_the_backlog_reads_a_saved_posting_once(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-reader-backlog@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    add_posting(factory, user, text="   ", url=OTHER_URL, title="Data analyst")  # never read
    [run_id] = backlog(factory)
    other = new_user(factory, "e2e-reader-backlog-2@example.org")
    save_credential(factory, keyring, other)
    add_posting(factory, other)
    assert backlog(factory) == []  # one run at a time for the whole app
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.input["job_ids"] == [str(job_id)]
    assert execute(rc, run_id) is RunStatus.done
    job = job_of(factory, job_id)
    assert job.read_at == NOW
    assert job.location == "Barcelona, Spain"
    [next_run] = backlog(factory)  # the other person's turn: this posting is read
    with factory() as db:
        run = db.get(AgentRun, next_run)
        assert run is not None
        assert run.user_id == other


def test_the_backlog_gives_a_posting_up_after_three_attempts_failed_for_their_content(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-reader-fails@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    for attempt in range(1, reader.ATTEMPTS_MAX + 1):
        [run_id] = backlog(factory)
        end_run(factory, run_id, failures.NO_RESULT)
        assert job_of(factory, job_id).read_attempts == attempt
    assert backlog(factory) == []
    assert job_of(factory, job_id).read_at is None


# Failures that are not the run's own: the next run would fail the same way, whatever it reads.
NOT_ITS_OWN = [
    failures.NO_CREDENTIAL,
    failures.UNREADABLE,
    failures.UNUSABLE,
    failures.REFUSED,
    failures.BUSY,
    failures.NO_CREDIT,
    runs.QUEUE_DOWN,
    failures.API_ERROR.format(status=500),
    failures.API_ERROR.format(status=529),
    failures.NO_CLI,
    failures.NO_START,
    failures.INTERNAL,
    failures.UNKNOWN_AGENT,
    failures.PROCESS.format(code=1),
    failures.PROCESS.format(code="unknown"),
]
# The run's own, and the ends the reaper writes (the bound against a posting that kills the
# worker).
ITS_OWN = [
    failures.NO_RESULT,
    failures.TURN_LIMIT.format(turns=reader.MAX_TURNS),
    failures.TIME_LIMIT.format(minutes="10 minutes"),
    failures.TOKEN_LIMIT,
    failures.API_ERROR.format(status=400),
    failures.API_ERROR.format(status=413),
    failures.FAILED.format(reason="error_during_execution"),
    runs.INTERRUPTED,
    runs.STALE,
    runs.LOST,
    "A message no module wrote.",
]


@pytest.mark.parametrize(
    ("error", "back", "pause"),
    [(error, True, True) for error in NOT_ITS_OWN]
    + [(runs.STOPPED, True, False)]
    + [(error, False, False) for error in ITS_OWN],
)
def test_an_attempt_counts_only_for_a_run_that_failed_for_its_own_content(
    factory: sessionmaker[Session],
    run_context: tuple[RunContext, Keyring],
    error: str,
    back: bool,
    pause: bool,
) -> None:
    _, keyring = run_context
    user = new_user(factory, f"e2e-reader-end-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    read = add_posting(factory, user, url=OTHER_URL, title="Data analyst")
    [run_id] = backlog(factory)
    assert (job_of(factory, job_id).read_attempts, job_of(factory, job_id).read_run_id) == (
        1,
        run_id,
    )
    with factory() as db:
        db.execute(sa.update(Job).where(Job.id == read).values(read_at=NOW))
        db.commit()
    end_run(factory, run_id, error)
    with factory() as db:
        assert reader.settle_ended(db) == 1
        reader.settle(db, run_id)  # a second settle gives nothing back again
        assert reader.paused(db, user, NOW) is pause
        db.commit()
    job = job_of(factory, job_id)
    assert (job.read_attempts, job.read_run_id) == (0 if back else 1, None)
    assert job_of(factory, read).read_attempts == 1  # a posting read keeps its attempt
    with factory() as db:
        assert reader.settle_ended(db) == 0


@pytest.mark.parametrize("error", [failures.REFUSED, failures.BUSY, failures.NO_CLI])
def test_the_backlog_leaves_a_person_alone_for_a_pause_after_such_a_failure(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring], error: str
) -> None:
    _, keyring = run_context
    user = new_user(factory, f"e2e-reader-pause-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    [run_id] = backlog(factory)
    end_run(factory, run_id, error)
    other = new_user(factory, f"e2e-reader-pause-{secrets.token_hex(3)}@example.org")
    save_credential(factory, keyring, other)
    add_posting(factory, other)
    second = timedelta(seconds=1)
    [theirs] = backlog(factory, NOW + second)  # another person is not paused
    with factory() as db:
        db.execute(sa.update(Job).where(Job.user_id == other).values(read_at=NOW))
        db.commit()
    end_run(factory, theirs, failures.NO_RESULT)
    assert backlog(factory, NOW + reader.BACKLOG_PAUSE - second) == []
    assert job_of(factory, job_id).read_attempts == 0
    [again] = backlog(factory, NOW + reader.BACKLOG_PAUSE)
    with factory() as db:
        run = db.get(AgentRun, again)
        assert run is not None
        assert run.user_id == user
    assert job_of(factory, job_id).read_attempts == 1


def test_a_backlog_run_the_queue_refused_gives_its_attempt_back(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-reader-queue@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)

    class Down:
        async def enqueue_job(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("the queue is down")

    ctx = {"redis": Down(), "sessions": factory, "clock": lambda: NOW}
    assert asyncio.run(reader.tick(ctx)) == 1
    assert ended_runs(factory, user) == [(RunStatus.failed, runs.QUEUE_DOWN)]
    job = job_of(factory, job_id)
    assert (job.read_attempts, job.read_run_id) == (0, None)
    assert backlog(factory) == []  # paused


def test_the_backlog_waits_for_the_persons_sweep_and_needs_a_credential(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-reader-wait@example.org")
    add_posting(factory, user)
    assert backlog(factory) == []  # no credential
    save_credential(factory, keyring, user)
    helpers.set_profile(factory, user, helpers.search())
    sweep_id = helpers.new_sweep(factory, user)
    assert backlog(factory) == []  # the sweep runs
    with factory() as db:
        db.execute(
            sa.update(Sweep)
            .where(Sweep.id == sweep_id)
            .values(status=SweepStatus.done, finished_at=NOW)
        )
        db.commit()
    assert len(backlog(factory)) == 1
