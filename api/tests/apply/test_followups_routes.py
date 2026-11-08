"""Follow ups on a real Postgres at head (SPEC-apply.md, "Follow ups"): the list, "I sent it",
the cap of two for a silent application, the switch, the draft agent's click, its submit and its
end, the person's own text, and the Gmail draft that only the person's Send can send. Two people,
A and B, with invented postings at "Example Robotics"; every account is `e2e-*@example.org`.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.apply import followup_agent, followup_service
from app.apply.followup_models import FollowUp
from app.documents import consent
from app.mail.draft_models import MailDraft, MailSend
from app.mail.google import SCOPE_READONLY, SCOPE_SEND
from app.mail.models import AccountStatus, MailAccount
from app.profile import service as profile_service
from tests.agents.conftest import API_KEY, new_key
from tests.apply.test_start import (
    AT,
    USER_A,
    USER_B,
    a,
    api,
    app,
    b,
    engine,
    factory,
    migrated_url,
    posting,
)
from tests.profile.conftest import full

__all__ = ["a", "api", "app", "b", "engine", "factory", "migrated_url"]

TODAY = AT.date()
PATH = "/api/apply/followups"
CANARY = "e2e-followup-canary@example.org"


def applied(factory: sessionmaker[Session], user_id: uuid.UUID, days_ago: int, **kw) -> uuid.UUID:
    with factory() as db:
        return posting(
            db, user_id, status="applied", applied_on=TODAY - timedelta(days=days_ago), **kw
        )


def items(api, token: str) -> list[dict]:
    response = api.get(PATH, token=token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["items"]


def sent_rows(factory: sessionmaker[Session], posting_id: uuid.UUID) -> list[FollowUp]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(FollowUp).where(
                    FollowUp.posting_id == posting_id, FollowUp.sent_on.is_not(None)
                )
            )
        )


def ready_to_write(factory: sessionmaker[Session], user_id: uuid.UUID) -> None:
    with factory() as db:
        consent.record(db, user_id, AT)
        ring = Keyring.from_text(new_key())
        credentials.save(db, ring, user_id, CredentialKind.anthropic_api_key, API_KEY, AT)
        db.commit()


def with_profile(factory: sessionmaker[Session], user_id: uuid.UUID) -> None:
    with factory() as db:
        profile = profile_service.get_profile(db, user_id, AT)
        for section in ("identity", "experience", "skills"):
            content = full(section)
            if section == "identity":
                content = {**content, "email": CANARY}
            profile_service.save_section(db, user_id, section, content, profile.revision, AT)
            profile = profile_service.get_profile(db, user_id, AT)
        db.commit()


def test_the_list_shows_each_persons_own_followed_postings_due_first(api, factory, a, b):
    token_a, token_b = api.login_token(USER_A), api.login_token(USER_B)
    due = applied(factory, a, 9, title="Data Engineer")
    waiting = applied(factory, a, 2, title="Platform Engineer")
    with factory() as db:
        posting(db, a, title="Found Only")  # not applied: not followed
    other = applied(factory, b, 9)
    found = items(api, token_a)
    assert [item["posting_id"] for item in found] == [str(due), str(waiting)]
    first = found[0]
    assert (first["urgency"], first["kind"], first["count"], first["can_draft"]) == (
        "due",
        "follow_up",
        0,
        True,
    )
    assert first["next_on"] == (TODAY - timedelta(days=2)).isoformat()
    assert first["applied_from"] == "posting"
    assert first["draft"] is None
    assert found[1]["urgency"] == "waiting"
    assert [item["posting_id"] for item in items(api, token_b)] == [str(other)]
    assert api.post(f"{PATH}/{other}/sent", token=token_a).status_code == 404
    assert api.get(PATH).status_code == 401


def test_i_sent_it_moves_the_next_date_and_a_second_click_records_nothing(api, factory, a):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    first = api.post(f"{PATH}/{job}/sent", token=token)
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["count"], body["urgency"], body["sent"]) == (1, "waiting", [TODAY.isoformat()])
    assert body["next_on"] == (TODAY + timedelta(days=7)).isoformat()
    again = api.post(f"{PATH}/{job}/sent", token=token)
    assert again.status_code == 200
    assert again.json()["count"] == 1
    assert [(row.sent_on, row.channel) for row in sent_rows(factory, job)] == [(TODAY, "own_mail")]


def test_never_more_than_two_follow_ups_for_a_silent_application(api, factory, a):
    token = api.login_token(USER_A)
    job = applied(factory, a, 30)
    with factory() as db:
        for days_ago in (20, 10):
            db.add(
                FollowUp(
                    user_id=a,
                    posting_id=job,
                    sent_on=TODAY - timedelta(days=days_ago),
                    channel="own_mail",
                )
            )
        db.commit()
    (item,) = items(api, token)
    assert (item["urgency"], item["next_on"], item["can_draft"]) == ("cold", None, False)
    for path in ("sent", "draft"):
        refused = api.post(f"{PATH}/{job}/{path}", token=token)
        assert refused.status_code == 409
        assert refused.json()["code"] == "cold"
    assert len(sent_rows(factory, job)) == 2


def test_an_interview_asks_one_thank_you_note(api, factory, a):
    token = api.login_token(USER_A)
    with factory() as db:
        job = posting(db, a, status="interview", applied_on=TODAY - timedelta(days=12))
    (item,) = items(api, token)
    assert (item["kind"], item["urgency"]) == ("thank_you", "due")
    assert api.post(f"{PATH}/{job}/sent", token=token).json()["urgency"] == "done"


def test_the_switch_turns_follow_ups_off_and_on(api, factory, a):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    assert api.get(PATH, token=token).json()["enabled"] is True
    off = api.request("PUT", f"{PATH}/settings", token=token, json={"enabled": False})
    assert off.status_code == 200
    assert off.json() == {"enabled": False}
    read = api.get(PATH, token=token).json()
    assert (read["enabled"], read["items"]) == (False, [])
    assert api.post(f"{PATH}/{job}/draft", token=token).json()["code"] == "off"
    assert (
        api.request("PUT", f"{PATH}/settings", token=token, json={"enabled": "no"}).status_code
        == 422
    )
    api.request("PUT", f"{PATH}/settings", token=token, json={"enabled": True})
    assert len(items(api, token)) == 1


def test_a_draft_is_written_on_a_click_with_the_consent_and_a_credential(
    api, app, factory, a, monkeypatch
):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    queued: list[tuple] = []

    async def enqueue(function, *args, queue, job_id):
        queued.append((function, args, queue, job_id))
        return True

    monkeypatch.setattr(app.state.queue, "enqueue", enqueue)
    assert api.post(f"{PATH}/{job}/draft", token=token).json()["code"] == "consent"
    ready_to_write(factory, a)
    started = api.post(f"{PATH}/{job}/draft", token=token)
    assert started.status_code == 202, started.text
    assert started.json()["draft"]["writing"] is True
    ((function, (run_id,), queue, job_id),) = queued
    assert (function, queue, job_id) == ("run_agent", queues.AGENTS, f"agent-run:{run_id}")
    with factory() as db:
        run = db.get(AgentRun, uuid.UUID(run_id))
        assert (run.agent, run.job_id, run.status) == ("apply_followup", job, RunStatus.queued)
        assert set(run.input) == {"follow_up_id", "token_budget"}
    again = api.post(f"{PATH}/{job}/draft", token=token)
    assert again.status_code == 409
    assert again.json()["code"] == "running"


def test_a_queue_that_is_down_fails_the_run_and_says_so(api, app, factory, a, monkeypatch):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    ready_to_write(factory, a)

    async def down(*args, **kwargs):
        raise queues.QueueUnavailableError

    monkeypatch.setattr(app.state.queue, "enqueue", down)
    refused = api.post(f"{PATH}/{job}/draft", token=token)
    assert refused.status_code == 503
    assert refused.json()["code"] == "queue_down"
    (item,) = items(api, token)
    assert item["draft"]["writing"] is False
    assert item["draft"]["error"] == followup_agent.ERRORS["queue_down"]


def _bound_run(factory, user_id, job) -> tuple[uuid.UUID, dict]:
    with factory() as db:
        run_id = followup_service.start_draft(db, user_id, job, AT)
        db.commit()
        run = db.get(AgentRun, run_id)
        return run_id, dict(followup_agent.bind(db, run))


def test_the_agent_reads_no_contact_detail_and_its_submit_is_checked_once(factory, a):
    job = applied(factory, a, 9, title="Data Engineer")
    ready_to_write(factory, a)
    with_profile(factory, a)
    run_id, binding = _bound_run(factory, a, job)
    with factory() as db:
        data = json.loads(followup_agent.context(db, a, binding, AT))
        assert (data["task"], data["number"]) == ("follow_up", 1)
        assert data["posting"]["title"] == "Data Engineer"
        assert CANARY not in json.dumps(data)
    body = (
        "Hello,\n\nI applied for the Data Engineer role \u2014 I bring 40 years of Python.\n\n"
        "Would a short call this week suit you?"
    )
    raw = {"subject": "Data Engineer: following up", "body": body}
    with factory() as db:
        text, error = followup_agent.submit(db, a, run_id, binding, raw, AT)
        db.commit()
    assert error
    assert "40 years" in text
    with factory() as db:
        text, error = followup_agent.submit(db, a, run_id, binding, raw, AT)
        db.commit()
    assert (text, error) == (followup_agent.STORED, False)
    with factory() as db:
        followup_agent.finish(db, run_id, RunStatus.done, None)
        db.commit()
        row = db.scalar(sa.select(FollowUp).where(FollowUp.posting_id == job))
        assert "\u2014" not in row.body
        assert row.written_by == "agent"
        assert row.issues
        assert row.run_id is None
        assert row.error is None
    with factory() as db:
        assert followup_agent.submit(db, a, run_id, binding, raw, AT)[1] is True  # ended


def test_a_run_that_ends_without_a_text_says_why(factory, a):
    job = applied(factory, a, 9)
    ready_to_write(factory, a)
    run_id, _ = _bound_run(factory, a, job)
    with factory() as db:
        followup_agent.finish(db, run_id, RunStatus.failed, "boom")
        db.commit()
        row = db.scalar(sa.select(FollowUp).where(FollowUp.posting_id == job))
        assert (row.run_id, row.error) == (None, "failed")


def test_a_run_binds_only_to_the_draft_that_names_it(factory, a, b):
    job = applied(factory, a, 9)
    ready_to_write(factory, a)
    run_id, _ = _bound_run(factory, a, job)
    with factory() as db:
        run = db.get(AgentRun, run_id)
        db.expunge(run)
        assert followup_agent.bind(db, run) is not None
        for change in ({"user_id": b}, {"job_id": uuid.uuid4()}):
            fields = {k: getattr(run, k) for k in ("id", "user_id", "job_id", "input")}
            other = AgentRun(**{**fields, **change})
            assert followup_agent.bind(db, other) is None


def test_the_persons_own_text_is_kept_with_what_the_check_found(api, factory, a):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    saved = api.request(
        "PUT",
        f"{PATH}/{job}/draft",
        token=token,
        json={"subject": "Following up", "body": "I led a team of 75 people - twice."},
    )
    assert saved.status_code == 200, saved.text
    draft = saved.json()["draft"]
    assert draft["written_by"] == "person"
    assert " - " not in draft["body"]
    assert any(issue["code"] == "number_not_in_sources" for issue in draft["issues"])
    two_lines = {"subject": "a\nb", "body": "x"}
    assert api.request("PUT", f"{PATH}/{job}/draft", token=token, json=two_lines).status_code == 422


def test_gmail_makes_a_draft_that_only_the_persons_send_can_send(api, factory, a):
    token = api.login_token(USER_A)
    job = applied(factory, a, 9)
    text = {"subject": "Following up", "body": "Hello, I applied last week."}
    no_text = api.post(f"{PATH}/{job}/gmail", token=token, json={"to": "jobs@example.org"})
    assert no_text.json()["code"] == "not_connected"
    with factory() as db:
        db.add(
            MailAccount(
                user_id=a,
                email="e2e-apply-a2-a@example.org",
                google_sub=uuid.uuid4().hex,
                refresh_token_encrypted=b"sealed",
                key_version=1,
                scopes=[SCOPE_READONLY, SCOPE_SEND],
                connected_at=AT,
                status=AccountStatus.ok,
            )
        )
        db.commit()
    assert api.post(f"{PATH}/{job}/gmail", token=token, json={"to": "x"}).status_code == 422
    assert (
        api.post(f"{PATH}/{job}/gmail", token=token, json={"to": "jobs@example.org"}).json()["code"]
        == "no_text"
    )
    api.request("PUT", f"{PATH}/{job}/draft", token=token, json=text)
    made = api.post(f"{PATH}/{job}/gmail", token=token, json={"to": "Jobs@Example.org"})
    assert made.status_code == 200, made.text
    draft_id = uuid.UUID(made.json()["draft_id"])
    with factory() as db:
        draft = db.get(MailDraft, draft_id)
        assert (draft.kind, draft.status, draft.written_by) == ("reply", "draft", "user")
        assert draft.to_addrs == ["Jobs@example.org"]
        assert draft.body == text["body"]
        assert db.scalar(sa.select(sa.func.count()).select_from(MailSend)) == 0
        draft.status, draft.sent_at = "sent", AT  # what the gate records after the person's Send
        db.commit()
    (item,) = items(api, token)
    assert (item["count"], item["draft"]) == (1, None)
    assert [(row.channel) for row in sent_rows(factory, job)] == ["gmail"]


@pytest.mark.parametrize("bad", ["not-a-uuid", "8c1e4c35-1f3e-4a4b-9a55-0d6f2f1c9b10"])
def test_an_unknown_posting_is_not_followed(api, a, bad):
    token = api.login_token(USER_A)
    refused = api.post(f"{PATH}/{bad}/sent", token=token)
    assert refused.status_code == 404
    assert refused.json()["code"] == "not_followed"
