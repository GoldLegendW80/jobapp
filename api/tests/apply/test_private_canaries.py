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
from app.apply import service
from app.apply.models import (
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationScreenshot,
    Mode,
    State,
)
from app.profile import service as profiles
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.a6_support import GateLeases, submit_ashby
from tests.apply.conftest import AT
from tests.apply.test_a5_run import WHY, application, located, services
from tests.apply.test_a6_gate import approve, submit_services
from tests.apply.test_a9_manual import FakeChannel, manual_ashby, manual_services, record
from tests.apply.test_a13_full_auto import HOLD, HOUR, answered, turn_on
from tests.conftest import make_settings
from tests.jobs.test_worker import flush_redis, redis_url
from tests.mail import canary

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


def application_state(db, application_id):
    return application(db, application_id).state
