"""The posting reader (app/jobs/reader.py, reader_tools.py; jobs J19, issue #764), against real
Postgres: the words, quotes and cut tables; what a save stores, drops and keeps (hand set
fields, duplicates, the VIE website's contract, the fit again); a sweep that reads the postings
it stored with the fake agent backend, one without a credential, one out of time; the backlog
read once, given up after three attempts, never during the person's sweep. Postings, employers
and places are invented."""

import dataclasses
import logging
import secrets
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import runs
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentRun, RunStatus
from app.agents.runner import RunContext
from app.browser.finder import _words
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


def save(factory: sessionmaker[Session], user_id: uuid.UUID, job_id: uuid.UUID, **over: object):
    return reader.save(factory, user_id, {"jobs": [str(job_id)]}, facts(**over), NOW)


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
    reader.save(factory, user, {"jobs": [str(other)]}, facts(contract_type=contract), NOW)
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
        {"jobs": [str(job_id)]},
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
        done = reader.save(factory, user, {"jobs": [str(job_id)]}, raw, NOW)
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
    reader.save(factory, user, {"jobs": [str(manual)]}, facts(), NOW)
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
        assert reader.bind_postings(db, run) == {"jobs": [str(mine)]}
        run = AgentRun(user_id=user, input={"job_ids": [str(theirs)]})
        assert reader.bind_postings(db, run) is None


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


def backlog(factory: sessionmaker[Session]) -> list[uuid.UUID]:
    with factory() as db:
        started = reader.start_backlog(db, NOW)
        db.commit()
        return started


def test_the_backlog_reads_a_saved_posting_once(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-reader-backlog@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    add_posting(factory, user, text="   ", url=OTHER_URL, title="Data analyst")  # never read
    [run_id] = backlog(factory)
    assert backlog(factory) == []  # one batch at a time
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert run.input["job_ids"] == [str(job_id)]
    assert execute(rc, run_id) is RunStatus.done
    job = job_of(factory, job_id)
    assert job.read_at == NOW
    assert job.location == "Barcelona, Spain"
    assert backlog(factory) == []


def test_the_backlog_gives_a_posting_up_after_three_failed_attempts(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-reader-fails@example.org")
    save_credential(factory, keyring, user)
    job_id = add_posting(factory, user)
    for attempt in range(1, reader.ATTEMPTS_MAX + 1):
        [run_id] = backlog(factory)
        with factory() as db:
            assert runs.fail_queued(db, run_id, runs.LOST, NOW)
            db.commit()
        assert job_of(factory, job_id).read_attempts == attempt
    assert backlog(factory) == []
    assert job_of(factory, job_id).read_at is None


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
