"""A17 private bytes through complete fake modes, real arq jobs and captured output.

Only the invented owner's profile, answers, screenshots and fake provider may retain markers.
Retain arq results in this test to inspect the actual worker return payload; production keeps
these jobs for zero seconds. No marker value is included in assertion diagnostics.
"""

import asyncio
import base64
import dataclasses
import logging
import uuid

import pytest
import redis
import sqlalchemy as sa
from arq.constants import job_key_prefix
from arq.jobs import Job

from app import queue, worker
from app.apply import form_agent, gate, profile_gate, profile_sources, service, site_profile
from app.apply.models import (
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationScreenshot,
    Mode,
    SiteProfileShot,
    State,
)
from app.mail import gate as mail_gate
from app.profile import service as profiles
from tests.apply import a18_support
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.a6_support import GateLeases, submit_ashby
from tests.apply.conftest import AT
from tests.apply.test_a5_run import WHY, application, located, services
from tests.apply.test_a6_gate import approve, submit_services
from tests.apply.test_a9_manual import FakeChannel, manual_ashby, manual_services, record
from tests.apply.test_a13_full_auto import HOLD, HOUR, answered, turn_on
from tests.apply.test_a14_email import connect, prepare, provider
from tests.conftest import make_settings
from tests.jobs.test_worker import flush_redis, redis_url
from tests.mail import canary
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["redis_url"]


def no_leak(markers, *, logs=(), client=None, values=()):
    found = canary.find(markers, logs=logs, redis=client, values=values)
    assert len(found) == 0, [item.where for item in found]


def plant_name(db, person, value):
    profile = profiles.get_profile(db, person.user_id, AT)
    identity = dict(profile.sections["identity"])
    identity["full_name"] = value
    profiles.save_section(db, person.user_id, "identity", identity, profile.revision, AT)
    db.commit()


class QueueProof:
    def __init__(self, settings, client, markers):
        self.settings, self.client, self.markers = settings, client, markers
        self.job_id = ""
        self.observed = set()

    def observe(self, *args, **kwargs):
        if self.job_id and self.client.exists(job_key_prefix + self.job_id):
            self.observed.add(self.job_id)
        no_leak(self.markers, client=self.client)

    def run(self, ctx, name, *args):
        self.job_id = f"a17:{name}:{uuid.uuid4()}"

        async def execute():
            base = (
                worker.agents_queue()
                if name == "apply_decide"
                else worker.apply_queue(self.settings)
            )
            api_queue = queue.Queue(self.settings.redis_url.get_secret_value())
            try:
                assert await api_queue.enqueue(name, *args, queue=base.name, job_id=self.job_id)
                self.observe()
                functions = base.functions
                selected = next(f for f in functions if f.name == name)
                assert selected.keep_result_s == 0
                # Inspect a real arq result even though production intentionally discards it.
                spec = dataclasses.replace(
                    base,
                    functions=(dataclasses.replace(selected, keep_result_s=3600),),
                    cron_jobs=(),
                )
                process = worker.build_worker(spec, self.settings, ctx)
                process.burst = True
                try:
                    await process.main()
                finally:
                    await worker.close_worker(process)
                self.observe()
                pool = await api_queue.pool()
                result = await Job(
                    self.job_id, pool, _queue_name=base.name, _deserializer=queue.deserialize
                ).result_info()
                assert result is not None
                assert result.success
                assert result.result is None
                assert tuple(result.args) == args
                assert result.kwargs == {}
            finally:
                await api_queue.close()

        asyncio.run(execute())
        assert self.job_id in self.observed


@pytest.fixture
def proof(migrated_url, redis_url, forms_base_url, caplog, capfd):
    flush_redis(redis_url)
    markers = [canary.marker("a17") for _ in range(4)]
    caplog.set_level(logging.INFO)
    settings = make_settings(migrated_url, redis_url=redis_url, apply_stub_base_url=forms_base_url)
    client = redis.Redis.from_url(redis_url)
    checked = QueueProof(settings, client, markers)
    try:
        yield checked
        captured = capfd.readouterr()
        records = caplog.get_records("call")
        assert records, "the journey must capture executed worker logs"
        no_leak(markers, logs=records, client=client, values=[captured.out, captured.err])
    finally:
        client.close()
        flush_redis(redis_url)


@pytest.mark.parametrize("mode", ["manual", "review", "full_auto", "dry_run"])
def test_complete_application_modes_keep_private_bytes_out_of_worker_sinks(
    db, factory, person_a, forms_base_url, proof, mode
):
    name, image, token, capability = proof.markers
    plant_name(db, person_a, name)
    located(db, person_a)
    if mode == "full_auto":
        turn_on(db, person_a)
        from tests.apply.test_a13_full_auto import liked

        app_id = liked(db, person_a, forms_base_url)
    else:
        app_id = service.start(
            db,
            person_a.user_id,
            person_a.posting_id,
            Mode.manual if mode == "manual" else Mode.review,
            mode == "dry_run",
            now=AT,
            stub_base=forms_base_url,
        ).application.id
    if mode == "manual":
        form = manual_ashby(on_call=proof.observe)

        def sent():
            form.sent = True

        channel = FakeChannel(
            [
                "fill_details",
                "attach_cv",
                sent,
                None,
                lambda: record(factory, person_a, app_id, True),
            ]
        )
        env = manual_services(
            factory, form, channel, leases=FakeLeases(token=token), stub=forms_base_url
        )
        proof.run(
            {"apply_services": env.fill, "apply_manual_services": env},
            "apply_manual",
            str(app_id),
        )
        assert name in form.spec("Full name").value
        assert application(db, app_id).state is State.submitted
    else:
        form = run_ashby(on_call=proof.observe)
        form.shot += image.encode()
        proof.markers.append(base64.b64encode(form.shot).decode())
        agent = FakeAgent(
            lambda q: (
                ("I want to build agents for warehouse operations.", "motivation")
                if q.label == WHY
                else ("leave", "")
            )
        )
        fill = services(factory, form, FakeLeases(token=token), agent, stub_base=forms_base_url)
        proof.run({"apply_services": fill}, "apply_fill", str(app_id))
        assert application(db, app_id).state is State.ready
        assert name in form.spec("Full name").value
        assert db.scalar(
            sa.select(ApplicationScreenshot.jpeg).where(
                ApplicationScreenshot.application_id == app_id
            )
        ).endswith(image.encode())
        final = submit_ashby(on_call=proof.observe)
        final.shot += image.encode()
        submit = submit_services(
            factory,
            final,
            forms_base_url,
            leases=GateLeases(token=token, capability=capability),
        )
        if mode == "full_auto":
            answered(db, app_id)
            submit.clock = lambda: AT + HOUR
            proof.run({"apply_submit_services": submit}, "apply_decide", str(app_id))
            assert application(db, app_id).state is State.held
            submit.clock = lambda: AT + HOUR + HOLD
        else:
            assert approve(factory, person_a, app_id).passed
        proof.run({"apply_submit_services": submit}, "apply_submit", str(app_id))
        expected = State.rehearsed if mode == "dry_run" else State.submitted
        assert application(db, app_id).state is expected
        assert len(final.gate_clicks) == (0 if mode == "dry_run" else 1)
        assert (
            db.scalar(
                sa.select(ApplicationAnswer.value).where(
                    ApplicationAnswer.application_id == app_id,
                    ApplicationAnswer.label == "Full name",
                )
            )
            == name
        )
    no_leak(
        proof.markers,
        values=list(db.scalars(sa.select(ApplicationEvent.data))),
        client=proof.client,
    )


def test_profile_fill_approval_and_save_keep_private_bytes_out_of_worker_sinks(
    db, factory, person_a, proof, monkeypatch
):
    name, image, token, capability = proof.markers
    plant_name(db, person_a, name)
    portal = a18_support.Portal()
    portal.jpeg += image.encode()
    proof.markers.append(base64.b64encode(portal.jpeg).decode())
    env = a18_support.services(factory, portal)
    original_type = portal.type
    original_acquire = env.leases.acquire

    async def acquired(*args, **kwargs):
        got = await original_acquire(*args, **kwargs)
        return dataclasses.replace(got, token=token, capability=capability)

    async def observed_type(*args, **kwargs):
        proof.observe()
        return await original_type(*args, **kwargs)

    monkeypatch.setattr(portal, "type", observed_type)
    monkeypatch.setattr(env.leases, "acquire", acquired)
    run_id = site_profile.start(
        db, person_a.user_id, "https://portal.example.org/profile", now=AT
    ).id
    db.commit()

    async def fake_agent(session):
        return form_agent.Ran()

    proof.run(
        {"site_profile_services": env, "site_profile_agent": fake_agent},
        "site_profile_run",
        str(run_id),
    )
    run = site_profile.lock(db, person_a.user_id, run_id)
    part = site_profile.sections(db, run)[0]
    fields = site_profile.fields(db, run, part.section)
    assert next(row.value for row in fields if row.label == "Full name") == name
    assert part.state == "ready", part.error_code
    # The Save outline re-encodes this JPEG; the private source image itself is watched too.
    assert db.scalar(sa.select(SiteProfileShot.jpeg)) is not None
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, fields), AT
    )
    args = str(run_id), part.section, str(auth.id)
    db.commit()

    async def save(tools, ref, given, element=""):
        assert given == capability
        proof.observe()
        return await portal.save(tools, ref, "test-profile-capability", element)

    monkeypatch.setattr(profile_gate, "gate_click", save)
    proof.run({"site_profile_services": env}, "site_profile_save", *args)
    db.expire_all()
    part = site_profile.sections(db, site_profile.lock(db, person_a.user_id, run_id))[0]
    assert part.state == "saved"
    assert portal.saved["Full name"] == name
    assert portal.clicks == 1
    no_leak(proof.markers, values=[part.events])


def test_explicit_email_approval_and_fake_provider_capture_no_private_logs(
    db, factory, person_a, proof
):
    fake = FakeGoogle()
    fake.seed("alice")
    ring = connect(db, person_a, fake)
    application = prepare(db, person_a)
    body = proof.markers[0].translate(str.maketrans("0123456789", "ghijklmnop"))
    proof.markers.append(body)
    proof.run({"sessions": factory, "clock": lambda: AT}, "apply_fill", str(application.id))
    gate.set_answer(db, person_a.user_id, application.id, "email_body", body, AT)
    approval = gate.authorise_approval(
        db, person_a.user_id, application.id, gate.current_hash(db, application), AT, 86400
    )
    decision = gate.decide(
        db, person_a.user_id, approval.id, AT, switches=gate.Switches(enabled=True, paused=False)
    )
    assert decision.passed
    db.commit()
    auth = mail_gate.authorise_application(db, application, AT)
    db.commit()
    settings, provider_app, mcp = provider(fake, ring)

    async def send():
        async with provider_app.router.lifespan_context(provider_app):
            row = await mail_gate.send(
                factory,
                auth,
                AT,
                settings=settings,
                mcp=mcp,
                switches=gate.Switches(enabled=True, paused=False),
            )
            assert row.result == "sent", row.refusal_code

    asyncio.run(send())
    assert len(fake.sent) == 1
    proof.markers.append(fake.sent[0]["raw"])
    assert body.encode() in base64.urlsafe_b64decode(fake.sent[0]["raw"])
    assert application_state(db, application.id) is State.submitted
    no_leak(proof.markers, values=list(db.scalars(sa.select(ApplicationEvent.data))))


def application_state(db, application_id):
    return application(db, application_id).state
