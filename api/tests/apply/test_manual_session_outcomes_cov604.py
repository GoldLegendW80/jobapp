"""Manual session outages retain the person's application and never submit a form."""

import asyncio
import json

import pytest

from app.apply import manual
from app.apply.models import State
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.jobs import service as jobs_service
from tests.apply.a5_support import FakeLeases
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, events
from tests.apply.test_a9_manual import (
    FakeChannel,
    frozen,
    manual_ashby,
    manual_services,
    session,
    start_manual,
)


@pytest.mark.parametrize("error", [BrowserError("unavailable"), StoreUnavailableError()])
def test_manual_lease_outage_preserves_queued_application_and_documents(
    db, factory, person_a, forms_base_url, error
):
    app_id = start_manual(db, person_a)
    form, leases = manual_ashby(), FakeLeases(error=error)
    env = manual_services(factory, form, FakeChannel(), leases=leases, stub=forms_base_url)
    assert session(env, app_id) == "lease_unavailable"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.queued, "browser_unavailable")
    assert form.calls == []
    assert leases.released == []
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]


def test_manual_restarted_lease_is_released_without_opening_the_form(
    db, factory, person_a, forms_base_url
):
    app_id = start_manual(db, person_a)
    form, leases = manual_ashby(), FakeLeases(token=None)
    env = manual_services(factory, form, FakeChannel(), leases=leases, stub=forms_base_url)
    assert session(env, app_id) == "lease_restarted"
    assert application(db, app_id).state is State.queued
    assert leases.released == [(str(person_a.user_id), "lease-1", "apply_restart")]
    assert form.calls == []
    assert frozen(db, person_a.cv_version_id) == [None]


def test_manual_command_crash_ends_session_with_reopenable_application(
    db, factory, person_a, forms_base_url
):
    app_id = start_manual(db, person_a)

    def unavailable():
        raise RuntimeError("synthetic command outage")

    form, leases = manual_ashby(), FakeLeases()
    env = manual_services(
        factory, form, FakeChannel([unavailable]), leases=leases, stub=forms_base_url
    )
    assert session(env, app_id) == "queued"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.queued, manual.ENDED)
    assert [event.data for event in events(db, app_id) if event.kind == "ended"] == [
        {"code": "internal_error"}
    ]
    assert len(leases.released) == 1
    assert form.submit_clicks == 0
    assert frozen(db, person_a.cv_version_id) == [None]


@pytest.mark.parametrize("error", [BrowserError("unavailable"), StoreUnavailableError()])
def test_manual_release_failure_keeps_the_persons_close_final(
    db, factory, person_a, forms_base_url, monkeypatch, error
):
    app_id = start_manual(db, person_a)
    form, leases = manual_ashby(), FakeLeases()

    async def unavailable(*args):
        raise error

    monkeypatch.setattr(leases, "release", unavailable)
    env = manual_services(factory, form, FakeChannel(), leases=leases, stub=forms_base_url)
    assert session(env, app_id) == "cancelled"
    assert (application(db, app_id).state, application(db, app_id).error_code) == (
        State.cancelled,
        "closed",
    )
    assert form.submit_clicks == 0
    assert frozen(db, person_a.cv_version_id) == [None]


def test_worker_restart_with_busy_posting_leaves_a_durable_recovery_marker(
    db, factory, person_a, monkeypatch
):
    app_id = start_manual(db, person_a)
    assert manual.begin(db, app_id, AT)
    db.commit()

    def busy(*args, **kwargs):
        raise jobs_service.JobBusyError

    monkeypatch.setattr(manual, "close", busy)
    assert manual.reset_interrupted(db, AT) == 0
    db.commit()
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (
        State.filling,
        manual.ENDED,
        "interrupted",
    )
    assert manual.ended_ids(db) == [app_id]
    assert frozen(db, person_a.cv_version_id) == [None]


class ChannelRedis:
    """Only the Redis boundary, keeping list entries and stored status visible to the test."""

    def __init__(self, commands=(), status=None):
        self.commands = list(commands)
        self.value = status
        self.expirations = []

    async def llen(self, key):
        return len(self.commands)

    async def rpush(self, key, command):
        self.commands.append(command)

    async def expire(self, key, seconds):
        self.expirations.append(seconds)

    async def blpop(self, keys, **kwargs):
        return (keys[0], self.commands.pop(0)) if self.commands else None

    async def get(self, key):
        return self.value

    async def set(self, key, value, ex):
        self.value = value
        self.expirations.append(ex)


def test_manual_channel_limits_commands_and_rejects_commands_outside_the_panel():
    redis = ChannelRedis(["attach_cv"] * manual.COMMANDS_MAX)
    channel = manual.RedisChannel(redis)
    assert not asyncio.run(channel.push("synthetic", "fill_details"))
    with pytest.raises(ValueError, match="submit"):
        asyncio.run(channel.push("synthetic", "submit"))
    assert redis.commands == ["attach_cv"] * manual.COMMANDS_MAX
    redis.commands.clear()
    assert asyncio.run(channel.push("synthetic", "attach_letter"))
    assert redis.commands == ["attach_letter"]
    assert redis.expirations == [manual.STATUS_TTL_S]


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(b"fill_details", "fill_details"), ("attach_cv", "attach_cv"), (b"submit", None)],
)
def test_manual_channel_consumes_only_supported_commands(stored, expected):
    redis = ChannelRedis([stored])
    channel = manual.RedisChannel(redis)
    assert asyncio.run(channel.pop("synthetic", 1)) == expected
    assert asyncio.run(channel.pop("synthetic", 1)) is None
    assert redis.commands == []


@pytest.mark.parametrize("stored", [None, "malformed json", "[]", "false"])
def test_manual_channel_corrupt_status_does_not_claim_an_open_session(stored):
    redis = ChannelRedis(status=stored)
    channel = manual.RedisChannel(redis)
    assert asyncio.run(channel.status("synthetic")) == {}
    status = {"phase": "ended", "state": "queued"}
    asyncio.run(channel.put_status("synthetic", status))
    assert json.loads(redis.value) == status
    assert asyncio.run(channel.status("synthetic")) == status
    assert redis.expirations == [manual.STATUS_TTL_S]
