"""Source-bound browserd, actual Chromium, host SDK registry and runner, no real sites."""

import asyncio
import dataclasses
import hashlib
import json
import os
import random
import uuid
from pathlib import Path

import httpx
import pytest

from app.browser import finder, sources
from app.browser.client import BrowserdClient, BrowserError
from app.browser.tools import LeaseTools
from tests.browser.b3_support import World, flush, redis_url  # noqa: F401
from tests.browser.b13_runtime import Recording
from tests.browser.runtime_support import (  # noqa: F401
    Runtime,
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runtime,
)
from tests.browser.test_b5_container import TOOL_CALL, Lease, ref
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
START = "http://pages:8080/b13/"
# The fixture server's names: `elsewhere`, and Indeed's home and a country site under `.test`
# (discovery.FAMILIES, owner 2026-10-28 and 2026-10-29), so a request to any of them is in its log.
PRIVATE = ["pages", "elsewhere", "www.indeed.test", "fr.indeed.test"]
INDEED = "http://www.indeed.test:8080/b13/indeed"


@pytest.fixture
def native(tmp_path):
    # Explicit opt-in avoids this proof ever building or pulling an image implicitly.
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path / "native.yml"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "browser": {
                        "pull_policy": "never",
                        "volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"],
                        "environment": {"BROWSER_PROXY_ALLOW_PRIVATE": ",".join(PRIVATE)},
                    },
                    "client": {"pull_policy": "never"},
                    "pages": {
                        "pull_policy": "never",
                        "command": ["python", "-I", "/b13_pages.py"],
                        "volumes": [f"{Path(__file__).with_name('b13_pages.py')}:/b13_pages.py:ro"],
                        "networks": {"egress": {"aliases": PRIVATE[1:]}},
                    },
                }
            }
        )
    )
    stack = Stack("b632-fix-r1-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        for path in (
            "chromium.py",
            "detect.py",
            "pages.py",
            "tools/actions.py",
            "tools/discovery.py",
        ):
            expected = hashlib.sha256((ROOT / "browser/browserd" / path).read_bytes()).hexdigest()
            actual = stack.python(
                "import hashlib,pathlib,sys; "
                "print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
                "/opt/browserd/browserd/" + path,
            ).strip()
            assert actual == expected
        expected_fixture = hashlib.sha256(
            Path(__file__).with_name("b13_pages.py").read_bytes()
        ).hexdigest()
        mounted_fixture = stack.execute(
            "pages",
            "python",
            "-I",
            "-c",
            "import hashlib,pathlib; "
            "print(hashlib.sha256(pathlib.Path('/b13_pages.py').read_bytes()).hexdigest())",
        ).strip()
        assert mounted_fixture == expected_fixture
        yield stack
    finally:
        if not getattr(stack, "completed", False):
            diagnostic = stack.run("logs", "--no-color", "--tail", "80", "browser", check=False)
            for secret in stack.redactions:
                diagnostic = diagnostic.replace(secret, "[redacted]")
            print(diagnostic)
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


@pytest.mark.parametrize("verified", [False, True])
def test_native_search_through_real_runtime(native, runtime: Runtime, alice, redis_url, verified):  # noqa: F811
    runtime.give_key(alice)
    recording = Recording()
    context = dataclasses.replace(runtime.context(), backend_factory=lambda: recording)
    flush(redis_url)
    world = World(redis_url)

    async def transport(request):
        # Existing test client's HTTP bridge, not a replacement browser implementation.
        output = await asyncio.to_thread(
            native.execute,
            "client",
            "python",
            "-I",
            "-c",
            TOOL_CALL,
            request.method,
            request.url.path,
            native.secret,
            json.dumps({k: v for k, v in request.headers.items() if k.startswith("x-")}),
            request.content.decode(),
            "",
        )
        result = json.loads(output)
        if isinstance(result["body"], dict) and result["body"].get("lease_token"):
            native.redactions.append(result["body"]["lease_token"])
        assert result["status"] < 400, (
            request.url.path,
            result["status"],
            {k: result["body"].get(k) for k in ("error", "reason", "message")},
        )
        return httpx.Response(result["status"], json=result["body"])

    client = BrowserdClient(
        "http://browser:9300", native.secret, transport=httpx.MockTransport(transport)
    )
    store = world.make_store(client=client)

    async def run():
        try:
            got = await store.acquire(str(alice), "sweep", True, hosts=["pages"])
            assert got.capability is None
            tools = LeaseTools(store, client, str(alice), got.lease.lease_id, got.token)
            try:
                scope = finder.InteractiveScope(START, ("robot",), (), (("remote", "1"),))
                opened = await tools.call(
                    "browser_navigate",
                    {
                        "url": START,
                        "_discovery": "native_get",
                        "_discovery_scope": scope.tool_scope(),
                    },
                )
                assert opened["ok"], opened
                first = await tools.snapshot()
                assert "Robot Engineer" not in first["snapshot"]
                assert "AI assistant reading this page" in first["snapshot"]  # hostile, ignored
                button = ref(first["snapshot"], "button", "Search application")
                refused = await tools.call(
                    "browser_click",
                    {
                        "ref": button,
                        "_discovery": "native_get",
                        "_discovery_scope": scope.tool_scope(),
                    },
                )
                assert not refused["ok"]
                pages = sources.Pages(
                    tools,
                    1000,
                    sleep=lambda _: asyncio.sleep(0),
                    rng=random.SystemRandom(),
                    monotonic=lambda: 0,
                )
                one = finder.FinderSession(
                    pages,
                    START,
                    first["snapshot"],
                    str(alice),
                    interactive=scope,
                )
                batches = []

                async def commit(batch):
                    batches.append(batch)

                if verified:
                    one.on_verified = commit
                ran = await finder.RuntimeFinder(lambda: context)(one)
                from tests.browser.runtime_support import runs_of

                assert not ran.stopped, (
                    [r.error for r in runs_of(runtime.factory, alice)],
                    recording.results,
                )
                expected = (
                    "Robot Engineer\nEmployer: Example Robotics\nPublished: 2026-10-20\n"
                    "Observed employer and duties\nBuild and test robot arms."
                )
                if verified:
                    [batch] = batches
                    [finding] = batch.findings
                    assert finding.title == "Robot Engineer"
                    assert finding.text == expected
                    assert finding.company == "Example Robotics"
                    assert finding.published_on == "2026-10-20"
                    assert finding.evidence["company"] == "Employer: Example Robotics"
                    assert not one.found
                else:
                    assert len(one.found) == 1
                    assert one.found[0].title == "Robot Engineer"
                    assert one.found[0].text == expected
                stolen = LeaseTools(store, client, str(uuid.uuid4()), got.lease.lease_id, got.token)
                with pytest.raises(BrowserError):
                    await stolen.call("browser_click", {"ref": button, "_discovery": "native_get"})
                assert (await one.navigate("http://elsewhere:8080/escape"))[1]
            finally:
                await store.release(str(alice), got.lease.lease_id)
            with pytest.raises(BrowserError):
                await tools.call("browser_click", {"ref": button, "_discovery": "native_get"})
        finally:
            await world.close()

    asyncio.run(run())
    observed = json.loads(
        native.execute(
            "browser",
            "python",
            "-I",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://pages:8080/observed').read().decode())",
        )
    )
    searches = [p["query"] for p in observed if p["path"] == "/b13/search"]
    assert searches == [
        {"q": ["robot"], "remote": ["1"]},
        {"q": ["robot"], "remote": ["1"], "page": ["2"]},
    ]
    assert not any(p["path"] in {"/apply", "/escape"} for p in observed)
    native.completed = True


def test_native_hostile_controls_and_redirects(native):
    failures = []
    for legacy in (True, False):
        for path in (
            "handler",
            "shadow",
            "named",
            "hidden",
            "default",
            "base",
            "redirect-off",
            "redirect-write",
        ):
            lease = Lease(native, "sweep")
            native.redactions.append(lease.token)
            scope = {"start": START + path, "titles": ["robot"], "places": [], "filters": {}}
            marker = {"_discovery": "native_get", "_discovery_scope": scope}
            try:
                assert lease.capability is None
                opened = lease.tool(
                    "browser_navigate", url=scope["start"], **({} if legacy else marker)
                )
                assert opened["ok"], (legacy, path, opened)
                button = ref(lease.snapshot(), "button", "Search")
                answer = lease.tool("browser_click", ref=button, **marker)
                if answer.get("ok"):
                    failures.append((legacy, path))
                assert answer["error"] in {"unsupported_discovery", "host_not_allowed"}
            finally:
                lease.end()
    observed = json.loads(
        native.execute(
            "browser",
            "python",
            "-I",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://pages:8080/observed').read().decode())",
        )
    )
    assert not failures, failures
    assert not any(p["path"] in {"/b13/custom-action", "/escape", "/remove"} for p in observed)
    assert not any(p.get("method") == "POST" for p in observed)
    assert [
        p["path"] for p in observed if p["path"] in {"/b13/off/search", "/b13/write/search"}
    ] == ["/b13/off/search", "/b13/write/search"]
    assert not any(p["path"] == "/b13/search" for p in observed)
    native.completed = True


def fetched(native, url: str) -> str:
    """`url` as the browser container reads it with plain Python, outside Chromium."""
    return native.execute(
        "browser",
        "python",
        "-I",
        "-c",
        f"import urllib.request; print(urllib.request.urlopen({url!r}).read().decode())",
    )


def test_native_start_page_redirect_on_its_own_host_is_followed(native):
    """Jobs J18 (issue 716): the start page's own redirect to its locale path is followed and
    its search runs; a start page redirected to another host or a write address is not, nor one
    whose Location Python's urljoin keeps on the host while Chromium goes to `elsewhere`."""
    answers = {}
    for path in ("locale", "locale-off", "locale-write", "locale-slash", "locale-slashes"):
        lease = Lease(native, "sweep")
        native.redactions.append(lease.token)
        scope = {
            "start": START + path,
            "titles": ["robot"],
            "places": [],
            "filters": {"remote": "0"},
        }
        marker = {"_discovery": "native_get", "_discovery_scope": scope}
        try:
            opened = lease.tool("browser_navigate", url=scope["start"], **marker)
            answers[path] = opened
            if path == "locale":
                assert opened["ok"], opened
                shot = lease.tool("browser_snapshot")
                assert shot["url"] == START + "en/", shot
                text = shot["snapshot"]
                box = ref(text, "searchbox", "Query")
                assert lease.tool("browser_type", ref=box, text="robot", **marker)["ok"]
                found = lease.tool("browser_click", ref=ref(text, "button", "Search"), **marker)
                assert found["ok"], found
        finally:
            lease.end()
    for path in ("locale-off", "locale-write", "locale-slash", "locale-slashes"):
        assert not answers[path]["ok"], (path, answers[path])
    # `elsewhere` is this same fixture server under its other name, and its log records the
    # Host of each request: one request sent there by the test shows the log sees that host,
    # so no request of Chromium's to it means none was made.
    fetched(native, "http://elsewhere:8080/b13/seen-elsewhere")
    observed = json.loads(fetched(native, "http://pages:8080/observed"))
    assert {"path": "/b13/seen-elsewhere", "host": "elsewhere:8080"} in [
        {"path": p["path"], "host": p.get("host")} for p in observed
    ]
    chromium = [p for p in observed if p["path"] not in {"/observed", "/b13/seen-elsewhere"}]
    assert not any(str(p.get("host")).startswith("elsewhere") for p in chromium), chromium
    assert not any(
        p["path"] in {"/escape", "/remove", "/b13/escape-slash", "/b13/escape-slashes"}
        or "\\" in p["path"]
        for p in chromium
    )
    assert {"path": "/b13/search", "query": {"q": ["robot"], "remote": ["0"]}} in [
        {"path": p["path"], "query": p.get("query")} for p in observed
    ]
    native.completed = True


def test_native_indeed_home_redirect_to_its_country_site_is_followed(native):
    """Owner, 2026-10-28 and 2026-10-29 (B19): Indeed's home sends the start page to the
    person's country site, a host of its family, and the search continues there. A redirect to
    another domain is refused and its target never requested, and so is one to a host the
    lease's list does not hold (test_b19_families holds the rest)."""
    answers = {}
    family = ["indeed.test", "*.indeed.test"]
    for path, hosts in (
        ("indeed", family),
        ("indeed-off", family),
        ("indeed", ["www.indeed.test"]),
    ):
        start = INDEED + path.removeprefix("indeed")
        lease = Lease(native, "sweep", hosts=hosts)
        native.redactions.append(lease.token)
        scope = {"start": start, "titles": ["robot"], "places": [], "filters": {"remote": "0"}}
        marker = {"_discovery": "native_get", "_discovery_scope": scope}
        country = hosts is family
        try:
            opened = lease.tool("browser_navigate", url=start, **marker)
            answers[path, country] = opened
            if (path, country) == ("indeed", True):
                assert opened["ok"], opened
                shot = lease.tool("browser_snapshot")
                assert shot["url"] == "http://fr.indeed.test:8080/b13/indeed", shot
                text = shot["snapshot"]
                box = ref(text, "searchbox", "Query")
                assert lease.tool("browser_type", ref=box, text="robot", **marker)["ok"]
                found = lease.tool("browser_click", ref=ref(text, "button", "Search"), **marker)
                assert found["ok"], found
        finally:
            lease.end()
    assert not answers["indeed-off", True]["ok"]
    assert not answers["indeed", False]["ok"]  # the lease's list still bounds the family
    observed = json.loads(fetched(native, "http://pages:8080/observed"))
    hosts = [(p.get("host"), p["path"], p.get("query")) for p in observed]
    assert ("fr.indeed.test:8080", "/b13/search", {"q": ["robot"], "remote": ["0"]}) in hosts
    # The redirect to another domain was made (its page was requested), its target never was
    # (`elsewhere` is in this log, test_native_start_page_redirect_on_its_own_host_is_followed);
    # the run whose list holds the home alone never reached the country site (one request there).
    assert ("www.indeed.test:8080", "/b13/indeed-off") in [(h, p) for h, p, _ in hosts]
    assert not any(host.startswith("elsewhere") for host, _, _ in hosts if host), hosts
    assert [h for h, p, _ in hosts if p == "/b13/indeed"].count("fr.indeed.test:8080") == 1
    native.completed = True


@pytest.mark.parametrize(
    ("case", "suffix", "company"),
    [
        ("nested-foreign", "Maintain the primary service.", None),
        ("nested-article", "Build reliable tools\nwith the guide.", "Example Robotics"),
        ("headed-list", "Fonctions Ω\nBuild reliable tools\nwith the guide.", "Example Robotics"),
        ("flat-owned", "Fonctions Ω\nBuild reliable tools\nwith the guide.", "Example Robotics"),
        ("flat-foreign", "Maintain the primary service.", "Example Robotics"),
        ("nested-refused", None, None),
        ("flat-refused", None, None),
    ],
)
def test_native_detail_ownership_and_prose(native, tmp_path, case, suffix, company):
    lease = Lease(native, "sweep")
    native.redactions.append(lease.token)

    async def scenario():
        class NativeTools:
            async def call(self, name, args):
                return await asyncio.to_thread(lease.tool, name, **args)

            async def snapshot(self):
                return await self.call("browser_snapshot", {})

        tools = NativeTools()
        start = START + "details"
        scope = finder.InteractiveScope(start, ("robot",))
        opened = await tools.call(
            "browser_navigate",
            {"url": start, "_discovery": "native_get", "_discovery_scope": scope.tool_scope()},
        )
        assert opened["ok"], opened
        first = (await tools.snapshot())["snapshot"]
        (tmp_path / "first-snapshot.txt").write_text(first)
        rng = random.SystemRandom()
        pages = sources.Pages(
            tools, 1000, sleep=lambda _: asyncio.sleep(0), rng=rng, monotonic=lambda: 0
        )
        batches = []

        async def commit(batch):
            batches.append(batch)

        one = finder.FinderSession(pages, start, first, "e2e-b5", interactive=scope)
        one.on_verified = commit
        answer, error = await one.navigate(start + "/" + case)
        (tmp_path / "navigate-result.json").write_text(
            json.dumps({"case": case, "answer": answer, "error": error})
        )
        assert not error, answer
        (tmp_path / "snapshot.json").write_text(answer)
        claim = {"title": "Robot Engineer", "url": start + "/" + case, "text": "Model text."}
        if case == "nested-foreign":
            answer, error = await one.report_verified(
                [{**claim, "company": "Other Systems", "published_on": "2026-10-19"}]
            )
            assert not error
            assert json.loads(answer)["kept"] == 0
            assert not batches
        answer, error = await one.report_verified([claim])
        assert not error
        assert json.loads(answer)["kept"] == int(suffix is not None)
        if suffix is None:
            assert not batches
        else:
            [batch] = batches
            [finding] = batch.findings
            employer = "Employer: Example Robotics\n" if company else ""
            assert finding.text == "Robot Engineer\n" + employer + suffix
            assert finding.company == company
            assert finding.published_on is None

    try:
        asyncio.run(scenario())
    finally:
        lease.end()
    native.completed = True


JUNGLE = START + "jungle"
PAGES = "http://pages:8080/"  # its search page sits at the root, /fr/jobs


class Person(Lease):
    """A lease of one person's own browser profile (`profile`), kept from one lease to the next,
    as a website the person signed in to keeps its cookie (B17)."""

    def __init__(self, stack, purpose: str, profile: str) -> None:
        self.stack = stack
        body = {
            "user_id": "e2e-b5",
            "profile_id": profile,
            "uid": 20000 + uuid.UUID(profile).int % 30000,
            "purpose": purpose,
            "hosts": ["pages"],
            "submit_selectors": [],
        }
        answer = stack.call("POST", "/leases", body)
        assert answer["status"] == 201, answer
        self.id = answer["body"]["lease_id"]
        self.token = answer["body"]["lease_token"]
        self.capability = answer["body"].get("gate_capability")


def test_native_a_website_like_the_jungle_is_searched_as_a_person_would(native):
    """2026-10-28, issue 716, on a website like Welcome to the Jungle (b13_pages.jungle): from its
    home with no search box to its search page by address; its cookie banner covers the search
    box (`element_covered`), accepting it is refused, declining it closes it; the search runs in
    the page, its address unchanged, and lists the postings to a member; a posting opens. Signed
    out, with no JavaScript, the same search shows only a count, no list."""
    profile = str(uuid.uuid4())
    member = Person(native, "sweep", profile)  # the person signs in, as in the live view
    native.redactions.append(member.token)
    try:
        assert member.tool("browser_navigate", url=JUNGLE + "/session")["ok"]
    finally:
        member.end()
    scope = {"start": JUNGLE, "titles": ["genai engineer"], "places": [], "filters": {}}
    signed = {"_discovery": "native_get", "_discovery_scope": {**scope, "signed_in": True}}
    lease = Person(native, "sweep", profile)
    native.redactions.append(lease.token)
    try:
        assert lease.tool("browser_navigate", url=JUNGLE, **signed)["ok"]
        home = lease.tool("browser_snapshot")["snapshot"]
        assert "Find a job" in home
        assert "searchbox" not in home
        assert lease.tool("browser_navigate", url=PAGES + "fr/jobs", **signed)["ok"]
        page = lease.tool("browser_snapshot")["snapshot"]
        box = ref(page, "searchbox", "What job")
        covered = lease.tool("browser_type", ref=box, text="GenAI Engineer", **signed)
        assert covered["error"] == "element_covered", covered
        for name in ("OK for me", "I choose"):
            refused = lease.tool("browser_click", ref=ref(page, "button", name), **signed)
            assert not refused["ok"], (name, refused)
        declined = lease.tool("browser_click", ref=ref(page, "button", "No, thanks"), **signed)
        assert declined["ok"], declined
        assert declined.get("declined") is True, declined  # the api never counts it a search
        page = lease.tool("browser_snapshot")["snapshot"]
        assert "No, thanks" not in page
        box = ref(page, "searchbox", "What job")
        assert lease.tool("browser_type", ref=box, text="GenAI Engineer", **signed)["ok"]
        searched = lease.tool("browser_click", ref=ref(page, "button", "Search"), **signed)
        assert searched["ok"], searched
        assert "declined" not in searched, searched
        assert lease.tool("browser_wait_for", text="GenAI Engineer")["ok"]
        shot = lease.tool("browser_snapshot")
        assert shot["url"] == PAGES + "fr/jobs", shot["url"]  # the list updated in place
        posting = ref(shot["snapshot"], "link", "GenAI Engineer")
        opened = lease.tool("browser_click", ref=posting, **signed)
        assert opened["ok"], opened
        shot = lease.tool("browser_snapshot")
        assert shot["url"] == PAGES + "fr/companies/example-robotics/jobs/genai-engineer"
        assert "Build agents with the customers" in shot["snapshot"]
    finally:
        lease.end()
    anonymous = Lease(native, "sweep")
    native.redactions.append(anonymous.token)
    marker = {"_discovery": "native_get", "_discovery_scope": scope}
    try:
        assert anonymous.tool("browser_navigate", url=JUNGLE, **marker)["ok"]
        assert anonymous.tool("browser_navigate", url=PAGES + "fr/jobs", **marker)["ok"]
        page = anonymous.tool("browser_snapshot")["snapshot"]
        assert "No, thanks" not in page  # its banner needs JavaScript, off signed out
        box = ref(page, "searchbox", "What job")
        typed = anonymous.tool("browser_type", ref=box, text="genai engineer", **marker)
        assert not typed["ok"], typed
        # A page with scripts takes no typing signed out (no script free proof); its search by
        # address does, and shows a count, no list.
        searched = PAGES + "fr/jobs?query=genai+engineer"
        assert anonymous.tool("browser_navigate", url=searched, **marker)["ok"]
        shot = anonymous.tool("browser_snapshot")
        assert "9756 jobs found" in shot["snapshot"]
        assert "GenAI Engineer" not in shot["snapshot"]
    finally:
        anonymous.end()
    observed = json.loads(fetched(native, "http://pages:8080/observed"))
    jungle = [p for p in observed if p["path"].startswith("/b13/jungle")]
    assert {"path": "/b13/jungle/consent", "choice": ["declined"]} in [
        {"path": p["path"], "choice": p["query"].get("choice")} for p in jungle
    ]
    assert not any(p["query"].get("choice") == ["accepted"] for p in jungle)
    assert not any(p.get("method") == "POST" for p in observed)
    lists = [p for p in jungle if p["path"] == "/b13/jungle/api/search"]
    assert [(p["query"], p["member"]) for p in lists] == [({"query": ["GenAI Engineer"]}, True)]
    native.completed = True
