"""The last approval and pause checks observe time after real lock waits."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.apply import caps, service, submit
from app.apply.caps_models import ApplySettings, LikeMode
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.jobs.models import Job
from tests.apply import a11_support as a11
from tests.apply.a6_support import submit_ashby
from tests.apply.conftest import AT
from tests.apply.test_a6_gate import approve, frozen, ready, run_submit, submit_services
from tests.apply.test_a13_full_auto import held, turn_on


class LockWait:
    """Advance the injected clock only when Postgres observes the tested waiter blocked."""

    def __init__(self, factory, statement, then):
        self.factory, self.statement, self.then = factory, statement, then
        self.locked, self.entering = threading.Event(), threading.Event()
        self.pid, self.future = None, None

    def __enter__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        return self

    def __exit__(self, *args):
        try:
            if self.future is not None:
                self.future.result(timeout=15)
        finally:
            self.pool.shutdown(wait=True)

    def owner(self):
        with self.factory() as owner:
            owner.execute(self.statement)
            self.locked.set()
            assert self.entering.wait(timeout=15)
            for _ in range(1000):
                if owner.scalar(sa.select(sa.func.pg_blocking_pids(self.pid))):
                    break
            else:
                pytest.fail("the final gate did not wait on the lock owner")
            self.then(owner)
            owner.commit()

    def start(self, db):
        assert self.future is None
        self.future = self.pool.submit(self.owner)
        assert self.locked.wait(timeout=15)
        self.pid = db.connection().exec_driver_sql("select pg_backend_pid()").scalar_one()
        self.entering.set()


def advisory(key):
    return sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0)))


def admit(db, app):
    app.state, app.updated_at, app.answers_hash = State.submitting, AT, b"h" * 32
    kind = (
        AuthorisationKind.full_auto_like
        if app.mode is Mode.full_auto
        else AuthorisationKind.person_approval
    )
    db.add(
        Authorisation(
            user_id=app.user_id,
            application_id=app.id,
            kind=kind,
            answers_hash=app.answers_hash,
            created_at=AT - timedelta(minutes=1),
            used_at=AT - timedelta(minutes=1),
            expires_at=AT + timedelta(days=1),
        )
    )
    a11.click(db, app, AT)


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("boundary", ["owner", "host"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("pause", [False, True])
@pytest.mark.parametrize("pace_case", ["clear", "new_click", "elapsed"])
def test_browser_final_locks_refresh_clock_and_live_pause(
    db,
    factory,
    person_a,
    person_b,
    forms_base_url,
    monkeypatch,
    mode,
    boundary,
    offset,
    pause,
    pace_case,
):
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = held(db, factory, person_a, forms_base_url)
    else:
        app_id = ready(db, factory, person_a, forms_base_url)
        assert approve(factory, person_a, app_id).passed
    db.expire_all()
    app = db.get(Application, app_id)
    authority = submit._spent(db, app)
    deadline = authority.expires_at
    auth_id, original = (
        authority.id,
        (authority.created_at, authority.expires_at, authority.used_at),
    )
    host = caps.host_of(app.ats, app.form_url)
    other = service.start(
        db, person_b.user_id, person_b.posting_id, Mode.review, False, now=AT
    ).application
    other.ats, other.form_url = app.ats, app.form_url
    other_id = other.id
    db.commit()
    current, paused = [deadline - timedelta(minutes=1)], [False]
    form = submit_ashby()
    env = submit_services(factory, form, forms_base_url)
    env.clock = lambda: current[0]

    async def live_pause():
        return paused[0]

    env.paused = live_pause

    def changed(owner):
        current[0] = deadline + timedelta(seconds=offset)
        paused[0] = pause
        if pace_case != "clear":
            owner.add(
                ApplicationEvent(
                    application_id=other_id,
                    user_id=person_b.user_id,
                    kind="click",
                    at=current[0]
                    - (caps.pace_of(host).interval if pace_case == "elapsed" else timedelta()),
                    data={},
                )
            )

    key = f"apply:{person_a.user_id}" if boundary == "owner" else f"apply_pace:{host}"
    with LockWait(factory, advisory(key), changed) as waiter:
        if boundary == "owner":
            original_bound = submit._bound

            def bound(session, plan):
                waiter.start(session)
                return original_bound(session, plan)

            monkeypatch.setattr(submit, "_bound", bound)
        else:
            original_reserve = caps.reserve

            def reserve(session, item, now, **kwargs):
                waiter.start(session)
                return original_reserve(session, item, now, **kwargs)

            monkeypatch.setattr(caps, "reserve", reserve)
        result = run_submit(env, app_id)
    expected = "paused" if pause else "authorisation_expired" if offset >= 0 else None
    db.expire_all()
    app, authority = db.get(Application, app_id), db.get(Authorisation, auth_id)
    assert (authority.created_at, authority.expires_at, authority.used_at) == original
    clicks = db.scalars(
        sa.select(ApplicationEvent).where(
            ApplicationEvent.application_id == app_id, ApplicationEvent.kind == "click"
        )
    ).all()
    paced = expected is None and pace_case == "new_click"
    assert len(form.gate_clicks) == len(clicks) == (0 if expected or paced else 1)
    if expected:
        assert app.error_code == expected
        assert app.state is (
            State.ready if mode is Mode.full_auto or not pause else State.cancelled
        )
        assert frozen(db, person_a.cv_version_id) == [None]
    elif paced:
        assert result == "paced"
        assert app.state is (State.approved if mode is Mode.review else State.held)
        assert app.needs == "host_pace"
    else:
        assert result == "submitted"
        assert clicks[0].at == current[0]


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "expiry",
        "paused",
        "pause_error",
        "rules",
        "closed",
        "applied",
        "documents",
        "settings",
        "full_auto_off",
        "fit",
        "source",
        "hash",
        "dry_run",
    ],
)
def test_browser_post_commit_stop_keeps_audit_without_click_or_freeze(
    db, factory, person_a, forms_base_url, monkeypatch, mode, fault
):
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = held(db, factory, person_a, forms_base_url)
    else:
        app_id = ready(db, factory, person_a, forms_base_url)
        assert approve(factory, person_a, app_id).passed
    db.expire_all()
    app = db.get(Application, app_id)
    authority = submit._spent(db, app)
    auth_id, original_used, deadline = authority.id, authority.used_at, authority.expires_at
    db.commit()
    current, pause = [AT + timedelta(minutes=15)], [False]
    form = submit_ashby()
    env = submit_services(factory, form, forms_base_url)
    env.clock = lambda: current[0]

    async def paused():
        if pause[0] == "error":
            raise ConnectionError("synthetic unavailable pause store")
        return bool(pause[0])

    env.paused = paused
    original_after = submit._after_arm

    async def after(*args):
        with factory() as session:
            if fault == "expiry":
                current[0] = deadline
            elif fault in {"paused", "pause_error"}:
                pause[0] = "error" if fault == "pause_error" else True
            elif fault == "rules":
                monkeypatch.setattr(submit.guardrails, "never_apply", lambda *a, **k: "never_apply")
            elif fault in {"closed", "applied"}:
                from app.jobs.models import JobStatus

                posting = session.get(Job, person_a.posting_id)
                posting.status = JobStatus.closed if fault == "closed" else JobStatus.applied
            elif fault == "documents":
                monkeypatch.setattr(submit.gate, "documents_code", lambda *a: "stale")
            elif fault == "settings":
                values = session.get(ApplySettings, person_a.user_id)
                if values is None:
                    session.add(ApplySettings(user_id=person_a.user_id, paused=True, updated_at=AT))
                else:
                    values.paused = True
            elif fault == "full_auto_off":
                values = session.get(ApplySettings, person_a.user_id)
                if values:
                    values.mode_after_like = LikeMode.review
            elif fault == "fit":
                session.get(Job, person_a.posting_id).fit = 0
            elif fault == "source":
                answer = session.scalars(
                    sa.select(ApplicationAnswer).where(
                        ApplicationAnswer.application_id == app_id,
                        ApplicationAnswer.kind == "text",
                    )
                ).first()
                answer.value = "Changed after the decision"
            elif fault == "hash":
                session.get(Application, app_id).answers_hash = b"x" * 32
            elif fault == "dry_run":
                session.get(Application, app_id).dry_run = True
            session.commit()
        return await original_after(*args)

    monkeypatch.setattr(submit, "_after_arm", after)
    result = run_submit(env, app_id)
    db.expire_all()
    app = db.get(Application, app_id)
    expected = {
        "expiry": "authorisation_expired",
        "paused": "paused",
        "pause_error": "paused",
        "rules": "never_apply",
        "closed": "posting_closed",
        "applied": "already_applied",
        "documents": "stale",
        "settings": "paused_by_you",
        "full_auto_off": "full_auto_off" if mode is Mode.full_auto else None,
        "fit": "below_min_fit" if mode is Mode.full_auto else None,
        "source": "stale",
        "hash": "stale",
        "dry_run": "stale",
    }.get(fault)
    assert len(form.gate_clicks) == (0 if expected else 1)
    assert db.get(Authorisation, auth_id).used_at == original_used
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(ApplicationEvent)
            .where(ApplicationEvent.application_id == app_id, ApplicationEvent.kind == "click")
        )
        == 1
    )
    if expected:
        # A refusal between the arm and the click ends as `recheck`'s before the arm: never
        # apply and a duplicate `blocked`, a closed posting `posting_closed`, the rest `ready`.
        state = {
            "never_apply": State.blocked,
            "already_applied": State.blocked,
            "posting_closed": State.posting_closed,
        }.get(expected, State.ready)
        assert (app.state, app.error_code, result) == (state, expected, state.value)
        assert app.submitted_at is None
        if fault != "applied":
            assert frozen(db, person_a.cv_version_id) == [None]
        else:  # an applied status freezes by its own reason (documents D14), never by apply
            assert frozen(db, person_a.cv_version_id) != ["apply"]
    else:
        assert (app.state, result) == (State.submitted, "submitted")


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("where", ["arm", "before_click"])
def test_a_pause_read_that_never_answers_under_the_gates_locks_reads_as_paused(
    db, factory, person_a, forms_base_url, monkeypatch, mode, where
):
    """The live pause read made while the gate's row locks are held (the arm's fresh switches,
    the checks again before the click) gives up after PAUSED_READ_S and reads as a pause: a hung
    Redis never keeps the locks until the job's timeout, and never lets the click through."""
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = held(db, factory, person_a, forms_base_url)
    else:
        app_id = ready(db, factory, person_a, forms_base_url)
        assert approve(factory, person_a, app_id).passed
    db.commit()
    form = submit_ashby()
    env = submit_services(factory, form, forms_base_url)
    env.clock = lambda: AT + timedelta(minutes=15)
    hung, reads = [False], {"hung": 0, "cancelled": 0}

    async def paused():
        if not hung[0]:
            return False
        reads["hung"] += 1
        try:
            await asyncio.Event().wait()  # Redis never answers
        except asyncio.CancelledError:
            reads["cancelled"] += 1
            raise
        return False

    env.paused = paused
    monkeypatch.setattr(submit, "PAUSED_READ_S", 0.05)
    if where == "arm":
        original_arm = submit.arm

        def arm(*args, **kwargs):
            hung[0] = True
            return original_arm(*args, **kwargs)

        monkeypatch.setattr(submit, "arm", arm)
    else:
        original_after = submit._after_arm

        async def after(*args):
            hung[0] = True
            return await original_after(*args)

        monkeypatch.setattr(submit, "_after_arm", after)
    result = run_submit(env, app_id)
    db.expire_all()
    app = db.get(Application, app_id)
    assert form.gate_clicks == []
    # One read only: the one under the locks gave up and refused, nothing read the pause again.
    assert reads == {"hung": 1, "cancelled": 1}
    # The arm's kill switch cancels a Review first item and hands a Full auto one to review;
    # between the arm and the click the item goes back to `ready`, its approval spent.
    state = State.cancelled if where == "arm" and mode is Mode.review else State.ready
    assert (app.state, app.error_code, result) == (state, "paused", state.value)
    assert app.submitted_at is None
    assert frozen(db, person_a.cv_version_id) == [None]


@pytest.mark.parametrize("pending", [State.held, State.approved, State.submitting, State.submitted])
@pytest.mark.parametrize("rule", ["company", "safety"])
def test_admitted_application_recheck_counts_competing_clicks_but_not_pending_approvals(
    db, person_a, pending, rule
):
    turn_on(db, person_a, safety_ceiling=1)
    if rule == "company":
        a11.cap(db, person_a.user_id, max_count=1)
    other = a11.clicked(db, person_a.user_id, AT, state=pending)
    if rule == "safety":
        db.get(Job, other.posting_id).company = "Another Example Company"
        other.form_url = "https://jobs.ashbyhq.com/another-example-company/101"
    posting = a11.posting(db, person_a.user_id, title="Different Robotics Role")
    app = a11.application(db, person_a.user_id, posting, mode=Mode.full_auto, state=State.held)
    db.flush()
    before = caps.check(db, app, AT)
    admit(db, app)
    after = caps.check(db, app, AT)
    expected = (
        ("company_cap" if rule == "company" else "safety_ceiling")
        if pending in (State.submitting, State.submitted)
        else None
    )
    assert before == after == expected


@pytest.mark.parametrize("other_count", [13, 14, 15])
@pytest.mark.parametrize("prior_own_click", [False, True])
@pytest.mark.parametrize("same_person", [False, True])
def test_linkedin_post_arm_daily_count_excludes_only_its_own_admitted_click(
    db, person_a, person_b, other_count, prior_own_click, same_person
):
    posting = a11.posting(db, person_a.user_id, title="Different Robotics Role")
    app = a11.application(db, person_a.user_id, posting, state=State.held)
    app.ats, app.form_url = "linkedin", "https://www.linkedin.com/jobs/view/101"
    other = a11.clicked(db, person_a.user_id if same_person else person_b.user_id, AT)
    other.ats, other.form_url = app.ats, "https://www.linkedin.com/jobs/view/102"
    for _ in range(other_count):
        db.add(
            ApplicationEvent(
                application_id=other.id, user_id=other.user_id, kind="click", at=AT, data={}
            )
        )
    db.flush()
    if prior_own_click:
        a11.click(db, app, AT - timedelta(minutes=10))
    assert caps.linkedin_daily(db, app, AT) == (other_count + prior_own_click >= 15)
    admit(db, app)
    assert caps.linkedin_daily(db, app, AT) == (other_count + prior_own_click >= 15)
