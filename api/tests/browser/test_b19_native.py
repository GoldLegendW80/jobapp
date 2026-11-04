"""B19 on real Chromium in the browser container: a signed in search of websites shaped like the
real ones (tests/browser_pages/server.py, b19/), under the `.test` stand in names of their
platforms' families. Indeed's home sends the search to its country site with a query, its native
GET form with Indeed's own field names searches by Enter, a posting opens with its tracking
query. Since B20 any text is typed (a title not saved too), the person's own list and the sign in
host open as any public address does (the api reads a sign in page, not browserd), and nothing
of a query is logged. Welcome to the Jungle's script search box outside any form searches by
Enter and by its magnifier button through a POST to its search service, which renders the list;
the Apply button and the Easy Apply link beside the box stay refused, and nothing is ever posted
to the website itself.
Needs an existing BROWSER_TEST_IMAGE; never builds or pulls one.
"""

import json
import os
import uuid

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_b13_native import fetched
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
NAMES = [
    "www.indeed.test",
    "fr.indeed.test",
    "secure.indeed.test",
    "www.welcometothejungle.test",
    "abc-dsn.algolia.test",
]


@pytest.fixture
def b19(tmp_path):
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path / "b19.yml"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "browser": {
                        "pull_policy": "never",
                        "volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"],
                        "environment": {
                            "BROWSER_PROXY_ALLOW_PRIVATE": ",".join(["pages", "elsewhere", *NAMES])
                        },
                    },
                    "client": {"pull_policy": "never"},
                    "pages": {
                        "pull_policy": "never",
                        "networks": {"egress": {"aliases": ["elsewhere", *NAMES]}},
                    },
                }
            }
        )
    )
    stack = Stack("b19-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        yield stack
    finally:
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


def signed(start: str) -> dict:
    scope = {"start": start}  # B20: browserd gets the start alone
    return {"_discovery": "native_get", "_discovery_scope": {**scope, "signed_in": True}}


def test_indeed_s_country_site_is_searched_by_its_own_form(b19):
    start = "http://www.indeed.test:8080/"
    marker = signed(start)
    lease = Lease(b19, "sweep", hosts=["indeed.test", "*.indeed.test"])
    b19.redactions.append(lease.token)
    try:
        opened = lease.tool("browser_navigate", url=start, **marker)
        assert opened["ok"], opened
        assert opened["url"] == "http://fr.indeed.test:8080/?r=us"
        page = lease.tool("browser_snapshot")["snapshot"]
        what, where = ref(page, "textbox", "What"), ref(page, "textbox", "Where")
        unsaved = lease.tool("browser_type", ref=what, text="Prompt Engineer", **marker)
        assert unsaved["ok"], unsaved  # B20: the agent types what it judges right
        assert lease.tool("browser_type", ref=where, text="Paris", **marker)["ok"]
        searched = lease.tool(
            "browser_type", ref=what, text="GenAI Engineer", submit=True, **marker
        )
        assert searched["ok"], searched
        shot = lease.tool("browser_snapshot")
        assert shot["url"].endswith("/jobs?q=GenAI+Engineer&l=Paris&from=searchOnHP"), shot["url"]
        posting = lease.tool("browser_click", ref=ref(shot["snapshot"], "link", "GenAI"), **marker)
        assert posting["ok"], posting
        assert "Build agents" in lease.tool("browser_snapshot")["snapshot"]
        assert lease.tool("browser_navigate", url="http://fr.indeed.test:8080/", **marker)["ok"]
        page = lease.tool("browser_snapshot")["snapshot"]
        # B20: the person's own list opens (here its sign in page, the login having expired),
        # and so does a recent search sent to the sign in host; the api reads the sign in page.
        for name in ("My jobs", "Recent search"):
            assert lease.tool("browser_navigate", url="http://fr.indeed.test:8080/", **marker)["ok"]
            page = lease.tool("browser_snapshot")["snapshot"]
            moved = lease.tool("browser_click", ref=ref(page, "link", name), **marker)
            assert moved["ok"], (name, moved)
            assert moved["url"].startswith("http://secure.indeed.test:8080/auth?continue="), moved
        for off in ("http://secure.indeed.test:8080/auth", "http://elsewhere:8080/"):
            assert lease.tool("browser_navigate", url=off, **marker)["ok"], off
    finally:
        lease.end()
    logs = b19.run("logs", "--no-color", "browser", check=False)
    assert "host=secure.indeed.test" not in logs  # nothing was stopped
    assert "continue" not in logs  # no query is ever logged


def test_the_jungle_s_script_box_searches_through_its_search_service(b19):
    start = "http://www.welcometothejungle.test:8080/fr/jobs"
    marker = signed(start)
    lease = Lease(b19, "sweep", hosts=["welcometothejungle.test", "www.welcometothejungle.test"])
    b19.redactions.append(lease.token)
    try:
        assert lease.tool("browser_navigate", url=start, **marker)["ok"]
        page = lease.tool("browser_snapshot")["snapshot"]
        box, where = ref(page, "combobox", "Search for a job"), ref(page, "combobox", "Where")
        # B20: any text is typed in any box, a title in the place box too; the place replaces it.
        assert lease.tool("browser_type", ref=where, text="GenAI Engineer", **marker)["ok"]
        assert lease.tool("browser_type", ref=where, text="Paris", **marker)["ok"]
        searched = lease.tool("browser_type", ref=box, text="GenAI Engineer", submit=True, **marker)
        assert searched["ok"], searched
        assert lease.tool("browser_wait_for", text="GenAI Engineer")["ok"]
        page = lease.tool("browser_snapshot")["snapshot"]
        for role, name in (("button", "Apply"), ("link", "Easy Apply")):
            refused = lease.tool("browser_click", ref=ref(page, role, name), **marker)
            assert refused["error"] == "submit", (name, refused)
        assert lease.tool("browser_click", ref=ref(page, "button", "Search"), **marker)["ok"]
        page = lease.tool("browser_snapshot")["snapshot"]
        posting = lease.tool("browser_click", ref=ref(page, "link", "GenAI Engineer"), **marker)
        assert posting["ok"], posting
        assert posting["url"].endswith("/jobs/genai-engineer_paris?q=0f3e2a&o=12"), posting
    finally:
        lease.end()
    seen = json.loads(fetched(b19, "http://pages:8080/b19/log"))
    posts = [(s["host"], s["path"].split("?")[0]) for s in seen if s["method"] == "POST"]
    assert posts == [("abc-dsn.algolia.test", "/1/indexes/*/queries")] * 2
    assert not any("apply" in s["path"] for s in seen)
    b19.completed = True
