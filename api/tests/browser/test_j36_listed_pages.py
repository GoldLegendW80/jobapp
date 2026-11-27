"""Jobs J36 (owner, 2026-11-27: "Oui", LinkedIn signed in included): after a website's agent,
the app reads by itself the pages of the postings kept from a card, one at a time in a tab of
its own (B27's open), and stores what each page answers. The store is a fake here (the jobs
tests run the real one): browserd's tabs are test_b27's `TabSite`, the permission a policy of
the website's GuardedTools. Invented hosts only, never a real job website."""

import asyncio
import math
import random
import re
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder, sources
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.sources import interactive, listed_pages
from app.jobs import sweep
from tests.browser.test_b20_agent import HOME, START
from tests.browser.test_b27_code_first import TabSite

HOST = "jobs.example.test"


def job(n: int) -> str:
    return f"{START}jobs/{n}"


# A posting's text as long as a posting's is (service.TEXT_MIN_CHARS, 300), invented.
BODY = (
    "- paragraph: Build invented robot arms with Python for Example Robotics, Paris. You design "
    "the welding cells of an invented factory, write the control software that drives them, test "
    "it on the bench with the team, and ship it to the line. You know Python, a robot framework "
    "and the safety rules of a workshop. Hybrid, two days a week on site, full time."
)


def posting(title: str) -> str:
    return f'- heading "{title}" [level=1]\n{BODY}'


SIGNIN = '- heading "Sign in" [level=1]\n- textbox "Email" [ref=e1]'
CHECK = '- heading "Just a moment" [level=1]\n- paragraph: Checking your browser'
CLOSED = '- heading "Robot Welder" [level=1]\n- paragraph: This job is no longer available.'
HINTED = f'- heading "Robot Welder" [level=1]\n- paragraph: Applications closed\n{BODY}'
ELSEWHERE = '- heading "Robot jobs" [level=1]\n- paragraph: 12 jobs found'
SHORT = '- heading "Robot Welder" [level=1]\n- paragraph: Build invented robot arms in Paris.'
# A wall whose own heading holds the posting's title (review of PR 864, item 4).
TITLED_SIGNIN = f'- heading "Robot Welder" [level=1]\n{SIGNIN.replace("level=1", "level=2")}'
TITLED_CHECK = f'- heading "Robot Welder" [level=2]\n{CHECK}\n{BODY}'
GUEST = f'- heading "Robot Welder" [level=1]\n- heading "Join LinkedIn" [level=2]\n{BODY}'
TITLES = ("Robot Welder", "Robot Painter", "Robot Arm Engineer", "Robot Tester", "Robot Pilot",
          "Robot Fitter")  # fmt: skip
# The website's search page listing the posting's title among six cards (review, item 3).
LIST_PAGE = "\n".join(
    ['- heading "Robot jobs near Paris" [level=1]', "- list:"]
    + [
        f'  - listitem:\n    - heading "{t}" [level=3]\n    - link "{t}" [ref=e{i}]:\n'
        f"      - /url: /jobs/{40 + i}"
        for i, t in enumerate(TITLES)
    ]
)


class NoTools:
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"no browser call expected: {name}")


def page_of(url: str, snapshot: str) -> finder.PageRead:
    pages = sources.Pages(
        NoTools(), 1000, sleep=lambda _: asyncio.sleep(0), rng=random.Random(3),  # noqa: S311  deterministic test pacing
        monotonic=lambda: 0,
    )  # fmt: skip
    scope = finder.InteractiveScope(START, ("robot",), (), ())
    one = finder.FinderSession(pages, START, HOME, "e2e-person", interactive=scope)
    one._read(url, snapshot)
    return one.pages_read[url]


A = job(1)
# The accept and refuse table of what a page answers: (status, landed, snapshot, title,
# platform, verdict). The requested address is A.
VERDICTS = [
    (200, A, posting("Robot Welder"), "Robot Welder", "custom", "read"),
    (None, A, posting("Robot Welder"), "Robot Welder", "custom", "read"),
    (200, A, posting("Senior Robot Welder (Stage)"), "Robot Welder", "custom", "read"),
    (200, A, posting("robot  welder"), "Robot Welder", "custom", "read"),  # case, spacing
    (200, job(1) + "?ref=list", posting("Robot Welder"), "Robot Welder", "custom", "read"),
    # Another page than its own, though it shows the title: never read (review, item 3).
    (
        200,
        START + "careers/robot-welder",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "unread",
    ),
    (200, START + "search?q=robot", LIST_PAGE, "Robot Welder", "custom", "unread"),
    (200, job(2), posting("Robot Welder"), "Robot Welder", "custom", "unread"),
    (
        200,
        "https://other.example.test/jobs/1",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "unread",
    ),
    (200, A, HINTED, "Robot Welder", "custom", "read"),  # a closure naming nothing: read
    # A wall is never read, whatever its headings or its text hold (review, item 4).
    (200, A, TITLED_SIGNIN, "Robot Welder", "custom", "walled"),
    (200, A, TITLED_SIGNIN + "\n" + BODY, "Robot Welder", "custom", "walled"),
    (200, A, TITLED_CHECK, "Robot Welder", "custom", "walled"),
    (200, A, GUEST, "Robot Welder", "linkedin", "walled"),
    (403, A, GUEST, "Robot Welder", "custom", "walled"),
    # Under service.TEXT_MIN_CHARS of text, the title alone is no posting's page.
    (200, A, SHORT, "Robot Welder", "custom", "unread"),
    (200, A, '- heading "Robot Welder" [level=1]', "Robot Welder", "custom", "unread"),
    (401, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (403, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (429, A, posting("Robot Welder"), "Robot Welder", "custom", "walled"),
    (
        200,
        START + "login?next=/jobs/1",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "walled",
    ),
    (
        200,
        START + "checkpoint/challenge",
        posting("Robot Welder"),
        "Robot Welder",
        "custom",
        "walled",
    ),
    (200, A, CHECK, "Robot Welder", "custom", "walled"),
    (200, A, SIGNIN, "Robot Welder", "custom", "walled"),
    (200, "https://www.linkedin.test/authwall?trk=x", SIGNIN, "Robot Welder", "linkedin", "walled"),
    (404, A, "", "Robot Welder", "custom", "gone"),
    (410, A, "", "Robot Welder", "custom", "gone"),
    (200, A, CLOSED, "Robot Welder", "custom", "gone"),  # its page says it is closed
    (404, START + "search?q=robot", "", "Robot Welder", "custom", "unread"),  # not its own page
    (200, START + "search?q=robot", ELSEWHERE, "Robot Welder", "custom", "unread"),
    (500, A, "", "Robot Welder", "custom", "unread"),
    (503, A, "", "Robot Welder", "custom", "unread"),
    (200, A, posting("Robot Arm Engineer"), "Robot Welder", "custom", "unread"),  # another one
    (200, A, posting("Robot"), "Robot Welder", "custom", "unread"),  # a piece of the title
    (200, A, posting("Robot Welders Guild"), "Robot Welder", "custom", "unread"),  # longer word
    (200, A, "- paragraph: Loading", "Robot Welder", "custom", "unread"),
    (200, "", "", "Robot Welder", "custom", "unread"),  # the page did not load
]


@pytest.mark.parametrize(
    ("status", "landed", "snapshot", "title", "platform", "expected"), VERDICTS
)
def test_what_a_page_answers(status, landed, snapshot, title, platform, expected) -> None:
    read = bool(landed) and (status is None or status < 400)
    page = page_of(landed, snapshot) if read else None
    opened = finder.AppOpen(landed, status, read, True, True)
    found, closed = listed_pages.verdict(opened, A, page, snapshot, title, platform)
    assert found == expected
    assert (closed is not None) == (expected == "gone")


def test_a_list_the_session_read_is_never_read_as_the_posting() -> None:
    page = page_of(A, posting("Robot Welder"))
    opened = finder.AppOpen(A, 200, True, True, True)
    snapshot = posting("Robot Welder")
    assert listed_pages.verdict(opened, A, page, snapshot, "Robot Welder", "custom")[0] == "read"
    found, _ = listed_pages.verdict(opened, A, page, snapshot, "Robot Welder", "custom", lists={A})
    assert found == "unread"


LI = "http://www.linkedin.test:8080/jobs/view/1234567890/"
IN = "http://fr.indeed.test:8080/viewjob?jk=0123456789abcdef"
# Whether the page the app landed on is the posting's own: (requested, landed, own).
OWN_PAGES = [
    (A, A, True),
    (A, A + "?ref=list", True),
    (A, A + "/", True),
    (A, job(2), False),
    (A, START + "search?q=robot+welder", False),
    (A, START + "jobs", False),
    (A, "https://other.example.test/jobs/1", False),
    (LI, LI, True),
    (LI, LI + "?trk=public_jobs", True),
    (LI, "http://www.linkedin.test:8080/jobs/view/robot-welder-at-example-1234567890/", True),
    (LI, "http://www.linkedin.test:8080/jobs/search/?currentJobId=1234567890", False),
    (LI, "http://www.linkedin.test:8080/jobs/view/1234567891/", False),
    (LI, "http://www.linkedin.test:8080/feed/", False),
    (IN, IN + "&from=serp", True),
    (IN, "http://fr.indeed.test:8080/viewjob?jk=fedcba9876543210", False),
    (IN, "http://fr.indeed.test:8080/cmp/example/jobs?jk=0123456789abcdef", False),
    (IN, "http://fr.indeed.test:8080/jobs?q=robot&vjk=0123456789abcdef", False),
    ("http://fr.indeed.test:8080/viewjob", "http://fr.indeed.test:8080/viewjob", False),  # no id
]


@pytest.mark.parametrize(("requested", "landed", "own"), OWN_PAGES)
def test_the_posting_own_page(requested: str, landed: str, own: bool) -> None:
    assert listed_pages.own_page(requested, landed) is own


WTTJ = "http://www.welcometothejungle.test:8080/"
INDEED = "http://www.indeed.test:8080/"
# A posting's address on a website's own hosts (finder.lease_hosts): (address, start, on).
ON_HOSTS = [
    (job(1), START, True),
    ("http://jobs.example.test/jobs/1", START, True),
    ("https://JOBS.example.test/jobs/1", START, True),
    ("https://sub.jobs.example.test/jobs/1", START, False),
    ("https://example.test/jobs/1", START, False),
    ("https://www.other-board.test/viewjob?jk=1", START, False),
    ("https://jobs.example.test.evil.test/jobs/1", START, False),
    ("", START, False),
    ("javascript:alert(1)", START, False),
    (IN, INDEED, True),
    ("http://indeed.test:8080/viewjob?jk=0123456789abcdef", INDEED, True),
    ("http://a.b.indeed.test:8080/viewjob", INDEED, False),
    ("http://fra.indeed.test:8080/viewjob", INDEED, False),
    ("https://fr.indeed.test/viewjob", INDEED, False),
    (LI, INDEED, False),
    (WTTJ + "fr/companies/example/jobs/robot-welder", WTTJ, True),
    ("http://welcometothejungle.test:8080/fr/x", WTTJ, True),
    ("http://fr.welcometothejungle.test:8080/fr/x", WTTJ, False),
]


@pytest.mark.parametrize(("url", "start", "on"), ON_HOSTS)
def test_a_posting_on_the_websites_own_hosts(url: str, start: str, on: bool) -> None:
    assert finder.on_lease_hosts(url, start) is on


class Board(TabSite):
    """browserd's tabs over the website's pages; `statuses` the HTTP status a navigate answers,
    `closes` the addresses the memory guard closes the tab on."""

    def __init__(self, pages: dict[str, str], **kw: Any) -> None:
        self.statuses: dict[str, int] = kw.pop("statuses", {})
        self.closes: set[str] = set(kw.pop("closes", ()))
        # The addresses whose navigate loads after the memory guard closed the app's tab during
        # its pause (browserd opens it again and names the close in its answer).
        self.reopens: set[str] = set(kw.pop("reopens", ()))
        # Where a navigate lands instead of the address asked for (a redirect).
        self.redirects: dict[str, str] = kw.pop("redirects", {})
        # browserd's answer to `browser_tabs new` when it makes no tab, or the error it raises.
        self.no_tab: Any = kw.pop("no_tab", None)
        super().__init__({kw.pop("start", START): HOME, **pages}, **kw)

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        if name == "browser_tabs" and args.get("action") == "new" and self.no_tab is not None:
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.calls.append((name, dict(args)))
            if isinstance(self.no_tab, Exception):
                raise self.no_tab
            return self.no_tab
        if name == "browser_navigate" and url in self.closes:
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.calls.append((name, dict(args)))
            close = {"id": "0000beef", "reason": "memory", "used_mib": 1870}
            return {"ok": False, "error": "tab_closed", "tab_closed": close}
        if name == "browser_navigate" and url in self.redirects:
            answer = await super().call(name, {**args, "url": self.redirects[url]})
            self.calls[-1] = (name, {**self.calls[-1][1], "url": url})
        else:
            answer = await super().call(name, args)
        if name == "browser_navigate" and answer.get("ok") and url in self.statuses:
            answer["status"] = self.statuses[url]
        if name == "browser_navigate" and answer.get("ok") and url in self.reopens:
            answer["tab_closed"] = {"id": "0000cafe", "reason": "memory", "used_mib": 1900}
        return answer

    def opened(self) -> list[str]:
        return [a["url"] for n, a in self.calls if n == "browser_navigate"]


class Policy:
    """The website's permission (B14's InteractivePolicy): taken back once `revoked`."""

    def __init__(self) -> None:
        self.revoked = False
        self.checks = 0

    async def check(self, context: Any) -> str | None:
        self.checks += 1
        return "permission_revoked" if self.revoked else None

    async def reserve(self, context: Any) -> str | None:
        return None

    async def observe(self, context: Any, url: str, snapshot: str) -> str | None:
        return None


class Commits:
    """The run's commits as the phase uses them: what was saved, a store that fails at a
    given save, the postings whose failed reading is their last try."""

    def __init__(
        self,
        fail_at: int | None = None,
        last_try: set[uuid.UUID] | None = None,
        left_as_is: set[uuid.UUID] | None = None,
    ):
        self.saved: list[sweep.ListedPage] = []
        self.fail_at, self.last_try = fail_at, last_try or set()
        self.left_as_is = left_as_is or set()  # postings the store leaves as they are
        self.unsettled = False
        self.sessions = None
        self.after: Any = None

    async def save(self, source_id, source, batch, set_aside=(), listed=None) -> None:
        assert (source_id, source, batch, set_aside) == ("src", HOST, None, ())
        if self.fail_at == len(self.saved):
            raise sweep.StoreRefusedError
        done = {"read": "read", "gone": "closed", "unread": "unread"}[listed.outcome]
        listed.done = "" if listed.job_id in self.left_as_is else done
        listed.given_up = listed.done == "unread" and listed.job_id in self.last_try
        self.saved.append(listed)
        if self.after is not None:
            self.after(len(self.saved))


class Leases:
    def __init__(self) -> None:
        self.waiting: list[tuple[str, str]] = []
        self.gone = False

    async def waiters(self) -> list[tuple[str, str]]:
        if self.gone:
            raise StoreUnavailableError
        return self.waiting


def listed(n: int, title: str = "Robot Welder") -> sweep.Listed:
    return sweep.Listed(uuid.UUID(int=n), job(n), title, "Example Robotics", "Paris, France")


def phase(
    monkeypatch,
    board: Board,
    held: list[sweep.Listed],
    *,
    commits: Commits | None = None,
    leases: Leases | None = None,
    policy: Policy | None = None,
    bound_s: float = 600.0,
    stopped: str = "",
    ran: str = "",
    carded: tuple[int, ...] = (),
    step_s: float = 0.0,
    tried: set[uuid.UUID] | None = None,
) -> tuple[dict[str, Any] | None, Commits, dict[str, Any]]:
    """The website's reading of pages after its agent, on `board`, for the postings `held`."""
    clock = [0.0]
    asked: dict[str, Any] = {}

    def postings(sessions, user_id, host, on_site, first):
        asked.update(host=host, first=set(first), on_site=on_site)
        return list(held)

    def left(sessions, user_id, host, on_site):
        asked.update(left_on_site=on_site)
        return 7

    monkeypatch.setattr(sweep, "listed_postings", postings)
    monkeypatch.setattr(sweep, "listed_left", left)

    async def sleep(seconds: float) -> None:
        asked.setdefault("pauses", []).append(seconds)
        clock[0] += seconds

    def monotonic() -> float:
        clock[0] += step_s
        return clock[0]

    context = interactive.SourceContext("e2e-person", "sweep-1", "src", "custom", START)
    tools = interactive.GuardedTools(board, policy or Policy(), context)
    tools.reserved = True
    pages = sources.Pages(
        tools, math.inf, sleep=sleep, rng=random.Random(5), monotonic=monotonic, pause=(0.0, 0.0),  # noqa: S311  deterministic test pacing
    )  # fmt: skip
    scope = finder.InteractiveScope(START, ("robot",), (), ())
    one = finder.FinderSession(pages, START, HOME, "e2e-person", interactive=scope)
    one.stopped = stopped
    site = interactive._Site(
        interactive.Searches(scope, unwanted_titles=re.compile(r"\bstage\b", re.IGNORECASE)),
        source_id="src",
        host=HOST,
    )
    commits = commits or Commits()
    plan = SimpleNamespace(
        commits=commits,
        user_id=uuid.UUID(int=99),
        known=SimpleNamespace(carded={uuid.UUID(int=n) for n in carded}),
        listed_tried=set() if tried is None else tried,
    )
    runner = SimpleNamespace(
        monotonic=monotonic, listed_pages_s=bound_s, leases=leases or Leases(), base_url=None
    )
    asyncio.run(listed_pages.read(one, ran, site=site, runner=runner, plan=plan))
    asked["session"] = one
    return site.listed_pages, commits, asked


def test_pages_that_read_a_wall_and_a_gone_page(monkeypatch) -> None:
    """Acceptance 1 to 3: one page at a time in a tab of its own, 2 to 4 s apart, the agent's
    tab selected again; read, gone and failed stored; a wall ends the reading and its posting
    stays as it is."""
    board = Board(
        {job(1): posting("Robot Welder"), job(2): "", job(3): ELSEWHERE, job(4): SIGNIN},
        statuses={job(2): 404},
    )
    held = [listed(1), listed(2), listed(3), listed(4), listed(5)]
    done, commits, asked = phase(monkeypatch, board, held, carded=(3,))
    assert done == {
        "read": 1,
        "closed": 1,
        "unread": 1,
        "given_up": 0,
        "ended": "walled",
        "left": 7,
    }
    assert [(p.job_id.int, p.outcome) for p in commits.saved] == [
        (1, "read"), (2, "gone"), (3, "unread"),
    ]  # fmt: skip
    assert board.opened() == [job(1), job(2), job(3), job(4)]  # never the one after the wall
    pauses = asked["pauses"]  # B20: no page pause of the website's own (pause_for: none)
    assert len(pauses) == 4
    assert all(2.0 <= p <= 4.0 for p in pauses)
    tabs = [a["action"] for n, a in board.calls if n == "browser_tabs"]
    assert tabs == ["new", "close", "select"] * 4
    assert board.tabs == [""]  # the agent's tab alone, selected
    read = commits.saved[0].finding
    assert (read.title, read.url, read.list_url) == ("Robot Welder", job(1), None)
    assert "invented robot arms" in read.text
    assert commits.saved[1].closed.verdict == "closed"
    assert asked["host"] == HOST
    assert asked["first"] == {uuid.UUID(int=3)}
    for on_site in (asked["on_site"], asked["left_on_site"]):  # the website's own hosts only
        assert on_site(job(1)) is True
        assert on_site("https://www.other-board.test/viewjob?jk=1") is False
    assert asked["session"].opened_by_app == 0  # B27's counter counts the agent's reports only


def test_a_read_page_meets_the_persons_exclusions(monkeypatch) -> None:
    board = Board({job(1): posting("Robot Welder (Stage)")})
    done, commits, _ = phase(monkeypatch, board, [listed(1)])
    assert done["read"] == 1
    assert done["ended"] == "done"
    assert commits.saved[0].excluded == "Unwanted title: Stage"


def test_a_failed_reading_counts_its_last_try(monkeypatch) -> None:
    board = Board({job(1): ELSEWHERE, job(2): ELSEWHERE})
    done, _, _ = phase(
        monkeypatch, board, [listed(1), listed(2)], commits=Commits(last_try={uuid.UUID(int=2)})
    )
    assert (done["unread"], done["given_up"], done["ended"]) == (2, 1, "done")


def test_a_close_for_memory_ends_the_reading(monkeypatch) -> None:
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, closes={job(1)}
    )
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2)])
    assert done["ended"] == "memory"
    assert commits.saved == []  # the browser's close is no failed reading of the posting
    assert board.opened() == [job(1)]
    assert asked["session"].tab_closed["memory"] == 1


def test_a_permission_taken_back_in_the_middle_ends_it_and_keeps_nothing_more(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)})
    policy, commits = Policy(), Commits()
    commits.after = lambda saved: setattr(policy, "revoked", True)
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2), listed(3)], commits=commits, policy=policy
    )
    assert done["ended"] == "permission"
    assert len(commits.saved) == 1
    assert board.opened() == [job(1)]


def test_a_permission_taken_back_during_a_page_keeps_nothing_of_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    policy = Policy()
    original = board.call

    async def revoking(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_navigate" and args.get("url") == job(2):
            policy.revoked = True
        return await original(name, args)

    board.call = revoking  # type: ignore[method-assign]
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)], policy=policy)
    assert done["ended"] == "permission"
    assert [p.job_id.int for p in commits.saved] == [1]


def test_an_application_waiting_for_the_browser_comes_first(monkeypatch) -> None:
    leases = Leases()
    commits = Commits()
    commits.after = lambda saved: leases.waiting.append(("e2e-other", "apply_fill"))
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2)], commits=commits, leases=leases
    )
    assert done["ended"] == "browser_wanted"
    assert board.opened() == [job(1)]


def test_a_lease_store_that_cannot_say_counts_as_one_waiting(monkeypatch) -> None:
    leases = Leases()
    leases.gone = True
    board = Board({job(1): posting("Robot Welder")})
    done, _, _ = phase(monkeypatch, board, [listed(1)], leases=leases)
    assert done["ended"] == "browser_wanted"
    assert board.opened() == []


def test_the_time_bound_starts_no_page_that_could_end_past_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in range(1, 30)})
    held = [listed(n) for n in range(1, 30)]
    done, commits, _ = phase(monkeypatch, board, held, bound_s=120.0, step_s=10.0)
    assert done["ended"] == "time_bound"
    assert 0 < len(commits.saved) < len(held)
    nothing, none, _ = phase(monkeypatch, Board({}), held, bound_s=0.0)
    assert nothing is None
    assert none.saved == []


def test_a_failed_store_ends_it(monkeypatch) -> None:
    board = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)], commits=Commits(fail_at=0))
    assert done["ended"] == "store_failed"
    assert commits.saved == []
    assert board.opened() == [job(1)]
    ended = Commits()
    ended.unsettled = True  # the sweep let go of its row
    done, _, _ = phase(monkeypatch, Board({}), [listed(1)], commits=ended)
    assert done["ended"] == "store_failed"


def test_a_lease_ended_during_a_page_ends_it(monkeypatch) -> None:
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, end_after=None
    )
    original = board.call

    async def ending(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_tabs" and args.get("action") == "new" and len(board.opened()) == 1:
            from app.browser.client import BrowserError

            raise BrowserError("ended")
        return await original(name, args)

    board.call = ending  # type: ignore[method-assign]
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert done["ended"] == "browser_lost"
    assert [p.outcome for p in commits.saved] == ["read"]


@pytest.mark.parametrize(
    ("stopped", "ran", "runs"),
    [
        ("", "", True),
        ("", "error", True),  # the agent's run failed: the browser and the lease are there
        ("", "turn_limit", True),
        ("token_budget", "", True),
        ("browser_memory", "", True),
        ("page_failed", "", True),
        ("", "cancelled", False),  # the person's Cancel
        ("", "no_credential", False),
        ("lease_ended", "", False),
        ("browser_unavailable", "", False),
        ("guard_failed", "", False),
        ("permission_revoked", "", False),
        ("store_failed", "", False),
    ],
)
def test_when_it_runs_after_the_agent(monkeypatch, stopped: str, ran: str, runs: bool) -> None:
    board = Board({job(1): posting("Robot Welder")})
    done, commits, asked = phase(monkeypatch, board, [listed(1)], stopped=stopped, ran=ran)
    assert (done is not None) == runs
    assert len(commits.saved) == runs
    assert asked["session"].stopped == stopped  # the agent's stop stays the website's


@pytest.mark.parametrize(
    "no_tab",
    [
        {"ok": False, "error": "too_many_tabs"},  # the agent left its 3 tabs open
        BrowserError("busy"),
        {"ok": False, "error": "person_input"},  # the person holds the input
        {"ok": True, "tabs": [{"index": 0, "url": START, "current": True}]},  # a tab it cannot name
    ],
)
def test_no_tab_of_its_own_stores_nothing_and_ends_the_reading(monkeypatch, no_tab) -> None:
    """Review of PR 864, item 1: browserd made no tab, so no page was opened: nothing is stored
    against any posting, and the reading ends with a reason the summary says."""
    board = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)}, no_tab=no_tab)
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2), listed(3)])
    assert done == {
        "read": 0, "closed": 0, "unread": 0, "given_up": 0, "ended": "no_tab", "left": 7,
    }  # fmt: skip
    assert commits.saved == []
    assert board.opened() == []
    assert [a["action"] for n, a in board.calls if n == "browser_tabs"].count("new") == 1
    assert "pauses" not in asked
    assert asked["session"].stopped == ""


def test_a_redirect_to_a_list_or_to_another_posting_is_never_read(monkeypatch) -> None:
    """Review of PR 864, item 3: the posting's address redirects to the website's search page
    listing its title, or to another posting's page holding it: no page is read as the
    posting, each is a failed reading."""
    search = START + "search?q=robot"
    board = Board(
        {search: LIST_PAGE, job(9): posting("Robot Welder")},
        redirects={job(1): search, job(2): job(9)},
    )
    done, commits, _ = phase(monkeypatch, board, [listed(1), listed(2)])
    assert [(p.outcome, p.finding) for p in commits.saved] == [("unread", None)] * 2
    assert (done["read"], done["unread"], done["ended"]) == (0, 2, "done")
    assert board.opened() == [job(1), job(2)]


def test_a_close_for_memory_named_by_a_navigate_that_loaded_ends_the_reading(monkeypatch) -> None:
    """Review of PR 864, follow up 5: the memory guard closed the app's tab during its pause and
    the navigate that followed loaded the page: the close is counted and ends the reading."""
    board = Board(
        {job(1): posting("Robot Welder"), job(2): posting("Robot Welder")}, reopens={job(1)}
    )
    done, commits, asked = phase(monkeypatch, board, [listed(1), listed(2)])
    assert asked["session"].tab_closed["memory"] == 1
    assert done["ended"] == "memory"
    assert board.opened() == [job(1)]
    assert [p.outcome for p in commits.saved] == ["read"]  # its page loaded and was read


def test_the_counts_are_what_the_store_did(monkeypatch) -> None:
    """Review of PR 864, item 5: a page the store left as it was (read meanwhile, its address
    changed, a posting the app closed before) is counted in nothing."""
    board = Board({job(1): posting("Robot Welder"), job(2): "", job(3): ELSEWHERE},
                  statuses={job(2): 404})  # fmt: skip
    kept = {uuid.UUID(int=n) for n in (1, 2, 3)}
    done, commits, _ = phase(
        monkeypatch, board, [listed(1), listed(2), listed(3)], commits=Commits(left_as_is=kept)
    )
    assert [p.outcome for p in commits.saved] == ["read", "gone", "unread"]
    assert (done["read"], done["closed"], done["unread"], done["ended"]) == (0, 0, 0, "done")


def test_two_websites_of_one_search_on_one_host_open_a_posting_once(monkeypatch) -> None:
    """Review of PR 864, follow up 6: two saved addresses of one website in one search: the
    second website's reading opens none of the postings the first opened."""
    tried: set[uuid.UUID] = set()
    first = Board({job(n): posting("Robot Welder") for n in (1, 2)})
    phase(monkeypatch, first, [listed(1), listed(2)], tried=tried)
    second = Board({job(n): posting("Robot Welder") for n in (1, 2, 3)})
    done, commits, _ = phase(monkeypatch, second, [listed(1), listed(2), listed(3)], tried=tried)
    assert first.opened() == [job(1), job(2)]
    assert second.opened() == [job(3)]
    assert [p.job_id.int for p in commits.saved] == [3]
    assert done["read"] == 1
