"""The mail provider accepts one exact request, for one minute."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.mail import send_key


def test_exact_request_and_expiry() -> None:
    now = datetime(2026, 10, 22, tzinfo=UTC)
    secret = "synthetic-test-key-" * 4
    send_id, user_id, draft_id = uuid4(), uuid4(), uuid4()
    key = send_key.mint(
        secret, send_id, user_id, "e2e-a@example.org", draft_id, None, "content", b"message", now
    )
    payload = send_key.verify(secret, key, str(user_id), "e2e-a@example.org", None, b"message", now)
    assert payload["send_id"] == str(send_id)
    for user, address, thread, raw, at in (
        (str(uuid4()), "e2e-a@example.org", None, b"message", now),
        (str(user_id), "e2e-b@example.org", None, b"message", now),
        (str(user_id), "e2e-a@example.org", "other-thread", b"message", now),
        (str(user_id), "e2e-a@example.org", None, b"changed", now),
        (str(user_id), "e2e-a@example.org", None, b"message", now + timedelta(seconds=60)),
    ):
        with pytest.raises(send_key.InvalidKeyError):
            send_key.verify(secret, key, user, address, thread, raw, at)


@pytest.mark.parametrize("key", ["", "garbage", "a.b", "a.b.c", "{}", "x" * 8193])
def test_invalid_keys_fail_closed(key: str) -> None:
    with pytest.raises(send_key.InvalidKeyError):
        send_key.verify(
            "synthetic-test-key-" * 4,
            key,
            str(uuid4()),
            "e2e-a@example.org",
            None,
            b"x",
            datetime.now(UTC),
        )


def test_unconfigured_process_cannot_mint() -> None:
    with pytest.raises(send_key.InvalidKeyError):
        send_key.mint(
            None,
            uuid4(),
            uuid4(),
            "e2e-a@example.org",
            uuid4(),
            None,
            "hash",
            b"x",
            datetime.now(UTC),
        )


def test_worker_refuses_to_start_with_the_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker
    from app.config import ConfigError, Settings, refuse_send_key

    url = "postgresql+psycopg://jobapp:secret@db:5432/jobapp"
    refuse_send_key(Settings(database_url=url))  # type: ignore[arg-type]
    held = Settings(database_url=url, mail_send_key="synthetic-test-key-" * 4)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="MAIL_SEND_KEY is refused in the worker"):
        refuse_send_key(held)
    started: list[object] = []
    monkeypatch.setattr(worker, "get_settings", lambda: held)
    monkeypatch.setattr(worker, "serve", lambda *a: started.append(a))
    monkeypatch.setattr(worker, "check", lambda *a: started.append(a))
    # Neither the worker nor its healthcheck runs: the container fails to start.
    for argv in ([], ["--check"]):
        with pytest.raises(ConfigError, match="MAIL_SEND_KEY"):
            worker.main(argv)
    assert started == []
