"""The fake Google itself (mail M1): the shapes later tasks lean on, its search, its control
switches, the seed mailboxes, and the same code run as a service on a loopback port."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from tests.mail import fake_gmail
from tests.mail.conftest import ALICE, BOB, BULK
from tests.mail.fake_gmail import ALL_SCOPES, SCOPE_READONLY, FakeGoogle, b64url

GMAIL = "/gmail/v1/users/me"


def _get(fake: FakeGoogle, token: str, path: str) -> tuple[int, Any]:
    reply = fake.handle("GET", path, {"authorization": f"Bearer {token}"})
    body = reply.body if isinstance(reply.body, bytes) else b"".join(reply.body)
    return reply.status, json.loads(body)


def _refresh_form(fake: FakeGoogle, refresh_token: str) -> str:
    return (
        f"grant_type=refresh_token&refresh_token={refresh_token}"
        f"&client_id={fake.client_id}&client_secret={fake.client_secret}"
    )


def _access(fake: FakeGoogle, email: str, scopes: tuple[str, ...] = ALL_SCOPES) -> str:
    form = _refresh_form(fake, fake.grant(email, scopes))
    reply = fake.handle("POST", "/token", {}, form.encode())
    assert reply.status == 200
    return json.loads(reply.body)["access_token"]  # type: ignore[arg-type]


def _ids(fake: FakeGoogle, token: str, q: str) -> set[str]:
    from urllib.parse import quote

    status, found = _get(fake, token, f"{GMAIL}/messages?maxResults=500&q={quote(q)}")
    assert status == 200, found
    return {item["id"] for item in found.get("messages", [])}


# --- the seed -------------------------------------------------------------------------------


def test_the_seed_holds_every_thread_the_journeys_name(fake: FakeGoogle) -> None:
    alice = fake.mailboxes[ALICE]
    threads = {message["threadId"] for message in alice.messages.values()}
    assert {
        "t-mistral",
        "t-ambiguous",
        "t-invitation",
        "t-invitation-fr",
        "t-pdf",
        "t-hostile",
        "t-huge",
    } <= threads
    assert sum(1 for message in alice.messages.values() if message["threadId"] == "t-huge") == 120
    assert alice.messages["m-pdf-1"]["attachments"][0]["filename"] == "interview-details.pdf"
    bob = fake.mailboxes[BOB]
    # The second mailbox mentions the same companies and shares no thread with the first.
    for company in ("Mistral AI", "Northwind Robotics", "Lumen Labs"):
        assert any(company in message.get("text", "") for message in bob.messages.values())
    assert not {m["threadId"] for m in bob.messages.values()} & threads - {"t-shared-id"}
    bulk = fake.seed("bulk")
    assert bulk.email == BULK
    assert len(bulk.messages) == 300
    assert [m["id"] for m in fake.seed("bulk").messages.values()] == [
        m["id"] for m in bulk.messages.values()
    ]
    assert [m["text"] for m in FakeGoogle().seed("bulk").messages.values()] == [
        m["text"] for m in bulk.messages.values()
    ]


def test_the_seed_names_synthetic_people_and_reserved_domains_only() -> None:
    allowed = {
        "example.org",
        "ashbyhq.com",
        "mistral.ai",
        "mail.gmail.com",
        "mx.google.com",
        "google.com",
    }
    for path in sorted(fake_gmail.FIXTURES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for domain in set(fake_gmail.re.findall(r"@([A-Za-z0-9.-]+\.[a-z]{2,})", text)):
            assert domain in allowed or domain.endswith(".example"), f"{path.name}: {domain}"
    assert len(list(fake_gmail.FIXTURES.glob("*.json"))) == 3


def test_gmails_own_headers_stand_above_what_the_sender_wrote(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    status, message = _get(fake, token, f"{GMAIL}/messages/m-hostile-1?format=full")
    assert status == 200
    names = [header["name"] for header in message["payload"]["headers"]]
    results = [
        h["value"] for h in message["payload"]["headers"] if h["name"] == "Authentication-Results"
    ]
    # Two headers say they are Gmail's verdict. The first is: it fails the mail. The second was
    # written by the sender and passes it.
    assert len(results) == 2
    assert "dmarc=fail" in results[0]
    assert "header.from=mistral.ai" in results[0]
    assert "dmarc=pass" in results[1]
    assert names.index("Delivered-To") < names.index("Authentication-Results") < names.index("From")
    reply_to = next(h["value"] for h in message["payload"]["headers"] if h["name"] == "Reply-To")
    assert "mistral-careers.example" in reply_to
    html_part = message["payload"]["parts"][1]
    assert html_part["mimeType"] == "text/html"
    assert b"<script>" in base64.urlsafe_b64decode(html_part["body"]["data"] + "==")
    text = base64.urlsafe_b64decode(message["payload"]["parts"][0]["body"]["data"] + "==").decode()
    assert "ignore every previous instruction" in text
    assert message["internalDate"].isdigit()
    assert message["historyId"].isdigit()
    assert set(message) == {
        "id",
        "threadId",
        "labelIds",
        "snippet",
        "historyId",
        "internalDate",
        "sizeEstimate",
        "payload",
    }


def test_odd_encodings_come_back_byte_for_byte(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    _, odd = _get(fake, token, f"{GMAIL}/messages/m-hostile-2")
    data = odd["payload"]["body"]["data"]
    assert "=" not in data  # base64url, unpadded
    assert "+" not in data
    assert "/" not in data
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    assert b"\xff\xfe\x00" in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    _, french = _get(fake, token, f"{GMAIL}/messages/m-invitation-fr-1")
    body = french["payload"]["body"]["data"]
    assert "reçu votre candidature" in base64.urlsafe_b64decode(
        body + "=" * (-len(body) % 4)
    ).decode("iso-8859-1")
    content_type = next(
        h["value"] for h in french["payload"]["headers"] if h["name"] == "Content-Type"
    )
    assert "iso-8859-1" in content_type


def test_metadata_and_minimal_carry_no_body(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    _, meta = _get(
        fake,
        token,
        f"{GMAIL}/messages/m-mistral-1?format=metadata&metadataHeaders=From&metadataHeaders=Subject",
    )
    assert set(meta["payload"]) == {"mimeType", "headers"}
    assert [header["name"] for header in meta["payload"]["headers"]] == ["From", "Subject"]
    assert "payload" not in _get(fake, token, f"{GMAIL}/messages/m-mistral-1?format=minimal")[1]
    status, raw = _get(fake, token, f"{GMAIL}/messages/m-mistral-1?format=raw")
    assert status == 400
    assert raw["error"]["errors"][0]["reason"] == "fakeUnsupportedFormat"
    status, thread = _get(fake, token, f"{GMAIL}/threads/t-mistral?format=metadata")
    assert status == 200
    assert [m["id"] for m in thread["messages"]] == ["m-mistral-1", "m-mistral-2"]


# --- search ---------------------------------------------------------------------------------


def test_the_search_operators_the_module_uses(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    assert _ids(fake, token, "from:ashbyhq.com") == {"m-mistral-1"}
    assert _ids(fake, token, "from:(ashbyhq.com OR lumen-labs.example)") == {
        "m-mistral-1",
        "m-invitation-2",
        "m-pdf-1",
    }
    assert _ids(
        fake, token, "{from:ashbyhq.com from:mistral.ai} -from:mistral-careers.example"
    ) >= {"m-mistral-1", "m-mistral-2"}
    assert _ids(fake, token, 'subject:"thanks for applying"') == {"m-mistral-1", "m-mistral-2"}
    assert _ids(fake, token, "entretien OR interview") >= {
        "m-invitation-2",
        "m-invitation-fr-1",
        "m-pdf-1",
    }
    assert _ids(fake, token, "has:attachment") == {"m-pdf-1"}
    assert _ids(fake, token, "filename:pdf from:lumen-labs.example") == {"m-pdf-1"}
    assert _ids(fake, token, "in:sent") == {"m-invitation-1"}
    assert _ids(fake, token, "to:jonas.weber@lumen-labs.example") == {"m-invitation-1"}
    assert _ids(fake, token, "is:unread") == {"m-invitation-2"}
    assert _ids(fake, token, "rfc822msgid:<m-mistral-2@mistral.ai>") == {"m-mistral-2"}
    assert _ids(fake, token, "rfc822msgid:m-mistral-2@mistral.ai") == {"m-mistral-2"}
    # Spam and trash stay out unless they are asked for.
    assert "m-spam-1" not in _ids(fake, token, "application")
    assert _ids(fake, token, "in:spam") == {"m-spam-1"}
    assert {"m-spam-1", "m-trash-1"} <= _ids(fake, token, "in:anywhere application")
    # Dates: the fake's clock is 2026-10-13 12:00 UTC.
    assert _ids(
        fake, token, "after:2026/10/09 before:2026/10/10 -from:harbor-analytics.example"
    ) == {"m-pdf-1"}
    assert "m-mistral-1" not in _ids(fake, token, "newer_than:10d")
    assert "m-mistral-1" in _ids(fake, token, "newer_than:180d")
    assert "m-mistral-1" in _ids(fake, token, "older_than:10d")
    # A word is a whole word, as in Gmail: "interview" does not match "interviewer".
    assert _ids(fake, token, "mistra") == set()


def test_a_search_the_fake_cannot_judge_fails_loudly(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    for q in ("category:promotions", "larger:5M", "from:(a", "newer_than:soon", "has:drive"):
        from urllib.parse import quote

        status, found = _get(fake, token, f"{GMAIL}/messages?q={quote(q)}")
        assert status == 400, q
        assert found["error"]["errors"][0]["reason"] == "fakeUnsupportedQuery"


def test_pages_are_newest_first_and_a_token_of_another_search_is_refused(fake: FakeGoogle) -> None:
    fake.seed("bulk")
    token = _access(fake, BULK)
    _, first = _get(fake, token, f"{GMAIL}/messages?maxResults=120")
    _, second = _get(
        fake, token, f"{GMAIL}/messages?maxResults=120&pageToken={first['nextPageToken']}"
    )
    _, third = _get(
        fake, token, f"{GMAIL}/messages?maxResults=120&pageToken={second['nextPageToken']}"
    )
    assert (len(first["messages"]), len(second["messages"]), len(third["messages"])) == (
        120,
        120,
        60,
    )
    assert "nextPageToken" not in third
    assert first["resultSizeEstimate"] == 300
    dates = [int(fake.mailboxes[BULK].messages[m["id"]]["internalDate"]) for m in first["messages"]]
    assert dates == sorted(dates, reverse=True)
    status, _ = _get(
        fake, token, f"{GMAIL}/messages?q=interview&pageToken={first['nextPageToken']}"
    )
    assert status == 400
    assert _get(fake, token, f"{GMAIL}/messages?q=nothingmatchesthisword")[1] == {
        "resultSizeEstimate": 0
    }


# --- errors, scopes, grants -----------------------------------------------------------------


def test_error_bodies_have_googles_shape(fake: FakeGoogle) -> None:
    token = _access(fake, ALICE)
    for kind, status, reason, word in [
        ("429", 429, "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
        ("403_rate", 403, "rateLimitExceeded", "PERMISSION_DENIED"),
        ("403_user_rate", 403, "userRateLimitExceeded", "PERMISSION_DENIED"),
        ("500", 500, "backendError", "INTERNAL"),
        ("503", 503, "backendError", "UNAVAILABLE"),
        ("404", 404, "notFound", "NOT_FOUND"),
    ]:
        fake.fail("getProfile", kind, retry_after=7)
        reply = fake.handle("GET", f"{GMAIL}/profile", {"authorization": f"Bearer {token}"})
        found = json.loads(reply.body)["error"]  # type: ignore[arg-type]
        assert (reply.status, found["code"], found["status"]) == (status, status, word)
        assert found["errors"][0]["reason"] == reason
        assert found["errors"][0]["domain"]
        assert reply.headers["Retry-After"] == "7"
    assert _get(fake, token, f"{GMAIL}/profile")[0] == 200  # a failure is used once


def test_a_grant_without_the_read_scope_is_refused_and_a_revoked_one_is_dead(
    fake: FakeGoogle,
) -> None:
    narrow = _access(fake, ALICE, scopes=("openid",))
    status, found = _get(fake, narrow, f"{GMAIL}/messages")
    assert status == 403
    assert found["error"]["errors"][0]["reason"] == "insufficientPermissions"
    token = _access(fake, ALICE)
    assert _get(fake, token, f"{GMAIL}/profile")[0] == 200
    fake.handle("POST", "/__control/revoke", {}, json.dumps({"email": ALICE}).encode())
    assert _get(fake, token, f"{GMAIL}/profile")[0] == 401
    form = _refresh_form(fake, "1//gone")
    reply = fake.handle("POST", "/token", {}, form.encode())
    assert (reply.status, json.loads(reply.body)["error"]) == (400, "invalid_grant")  # type: ignore[arg-type]
    wrong = fake.handle("POST", "/token", {}, form.replace(fake.client_secret, "nope").encode())
    assert (wrong.status, json.loads(wrong.body)["error"]) == (401, "invalid_client")  # type: ignore[arg-type]


def test_the_consent_page_refuses_what_google_would_and_a_request_without_pkce(
    fake: FakeGoogle,
) -> None:
    from urllib.parse import urlencode

    ask = {
        "client_id": fake.client_id,
        "redirect_uri": "http://localhost/api/mail/oauth/callback",
        "response_type": "code",
        "scope": " ".join(ALL_SCOPES),
        "state": "s",
        "code_challenge": "c" * 43,
        "code_challenge_method": "S256",
    }
    page = fake.handle("GET", "/o/oauth2/v2/auth?" + urlencode(ask), {})
    assert page.status == 200
    body = page.body.decode()  # type: ignore[union-attr]
    assert body.count('type="checkbox"') == 3
    assert 'name="account"' in body
    assert ">Allow</button>" in body
    assert ">Deny</button>" in body
    for change, title in [
        ({"client_id": "someone-else"}, b"invalid_client"),
        (
            {"redirect_uri": "http://evil.example/api/mail/oauth/callback/x"},
            b"redirect_uri_mismatch",
        ),
        ({"code_challenge_method": "plain"}, b"PKCE"),
        ({"response_type": "token"}, b"invalid_request"),
    ]:
        refused = fake.handle("GET", "/o/oauth2/v2/auth?" + urlencode({**ask, **change}), {})
        assert refused.status in (400, 401)  # type: ignore[operator]
        assert title in refused.body
    deny = {**ask, "decision": "deny", "account": ALICE}
    denied = fake.handle("POST", "/o/oauth2/v2/auth/decision", {}, urlencode(deny).encode())
    assert denied.status == 302
    assert (
        denied.headers["Location"]
        == "http://localhost/api/mail/oauth/callback?state=s&error=access_denied"
    )
    unknown = {**ask, "decision": "allow", "account": "nobody@example.org"}
    assert (
        fake.handle("POST", "/o/oauth2/v2/auth/decision", {}, urlencode(unknown).encode()).status
        == 400
    )


# --- messages.send: what M12 and M23 are tested against -------------------------------------


def _raw(message_id: str | None, marker: str) -> str:
    mail = EmailMessage()
    mail["From"] = ALICE
    mail["To"] = "Jonas Weber <jonas.weber@lumen-labs.example>"
    mail["Subject"] = "Re: Application, Applied AI Engineer"
    mail["In-Reply-To"] = "<m-invitation-2@lumen-labs.example>"
    mail["X-Jobapp-Send"] = marker
    if message_id:
        mail["Message-ID"] = message_id
    mail.set_content("Tuesday 10:00 suits me.\n\nAlice")
    return b64url(mail.as_bytes())


def _send(fake: FakeGoogle, token: str, body: dict[str, Any]) -> fake_gmail.Reply:
    return fake.handle(
        "POST",
        f"{GMAIL}/messages/send",
        {"authorization": f"Bearer {token}"},
        json.dumps(body).encode(),
    )


def test_messages_send_keeps_the_message_id_and_the_header_and_answers_rfc822msgid(
    fake: FakeGoogle,
) -> None:
    token = _access(fake, ALICE)
    reply = _send(
        fake,
        token,
        {"raw": _raw("<send-1@jobapp.example>", "send-one"), "threadId": "t-invitation"},
    )
    answer = json.loads(reply.body)  # type: ignore[arg-type]
    assert reply.status == 200
    assert answer["threadId"] == "t-invitation"
    assert answer["labelIds"] == ["SENT"]
    assert _ids(fake, token, "rfc822msgid:<send-1@jobapp.example>") == {answer["id"]}
    _, stored = _get(fake, token, f"{GMAIL}/messages/{answer['id']}?format=metadata")
    headers = {h["name"]: h["value"] for h in stored["payload"]["headers"]}
    assert headers["Message-ID"] == "<send-1@jobapp.example>"
    assert headers["X-Jobapp-Send"] == "send-one"
    sent = json.loads(fake.handle("GET", f"/__control/sent?email={ALICE}", {}).body)["sent"]  # type: ignore[arg-type]
    assert [item["messageId"] for item in sent] == ["<send-1@jobapp.example>"]
    assert base64.urlsafe_b64decode(sent[0]["raw"] + "==").startswith(b"From: ")
    assert fake.handle("GET", f"/__control/sent?email={BOB}", {}).body == b'{"sent": []}'
    # A thread that is not the user's, a body without `raw`, a grant without gmail.send.
    assert _send(fake, token, {"raw": _raw(None, "x"), "threadId": "b-t-invitation"}).status == 404
    assert _send(fake, token, {"threadId": "t-invitation"}).status == 400
    readonly = _access(fake, ALICE, scopes=("openid", SCOPE_READONLY))
    assert _send(fake, readonly, {"raw": _raw(None, "x")}).status == 403
    assert len(fake.sent) == 1


def test_the_switch_makes_the_fake_replace_the_message_id_or_drop_the_answer(
    fake: FakeGoogle,
) -> None:
    token = _access(fake, ALICE)
    fake.handle("POST", "/__control/switch", {}, b'{"name": "send_message_id", "value": "replace"}')
    reply = _send(fake, token, {"raw": _raw("<send-2@jobapp.example>", "send-two")})
    answer = json.loads(reply.body)  # type: ignore[arg-type]
    assert _ids(fake, token, "rfc822msgid:<send-2@jobapp.example>") == set()
    assert fake.sent[-1]["messageIdGiven"] == "<send-2@jobapp.example>"
    assert fake.sent[-1]["messageId"].endswith("@mail.gmail.com>")
    assert fake.sent[-1]["headers"]["X-Jobapp-Send"] == "send-two"  # the header survives
    assert _ids(fake, token, f"rfc822msgid:{fake.sent[-1]['messageId']}") == {answer["id"]}
    fake.switch("send_message_id", "keep")
    fake.switch("send_answer", "drop")
    dropped = _send(fake, token, {"raw": _raw("<send-3@jobapp.example>", "send-three")})
    assert dropped.fault == "drop"  # the mail left, the answer never came
    assert len(_ids(fake, token, "rfc822msgid:<send-3@jobapp.example>")) == 1
    fake.switch("send_answer", "error")
    assert _send(fake, token, {"raw": _raw("<send-4@jobapp.example>", "send-four")}).status == 503
    assert len(_ids(fake, token, "rfc822msgid:<send-4@jobapp.example>")) == 1
    with pytest.raises(ValueError, match="send_answer"):
        fake.switch("send_answer", "sometimes")
    assert [call["status"] for call in fake.calls_of("messages.send")] == [200, "drop", 503]


def test_the_control_api_seeds_changes_and_reports(fake: FakeGoogle) -> None:
    def control(name: str, body: dict[str, Any]) -> Any:
        reply = fake.handle("POST", f"/__control/{name}", {}, json.dumps(body).encode())
        assert reply.status == 200, reply.body
        return json.loads(reply.body)  # type: ignore[arg-type]

    assert control("mailboxes", {"name": "bulk"})["messages"] == 300
    made = control(
        "mailboxes",
        {
            "email": "e2e-carol@example.org",
            "messages": [{"id": "c1", "headers": [["Subject", "Hello"]], "text": "hello"}],
        },
    )
    assert made == {"email": "e2e-carol@example.org", "messages": 1, "historyId": "1001"}
    token = _access(fake, "e2e-carol@example.org")
    added = control(
        "messages",
        {
            "email": "e2e-carol@example.org",
            "message": {"headers": [["Subject", "Second"]], "text": "second"},
        },
    )
    control("labels", {"email": "e2e-carol@example.org", "id": added["id"], "add": ["STARRED"]})
    _, history = _get(fake, token, f"{GMAIL}/history?startHistoryId=1001")
    assert [sorted(set(r) - {"id", "messages"}) for r in history["history"]] == [
        ["messagesAdded"],
        ["labelsAdded"],
    ]
    control("history-floor", {"email": "e2e-carol@example.org", "historyId": "1002"})
    assert _get(fake, token, f"{GMAIL}/history?startHistoryId=1001")[0] == 404
    assert control("delete-message", {"email": "e2e-carol@example.org", "id": "c1"}) == {
        "deleted": True
    }
    control("fail", {"endpoint": "getProfile", "kind": "503", "times": 2})
    assert [_get(fake, token, f"{GMAIL}/profile")[0] for _ in range(3)] == [503, 503, 200]
    control("expire-access", {})
    assert _get(fake, token, f"{GMAIL}/profile")[0] == 401
    control("clock", {"now": "2027-01-01T00:00:00Z"})
    assert fake.now_ms == fake_gmail.parse_time("2027-01-01T00:00:00Z")
    calls = json.loads(fake.handle("GET", "/__control/calls?endpoint=getProfile", {}).body)["calls"]  # type: ignore[arg-type]
    assert [call["status"] for call in calls] == [503, 503, 200, 401]
    assert (
        fake.handle("POST", "/__control/fail", {}, b'{"endpoint": "x", "kind": "teapot"}').status
        == 400
    )
    assert fake.handle("POST", "/__control/nothing", {}, b"{}").status == 400
    control("reset", {})
    assert fake.mailboxes == {}
    assert fake.calls == []


def test_the_call_log_never_holds_a_token(fake: FakeGoogle) -> None:
    refresh = fake.grant(ALICE)
    form = _refresh_form(fake, refresh)
    access = json.loads(fake.handle("POST", "/token", {}, form.encode()).body)["access_token"]  # type: ignore[arg-type]
    _get(fake, access, f"{GMAIL}/profile")
    text = json.dumps(fake.calls)
    assert refresh not in text
    assert access not in text
    assert fake.client_secret not in text
    assert fake.calls[0]["query"] == {"grant_type": "refresh_token"}


# --- the same code as a service -------------------------------------------------------------


def test_run_as_a_service_it_answers_on_a_loopback_port(tmp_path: Path) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = Path(fake_gmail.__file__)
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(script),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--seed",
            str(fake_gmail.FIXTURES),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"

    def call(
        method: str, path: str, body: bytes | None = None, token: str | None = None
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(base + path, data=body, method=method)  # noqa: S310, loopback
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=20) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    try:
        assert process.stdout is not None
        # The line is printed once the port is bound: reading it is the wait.
        assert "3 mailboxes" in process.stdout.readline()
        assert call("GET", "/healthz") == (200, b'{"ok": true}')
        status, body = call("POST", "/__control/grant", json.dumps({"email": ALICE}).encode())
        refresh = json.loads(body)["refresh_token"]
        form = _refresh_form(FakeGoogle(), refresh)
        status, body = call("POST", "/token", form.encode())
        token = json.loads(body)["access_token"]
        status, body = call("GET", f"{GMAIL}/profile", token=token)
        assert (status, json.loads(body)["emailAddress"]) == (200, ALICE)
        assert call("GET", f"{GMAIL}/profile", token="wrong")[0] == 401
        # A streamed answer arrives whole over a real socket.
        attachment = (
            FakeGoogle().seed("alice").messages["m-pdf-1"]["attachments"][0]["attachmentId"]
        )
        status, body = call(
            "GET", f"{GMAIL}/messages/m-pdf-1/attachments/{attachment}", token=token
        )
        assert status == 200
        assert base64.urlsafe_b64decode(json.loads(body)["data"] + "==").startswith(b"%PDF-1.4")
        # A reset keeps what --seed loaded and forgets the grants.
        assert call("POST", "/__control/reset", b"{}")[0] == 200
        assert call("GET", f"{GMAIL}/profile", token=token)[0] == 401
        assert json.loads(call("POST", "/__control/grant", json.dumps({"email": BOB}).encode())[1])[
            "refresh_token"
        ]
    finally:
        process.terminate()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
