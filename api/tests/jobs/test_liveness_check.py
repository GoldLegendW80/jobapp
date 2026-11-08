"""The check of a posting's own address (app/jobs/liveness_check.py, SPEC-jobs "Liveness"):
where the request may go and how it is sent (a fake resolver and transport: no request leaves the
test), what is stored and when a posting moves to closed (real Postgres), and the clicks it
refuses (the app's routes). Every site and address here is invented."""

import asyncio
import gzip
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import Application
from app.browser.leases import StoreUnavailableError
from app.browser.sources import PACE_POLL_S, MemoryPacer
from app.jobs import liveness_check, service
from app.jobs.liveness import CLOSED, MAY_BE_CLOSED, NO_CLOSURE, UNCONFIRMED, Finding
from app.jobs.liveness_check import Checker, address_allowed
from app.jobs.models import Job, JobEvent, JobStatus
from app.opportunities import deck as decks
from app.opportunities import router as deck_router
from tests.identity.api_helpers import Api, Clock
from tests.jobs.test_api import Signed, seed, sign_in

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import proxy  # noqa: E402

PUBLIC = "203.0.114.10"
HOST = "jobs.example-robotics.example"
POSTING = f"https://{HOST}/careers/4012345-robotics-engineer"
COPY = "Example Robotics builds warehouse robots and hires engineers who like clear code. " * 6
CLOSED_PAGE = (
    f"<html><body><p>{COPY}</p><h2>Cette offre n\u2019est plus disponible.</h2></body></html>"
)
OPEN_PAGE = f"<html><body><h1>Robotics engineer</h1><p>{COPY}</p><a>Apply</a></body></html>"
HINT_PAGE = f"<html><body><p>{COPY}</p><span>Job expired</span></body></html>"


@dataclass(frozen=True)
class Page:
    """An answer of the fake web, sent as a stream as a real server's is."""

    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def html(text: str, status: int = 200, **headers: str) -> Page:
    return Page(status, text.encode(), {"content-type": "text/html; charset=utf-8", **headers})


def redirect(location: str, status: int = 302) -> Page:
    return Page(status, b"", {"location": location})


class Site:
    """The fake web: answers by the Host header and path the request carries."""

    def __init__(self, routes: dict[str, Page | Callable[[], httpx.Response]]) -> None:
        self.routes = routes
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        key = request.headers["host"] + request.url.raw_path.decode()
        found = self.routes.get(key, Page(404, b"nothing here"))
        if callable(found):
            return found()
        return httpx.Response(
            found.status, headers=found.headers, stream=httpx.ByteStream(found.body)
        )


def resolver(addresses: dict[str, list[str]] | None = None) -> liveness_check.Resolve:
    async def resolve(host: str, port: int) -> list[str]:
        return (addresses or {}).get(host, [host if host[0].isdigit() else PUBLIC])

    return resolve


async def instant(seconds: float) -> None:
    await asyncio.sleep(0)


def checker(site: Site, addresses: dict[str, list[str]] | None = None, **kwargs: Any) -> Checker:
    return Checker(
        kwargs.pop("pacer", MemoryPacer()),
        transport=httpx.MockTransport(site),
        resolve=resolver(addresses),
        sleep=kwargs.pop("sleep", instant),
        **kwargs,
    )


def check(site: Site, url: str = POSTING, **kwargs: Any) -> Finding:
    return anyio.run(checker(site, **kwargs).check, url)


PATH = "/careers/4012345-robotics-engineer"


# --- where a request may go ---------------------------------------------------------------------


ADDRESSES = [
    ("8.8.8.8", True),
    ("203.0.114.10", True),
    ("2a00:1450:4007:80e::200e", True),
    ("::ffff:8.8.8.8", True),
    ("127.0.0.1", False),
    ("10.0.0.7", False),
    ("172.18.0.3", False),
    ("192.168.1.20", False),
    ("169.254.169.254", False),
    ("100.64.0.1", False),
    ("0.0.0.0", False),  # noqa: S104
    ("224.0.0.1", False),
    ("255.255.255.255", False),
    ("192.0.2.1", False),
    ("198.51.100.4", False),
    ("203.0.113.9", False),
    ("::1", False),
    ("::", False),
    ("fe80::1", False),
    ("fe80::1%eth0", False),
    ("fd00::1", False),
    ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("64:ff9b::a00:1", False),
    ("2002:a00:1::1", False),
    ("2001:db8::1", False),
    ("2001::1", False),
    ("ff02::1", False),
    ("not an address", False),
    ("", False),
]


@pytest.mark.parametrize(("address", "allowed"), ADDRESSES)
def test_only_public_unicast_addresses_are_reached(address: str, allowed: bool) -> None:
    assert address_allowed(address) is allowed


@pytest.mark.parametrize(("address", "allowed"), ADDRESSES)
def test_the_rule_is_browserds_egress_rule(address: str, allowed: bool) -> None:
    assert proxy.address_allowed(address) is allowed
    assert liveness_check.REFUSED_V4 == proxy.REFUSED_V4
    assert liveness_check.REFUSED_V6 == proxy.REFUSED_V6
    assert liveness_check.GLOBAL_V6 == proxy.GLOBAL_V6


def test_the_request_goes_to_the_address_checked_under_the_hosts_name() -> None:
    site = Site({HOST + PATH: html(OPEN_PAGE)})
    assert check(site) == Finding(NO_CLOSURE, "none")
    (sent,) = site.seen
    assert sent.url.host == PUBLIC
    assert sent.headers["host"] == HOST
    assert sent.extensions["sni_hostname"] == HOST
    assert "cookie" not in sent.headers
    assert "jobapp" in sent.headers["user-agent"]


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.7"], ["127.0.0.1"], ["169.254.169.254"], [PUBLIC, "172.18.0.3"], ["::1"], []],
)
def test_a_host_with_any_private_address_is_never_asked(addresses: list[str]) -> None:
    site = Site({HOST + PATH: html(CLOSED_PAGE)})
    assert check(site, addresses={HOST: addresses}) == Finding(UNCONFIRMED, "address_refused")
    assert site.seen == []


@pytest.mark.parametrize(
    "url",
    [
        f"https://{HOST}:8000{PATH}",
        f"ftp://{HOST}{PATH}",
        "https://[2a00:1450:4007:80e::200e]/careers/1",
        f"https://user:secret@{HOST}{PATH}",
        f"https://{HOST}/careers/emploi d\u00e9veloppeur",
        "javascript:alert(1)",
        "https://http://",
    ],
)
def test_an_address_the_rule_does_not_read_is_never_asked(url: str) -> None:
    site = Site({})
    assert check(site, url) == Finding(UNCONFIRMED, "address_refused")
    assert site.seen == []


@pytest.mark.parametrize(
    "location",
    ["http://127.0.0.1:8000/admin", "http://10.0.0.7/", "https://internal.example/", "/careers"],
)
def test_every_redirect_is_checked_again(location: str) -> None:
    site = Site(
        {
            HOST + PATH: redirect(location),
            HOST + "/careers": html(OPEN_PAGE),
        }
    )
    found = check(site, addresses={"internal.example": ["10.1.2.3"]})
    if location == "/careers":
        assert found == Finding(MAY_BE_CLOSED, "redirected")
        assert len(site.seen) == 2
    else:
        assert found == Finding(UNCONFIRMED, "address_refused")
        assert len(site.seen) == 1


def test_a_redirect_that_keeps_the_postings_id_is_its_own_page() -> None:
    site = Site(
        {
            HOST + PATH: redirect(f"/en{PATH}", 301),
            HOST + f"/en{PATH}": html(CLOSED_PAGE),
        }
    )
    found = check(site)
    assert found.verdict == CLOSED
    assert found.sentence == "Cette offre n\u2019est plus disponible."


def test_redirects_are_followed_a_few_times_only() -> None:
    loop = {HOST + f"/r{n}": redirect(f"/r{n + 1}") for n in range(9)}
    site = Site({HOST + PATH: redirect("/r0"), **loop})
    assert check(site) == Finding(UNCONFIRMED, "too_many_redirects")
    assert len(site.seen) == liveness_check.REDIRECTS_MAX + 1


def test_a_linkedin_posting_is_never_asked() -> None:
    site = Site({})
    found = check(site, "https://fr.linkedin.com/jobs/view/4012345678/")
    assert found == Finding(UNCONFIRMED, "linkedin")
    assert site.seen == []


def test_a_posting_without_an_address_is_unconfirmed() -> None:
    assert anyio.run(checker(Site({})).check, None) == Finding(UNCONFIRMED, "no_address")


def test_a_network_failure_is_unconfirmed() -> None:
    def refuse() -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert check(Site({HOST + PATH: refuse})) == Finding(UNCONFIRMED, "network")


def test_a_resolver_that_fails_is_unconfirmed() -> None:
    async def fails(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    found = anyio.run(Checker(MemoryPacer(), resolve=fails).check, POSTING)
    assert found == Finding(UNCONFIRMED, "network")


@pytest.mark.parametrize(
    ("content_type", "reason"),
    [("application/pdf", "not_a_page"), ("application/json", "not_a_page"), ("", "not_a_page")],
)
def test_an_answer_that_is_not_a_page_is_unconfirmed(content_type: str, reason: str) -> None:
    answer = Page(200, b"%PDF-1.7", {"content-type": content_type})
    assert check(Site({HOST + PATH: answer})) == Finding(UNCONFIRMED, reason)


def test_a_plain_text_page_is_read() -> None:
    text = f"{COPY}\nThis job is no longer available."
    answer = Page(200, text.encode(), {"content-type": "text/plain"})
    assert check(Site({HOST + PATH: answer})).verdict == CLOSED


def test_a_compressed_page_is_read_within_the_bound() -> None:
    body = gzip.compress(CLOSED_PAGE.encode())
    answer = Page(200, body, {"content-type": "text/html", "content-encoding": "gzip"})
    assert check(Site({HOST + PATH: answer})).verdict == CLOSED


def test_an_unknown_coding_is_unreadable() -> None:
    answer = Page(200, b"\x1b", {"content-type": "text/html", "content-encoding": "br"})
    assert check(Site({HOST + PATH: answer})) == Finding(UNCONFIRMED, "unreadable")


def test_a_huge_page_is_read_up_to_its_bound() -> None:
    bomb = gzip.compress(b"<p>" + b"a" * (40 * 1024 * 1024) + b"</p>")
    answer = Page(200, bomb, {"content-type": "text/html", "content-encoding": "gzip"})
    assert check(Site({HOST + PATH: answer})).verdict == NO_CLOSURE
    late = html(OPEN_PAGE + "x" * liveness_check.BODY_MAX + "This job is no longer available.")
    assert check(Site({HOST + PATH: late})).verdict == NO_CLOSURE


def test_a_page_in_another_charset_is_read() -> None:
    body = f"<p>{COPY}</p><p>Cette offre a expir\u00e9.</p>".encode("latin-1")
    answer = Page(200, body, {"content-type": "text/html; charset=ISO-8859-1"})
    assert check(Site({HOST + PATH: answer})).verdict == CLOSED


# Codecs that raise on a page ("undefined", "idna"), are not text ("base64", "rot13") or do not
# exist: the page is read as UTF-8, never a raise out of the check.
UNUSABLE_CHARSETS = ["undefined", "idna", "base64", "rot13", "hex", "no-such-charset"]


@pytest.mark.parametrize("charset", UNUSABLE_CHARSETS)
def test_a_charset_the_check_cannot_use_reads_the_page_as_utf8(charset: str) -> None:
    for text, verdict in ((OPEN_PAGE, NO_CLOSURE), (CLOSED_PAGE, CLOSED)):
        answer = Page(200, text.encode(), {"content-type": f"text/html; charset={charset}"})
        assert check(Site({HOST + PATH: answer})).verdict == verdict, charset


def test_a_page_the_reader_fails_on_is_unconfirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fails(*args: Any) -> Finding:
        raise UnicodeError("label too long")

    monkeypatch.setattr(liveness_check.liveness, "judge", fails)
    found = check(Site({HOST + PATH: html(OPEN_PAGE)}))
    assert found == Finding(UNCONFIRMED, "unreadable")


def test_any_other_error_of_the_check_is_unconfirmed() -> None:
    def breaks() -> httpx.Response:
        raise RuntimeError("a bug of the transport")

    assert check(Site({HOST + PATH: breaks})) == Finding(UNCONFIRMED, "check_failed")


class HangingSite:
    """A site that takes the request and never answers."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        await asyncio.Event().wait()
        raise AssertionError("never answered")


def test_a_site_that_never_answers_ends_at_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(liveness_check, "BUDGET_S", 0.05)
    site = HangingSite()
    assert check(site) == Finding(UNCONFIRMED, "timeout")  # type: ignore[arg-type]
    assert len(site.seen) == 1


@pytest.mark.parametrize(
    "location",
    [
        "https://www.linkedin.com/jobs/view/4012345678/",
        "https://fr.linkedin.com/jobs/view/4012345678/",
        "https://LINKEDIN.COM/jobs/view/4012345678",
    ],
)
def test_a_redirect_to_linkedin_is_never_followed(location: str) -> None:
    site = Site({HOST + PATH: redirect(location)})
    assert check(site) == Finding(UNCONFIRMED, "linkedin")
    assert len(site.seen) == 1


def test_each_request_of_a_redirect_takes_its_own_hosts_turn() -> None:
    other = "careers.example-two.example"
    pacer = Pacer([0.0, 0.0])
    site = Site(
        {
            HOST + PATH: redirect(f"https://{other}/jobs/4012345"),
            other + "/jobs/4012345": html(OPEN_PAGE),
        }
    )
    assert check(site, pacer=pacer).verdict == MAY_BE_CLOSED  # another host: redirected
    assert [call[:2] for call in pacer.calls] == [
        ("reserve", HOST),
        ("release", HOST),
        ("reserve", other),
        ("release", other),
    ]


SECOND = "203.0.114.11"


def test_the_next_address_is_tried_when_the_first_cannot_be_reached() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=httpx.ByteStream(OPEN_PAGE.encode())
        )

    seen: list[str] = []

    def recorded(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return answer(request)

    made = Checker(
        MemoryPacer(),
        transport=httpx.MockTransport(recorded),
        resolve=resolver({HOST: [PUBLIC, SECOND, PUBLIC]}),
        sleep=instant,
    )
    assert anyio.run(made.check, POSTING) == Finding(NO_CLOSURE, "none")
    assert seen == [PUBLIC, SECOND]


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"), httpx.ReadError("x")]
)
def test_no_address_reached_is_the_network(error: Exception) -> None:
    seen: list[str] = []

    def fails(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        raise error

    made = Checker(
        MemoryPacer(),
        transport=httpx.MockTransport(fails),
        resolve=resolver({HOST: [PUBLIC, SECOND]}),
        sleep=instant,
    )
    assert anyio.run(made.check, POSTING) == Finding(UNCONFIRMED, "network")
    # Only an address that could not be reached gives its turn to the next one.
    assert seen == ([PUBLIC] if isinstance(error, httpx.ReadError) else [PUBLIC, SECOND])


# --- the pace -----------------------------------------------------------------------------------


class Pacer:
    def __init__(self, answers: list[float | Exception | None]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, str]] = []

    async def reserve(self, host: str, pause: float, holder: str) -> float | None:
        self.calls.append(("reserve", host, holder))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def release(self, host: str, holder: str) -> None:
        self.calls.append(("release", host, holder))


def test_a_request_waits_for_its_hosts_turn_and_gives_it_back() -> None:
    pacer = Pacer([None, None, 0.5])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    site = Site({HOST + PATH: html(OPEN_PAGE)})
    assert check(site, pacer=pacer, sleep=sleep).verdict == NO_CLOSURE
    assert slept == [PACE_POLL_S, PACE_POLL_S, 0.5]
    holders = {holder for _, _, holder in pacer.calls}
    assert len(holders) == 1
    assert [call[:2] for call in pacer.calls] == [("reserve", HOST)] * 3 + [("release", HOST)]


def test_the_pacers_store_down_is_unconfirmed_and_nothing_is_sent() -> None:
    site = Site({HOST + PATH: html(OPEN_PAGE)})
    assert check(site, pacer=Pacer([StoreUnavailableError()])) == Finding(UNCONFIRMED, "busy")
    assert site.seen == []


def test_a_failed_request_gives_the_host_back() -> None:
    def refuse() -> httpx.Response:
        raise httpx.ReadError("reset")

    pacer = Pacer([0.0])
    check(Site({HOST + PATH: refuse}), pacer=pacer)
    assert [call[0] for call in pacer.calls] == ["reserve", "release"]


# --- what is stored ----------------------------------------------------------------------------


def posting(factory: sessionmaker[Session], user: uuid.UUID, **values: Any) -> uuid.UUID:
    (job,) = seed(factory, user, [{"url": POSTING, **values}])
    return job.id


def ensure(
    factory: sessionmaker[Session], site: Site, user: uuid.UUID, job_id: uuid.UUID, clock: Clock,
    **kwargs: Any,
) -> liveness_check.Outcome | None:  # fmt: skip
    async def run() -> liveness_check.Outcome | None:
        return await liveness_check.ensure(checker(site), factory, user, job_id, clock(), **kwargs)

    return anyio.run(run)


def row(factory: sessionmaker[Session], job_id: uuid.UUID) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


def status_events(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as db:
        return [
            e.data
            for e in db.scalars(
                sa.select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
            if e.kind.value == "status"
        ]


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-liveness-a@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-liveness-b@example.org")


def test_a_closed_page_moves_an_open_posting_to_closed(
    factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id)
    outcome = ensure(factory, Site({HOST + PATH: html(CLOSED_PAGE)}), alice.id, job_id, clock)
    assert outcome is not None
    assert outcome.status is JobStatus.closed
    stored = row(factory, job_id)
    assert stored.status is JobStatus.closed
    assert stored.liveness == {
        "verdict": CLOSED,
        "reason": "page_says",
        "sentence": "Cette offre n\u2019est plus disponible.",
        "checked_at": clock().isoformat(),
        "moved": True,
    }
    assert status_events(factory, job_id) == [
        {"from": "found", "to": "closed", "via": "liveness", "check": "page_says"}
    ]


@pytest.mark.parametrize("page", [HINT_PAGE, OPEN_PAGE])
def test_anything_else_is_stored_and_moves_nothing(
    factory: sessionmaker[Session], alice: Signed, clock: Clock, page: str
) -> None:
    job_id = posting(factory, alice.id)
    before = row(factory, job_id)
    ensure(factory, Site({HOST + PATH: html(page)}), alice.id, job_id, clock)
    after = row(factory, job_id)
    assert after.status is JobStatus.found
    assert after.updated_at == before.updated_at  # a check is not an edit
    assert after.liveness is not None
    assert after.liveness["verdict"] == (MAY_BE_CLOSED if page == HINT_PAGE else NO_CLOSURE)
    assert after.liveness["moved"] is False
    assert status_events(factory, job_id) == []


@pytest.mark.parametrize(
    "status", [JobStatus.applied, JobStatus.interview, JobStatus.rejected, JobStatus.skipped]
)
def test_a_posting_past_applying_or_passed_keeps_its_status(
    factory: sessionmaker[Session], alice: Signed, clock: Clock, status: JobStatus
) -> None:
    job_id = posting(factory, alice.id, status=status)
    ensure(factory, Site({HOST + PATH: html(CLOSED_PAGE)}), alice.id, job_id, clock)
    stored = row(factory, job_id)
    assert stored.status is status
    assert stored.liveness is not None
    assert stored.liveness["verdict"] == CLOSED
    assert stored.liveness["moved"] is False


def test_a_posting_the_person_reopened_is_never_closed_again_by_the_app(
    factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id)
    site = Site({HOST + PATH: html(CLOSED_PAGE)})
    ensure(factory, site, alice.id, job_id, clock)
    with factory() as db:
        job = service.lock_job(db, alice.id, job_id)
        assert job is not None
        service.set_status(db, job, JobStatus.shortlisted, clock())
        db.commit()
    clock.advance(hours=2)
    outcome = ensure(factory, site, alice.id, job_id, clock)
    assert outcome is not None
    assert outcome.status is JobStatus.shortlisted
    assert row(factory, job_id).liveness["verdict"] == CLOSED  # shown as a proposal
    assert row(factory, job_id).liveness["moved"] is False


def test_a_recent_check_is_reused_and_an_old_one_asked_again(
    factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id)
    site = Site({HOST + PATH: html(OPEN_PAGE)})
    ensure(factory, site, alice.id, job_id, clock)
    clock.advance(minutes=59)
    ensure(factory, site, alice.id, job_id, clock)
    assert len(site.seen) == 1
    ensure(factory, site, alice.id, job_id, clock, force=True)
    assert len(site.seen) == 2
    clock.advance(hours=1, seconds=1)
    ensure(factory, site, alice.id, job_id, clock)
    assert len(site.seen) == 3


def test_a_closed_posting_is_not_asked_unless_the_person_asks(
    factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id, status=JobStatus.closed)
    site = Site({HOST + PATH: html(OPEN_PAGE)})
    outcome = ensure(factory, site, alice.id, job_id, clock)
    assert outcome == liveness_check.Outcome(JobStatus.closed, None)
    assert site.seen == []
    ensure(factory, site, alice.id, job_id, clock, force=True)
    assert row(factory, job_id).status is JobStatus.closed  # never reopened by a check


def test_another_persons_posting_is_neither_asked_nor_stored(
    factory: sessionmaker[Session], alice: Signed, bob: Signed, clock: Clock
) -> None:
    job_id = posting(factory, bob.id)
    site = Site({HOST + PATH: html(CLOSED_PAGE)})
    assert ensure(factory, site, alice.id, job_id, clock, force=True) is None
    assert site.seen == []
    assert row(factory, job_id).liveness is None


@pytest.mark.parametrize(
    ("stored", "sentence"),
    [
        (
            {"verdict": CLOSED, "reason": "page_says", "sentence": "Poste pourvu."},
            "This posting is closed. Its page says: \u201cPoste pourvu.\u201d",
        ),
        (
            {"verdict": CLOSED, "reason": "http_410"},
            "This posting is closed: its page no longer exists.",
        ),
        (
            {"verdict": MAY_BE_CLOSED, "reason": "page_hints", "sentence": "Job expired"},
            "This posting is closed.",
        ),
        (None, "This posting is closed."),
    ],
)
def test_the_sentence_of_a_refused_click(stored: dict[str, Any] | None, sentence: str) -> None:
    assert liveness_check.closed_sentence(stored) == sentence


# --- the routes ------------------------------------------------------------------------------


@pytest.fixture
def site(app: FastAPI) -> Site:
    found = Site({HOST + PATH: html(CLOSED_PAGE)})
    app.state.liveness = checker(found)
    return found


QUOTED = (
    "This posting is closed. Its page says: \u201cCette offre n\u2019est plus disponible.\u201d"
)


def test_check_now_closes_a_posting_whose_page_says_so(
    api: Api, factory: sessionmaker[Session], alice: Signed, site: Site
) -> None:
    job_id = posting(factory, alice.id)
    response = api.request("POST", f"/api/jobs/{job_id}/check", token=alice.token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "closed"
    assert body["liveness"]["verdict"] == CLOSED
    assert body["liveness"]["sentence"] == "Cette offre n\u2019est plus disponible."
    again = api.request("POST", f"/api/jobs/{job_id}/check", token=alice.token)
    assert again.status_code == 200
    assert len(site.seen) == 2  # the person's check always asks


def test_check_now_is_the_persons_own(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed, site: Site
) -> None:
    job_id = posting(factory, bob.id)
    assert api.request("POST", f"/api/jobs/{job_id}/check", token=alice.token).status_code == 404
    assert api.request("POST", "/api/jobs/not-a-uuid/check", token=alice.token).status_code == 404
    assert api.request("POST", f"/api/jobs/{job_id}/check").status_code == 401
    assert site.seen == []


def applications(factory: sessionmaker[Session], user: uuid.UUID) -> list[Application]:
    with factory() as db:
        return list(db.scalars(sa.select(Application).where(Application.user_id == user)))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/postings/{id}/like", None),
        ("/api/postings/{id}/applications", {"mode": "apply"}),
        ("/api/postings/{id}/applications", {"mode": "manual"}),
    ],
)
def test_a_like_or_an_apply_on_a_closed_page_is_refused_with_its_words(
    api: Api, factory: sessionmaker[Session], alice: Signed, site: Site, path: str, body: Any
) -> None:
    job_id = posting(factory, alice.id)
    response = api.request("POST", path.format(id=job_id), token=alice.token, json=body)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "posting_closed"
    assert response.json()["detail"] == QUOTED
    assert applications(factory, alice.id) == []
    assert row(factory, job_id).status is JobStatus.closed


def test_write_on_a_closed_page_is_refused_with_its_words(
    api: Api, factory: sessionmaker[Session], alice: Signed, site: Site
) -> None:
    job_id = posting(factory, alice.id)
    response = api.request(
        "POST", f"/api/jobs/{job_id}/documents/write", token=alice.token, json={"kinds": ["cv"]}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == QUOTED
    with factory() as db:
        written = db.execute(
            sa.text("SELECT count(*) FROM documents WHERE posting_id = :p"), {"p": job_id}
        ).scalar_one()
    assert written == 0


def test_a_click_on_a_posting_the_person_closed_is_refused_without_a_request(
    api: Api, factory: sessionmaker[Session], alice: Signed, site: Site
) -> None:
    job_id = posting(factory, alice.id, status=JobStatus.closed)
    response = api.request("POST", f"/api/postings/{job_id}/like", token=alice.token)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This posting is closed.",
        "code": "posting_closed",
        "application_id": None,
    }
    assert site.seen == []


def test_a_click_goes_on_when_the_check_cannot_tell(
    api: Api, app: FastAPI, factory: sessionmaker[Session], alice: Signed
) -> None:
    refused = Site({HOST + PATH: html("Access denied", status=403)})
    app.state.liveness = checker(refused)
    job_id = posting(factory, alice.id)
    response = api.request("POST", f"/api/postings/{job_id}/like", token=alice.token)
    assert response.json().get("code") != "posting_closed", response.text
    assert row(factory, job_id).liveness["verdict"] == UNCONFIRMED
    assert len(refused.seen) == 1


CLICKS = [
    ("/api/postings/{id}/like", None),
    ("/api/postings/{id}/applications", {"mode": "apply"}),
    ("/api/jobs/{id}/documents/write", {"kinds": ["cv"]}),
]


def goes_on(api: Api, token: str, path: str, body: Any, job_id: uuid.UUID) -> None:
    response = api.request("POST", path.format(id=job_id), token=token, json=body)
    assert response.status_code < 500, response.text
    assert response.json().get("code") != "posting_closed", response.text


@pytest.mark.parametrize(("path", "body"), CLICKS)
@pytest.mark.parametrize("charset", ["undefined", "idna"])
def test_a_click_goes_on_on_a_page_whose_charset_the_check_cannot_use(
    api: Api, app: FastAPI, factory: sessionmaker[Session], alice: Signed, path: str, body: Any,
    charset: str,
) -> None:  # fmt: skip
    page = Page(200, OPEN_PAGE.encode(), {"content-type": f"text/html; charset={charset}"})
    found = Site({HOST + PATH: page})
    app.state.liveness = checker(found)
    job_id = posting(factory, alice.id)
    goes_on(api, alice.token, path, body, job_id)
    assert row(factory, job_id).liveness["verdict"] == NO_CLOSURE
    assert len(found.seen) == 1


@pytest.mark.parametrize(("path", "body"), CLICKS)
def test_a_click_goes_on_when_the_page_cannot_be_read(
    api: Api, app: FastAPI, factory: sessionmaker[Session], alice: Signed, path: str, body: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    def fails(*args: Any) -> Finding:
        raise UnicodeError("label too long")

    monkeypatch.setattr(liveness_check.liveness, "judge", fails)
    app.state.liveness = checker(Site({HOST + PATH: html(CLOSED_PAGE)}))
    job_id = posting(factory, alice.id)
    goes_on(api, alice.token, path, body, job_id)
    assert row(factory, job_id).liveness["reason"] == "unreadable"
    assert row(factory, job_id).status is JobStatus.found


@pytest.mark.parametrize(("path", "body"), CLICKS)
def test_a_click_goes_on_whatever_stops_the_check(
    api: Api, factory: sessionmaker[Session], alice: Signed, site: Site, path: str, body: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def breaks(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("a bug of the check")

    monkeypatch.setattr(liveness_check, "ensure", breaks)
    job_id = posting(factory, alice.id)
    goes_on(api, alice.token, path, body, job_id)
    assert site.seen == []


def test_a_click_goes_on_when_the_site_never_answers(
    api: Api, app: FastAPI, factory: sessionmaker[Session], alice: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(liveness_check, "BUDGET_S", 0.05)
    hanging = HangingSite()
    app.state.liveness = checker(hanging)  # type: ignore[arg-type]
    job_id = posting(factory, alice.id)
    goes_on(api, alice.token, "/api/postings/{id}/like", None, job_id)
    assert row(factory, job_id).liveness["reason"] == "timeout"
    assert len(hanging.seen) == 1


def test_apply_to_the_selected_checks_each_posting_first(
    app: FastAPI, factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    first, second = (
        posting(factory, alice.id),
        posting(factory, alice.id, slug="job-9", url=f"https://{HOST}/careers/4099999"),
    )
    found = Site({HOST + PATH: html(CLOSED_PAGE), HOST + "/careers/4099999": html(OPEN_PAGE)})
    app.state.liveness = checker(found)

    class Request:
        pass

    request = Request()
    request.app = app  # type: ignore[attr-defined]

    async def run() -> None:
        await anyio.to_thread.run_sync(
            liveness_check.check_all, request, alice.id, [first, second, first]
        )

    anyio.run(run)
    assert row(factory, first).status is JobStatus.closed
    assert row(factory, second).liveness["verdict"] == NO_CLOSURE
    assert len(found.seen) == 2


def test_the_deck_and_the_list_show_what_the_check_found(
    api: Api, factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id)
    ensure(factory, Site({HOST + PATH: html(HINT_PAGE)}), alice.id, job_id, clock)
    detail = api.request("GET", f"/api/jobs/{job_id}", token=alice.token).json()
    assert detail["liveness"]["verdict"] == MAY_BE_CLOSED
    assert detail["liveness"]["sentence"] == "Job expired"
    listed = api.request("GET", "/api/jobs", token=alice.token).json()["items"]
    assert listed[0]["liveness"]["verdict"] == MAY_BE_CLOSED
    card = deck_router._card(decks.Card(row(factory, job_id), decks.Reason((), None, None)))
    assert card.liveness is not None
    assert card.liveness.verdict == MAY_BE_CLOSED
    assert card.liveness.sentence == "Job expired"


def test_only_an_object_is_stored_and_a_bad_date_is_never_fresh(
    factory: sessionmaker[Session], alice: Signed, clock: Clock
) -> None:
    job_id = posting(factory, alice.id)
    with factory() as db, pytest.raises(sa.exc.IntegrityError):
        db.execute(sa.update(Job).where(Job.id == job_id).values(liveness=[1]))
    assert liveness_check.fresh({"checked_at": "garbage"}, clock()) is False
    assert liveness_check.fresh(None, clock()) is False
    assert liveness_check.fresh(
        {"checked_at": (clock() - timedelta(minutes=5)).isoformat()}, clock()
    )


def test_apply_to_the_selected_returns_at_its_budget_and_one_failure_fails_alone(
    app: FastAPI, factory: sessionmaker[Session], alice: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(liveness_check, "BULK_BUDGET_S", 0.2)
    hangs, raises, answers = (
        posting(factory, alice.id, slug=f"job-{n}", url=f"https://{HOST}/careers/40{n}0000")
        for n in (1, 2, 3)
    )
    asked: list[str] = []

    class Mixed:
        """One posting's site never answers, one check raises, the third answers."""

        async def check(self, url: str | None) -> Finding:
            asked.append(str(url))
            if url == f"https://{HOST}/careers/4010000":
                await asyncio.Event().wait()
            if url == f"https://{HOST}/careers/4020000":
                raise RuntimeError("a bug of the check")
            return Finding(NO_CLOSURE, "none")

    app.state.liveness = Mixed()

    class Request:
        pass

    request = Request()
    request.app = app  # type: ignore[attr-defined]

    async def run() -> None:
        await anyio.to_thread.run_sync(
            liveness_check.check_all, request, alice.id, [hangs, raises, answers]
        )

    anyio.run(run)
    assert len(asked) == 3
    assert row(factory, hangs).liveness is None
    assert row(factory, raises).liveness is None
    assert row(factory, answers).liveness["verdict"] == NO_CLOSURE
