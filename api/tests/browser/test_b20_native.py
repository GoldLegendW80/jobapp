"""B20 on real Chromium in the browser container: job websites only an unguarded agent can search
(tests/browser_pages/server.py, b20/), searched through a sweep lease's discovery the way Claude
Code with Playwright CLI searches them on the owner's Mac, and the line held on every path.

- The board: a results list rendered by JavaScript (the anonymous context runs it), a cookie
  banner over the page whose form POSTs the choice (a consent form leaves), more results as the
  list scrolls, a posting in the same tab (Back and Forward), one in a second tab (tabs), and one
  on an ATS host.
- The company's search on another host of the company: a GET form, and a POST form to /search.
- The ATS posting: Save job and Follow leave; Apply, Easy Apply, Send message and Enter in the
  application form are refused; the agent's own script is refused `element.click()`,
  `form.submit()` and `form.requestSubmit()` on Apply, and its fetch POST to a message or apply
  address never leaves; what code cannot see inside a script (another realm's `submit`, a click
  it dispatches itself, a worker, a socket) is measured, and the request floor holds what leaves.
- B19's Welcome to the Jungle stand in, now anonymous: its Algolia search leaves (its client's
  metadata says "connect" and "application").

The server's log (GET /b20/log, /b19/log) is what the websites saw. Needs an existing
BROWSER_TEST_IMAGE; never builds or pulls one.
"""

import json
import os
import re
import uuid

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
NAMES = [
    "www.b20-board.test",
    "careers.b20-robotics.test",
    "search.b20-robotics.test",
    "ats.b20-hire.test",
    "www.welcometothejungle.test",
    "abc-dsn.algolia.test",
]
BOARD = "http://www.b20-board.test:8080/"
CAREERS = "http://careers.b20-robotics.test:8080/"
ATS = "http://ats.b20-hire.test:8080/example-robotics/jobs/42"
JUNGLE = "http://www.welcometothejungle.test:8080/fr/jobs"


@pytest.fixture(scope="module")
def b20(tmp_path_factory):
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path_factory.mktemp("b20") / "b20.yml"
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
    stack = Stack("b20-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        yield stack
    finally:
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


def fetched(stack: Stack, url: str) -> str:
    """`url` as the browser container reads it with plain Python, outside Chromium."""
    code = f"import urllib.request; print(urllib.request.urlopen({url!r}).read().decode())"
    return stack.execute("browser", "python", "-I", "-c", code)


def seen(stack: Stack, log: str = "b20") -> list[dict]:
    return json.loads(fetched(stack, f"http://pages:8080/{log}/log"))


def named(snapshot: str, role: str, name: str) -> str:
    """The ref of the element of that role whose name is exactly `name`."""
    found = re.search(rf'\b{role} "{re.escape(name)}"[^\n]*\[ref=(e\d+)\]', snapshot)
    assert found, f"no {role} {name!r} in:\n{snapshot[:3000]}"
    return found.group(1)


class Search:
    """A sweep lease's discovery: every call carries the api's marker and scope (B20)."""

    def __init__(self, stack: Stack, start: str) -> None:
        self.lease = Lease(stack, "sweep", hosts=["b20.test"])
        stack.redactions.append(self.lease.token)
        self.marker = {"_discovery": "native_get", "_discovery_scope": {"start": start}}

    def __call__(self, name: str, **args):
        return self.lease.tool(name, **args, **self.marker)

    def page(self) -> str:
        shot = self("browser_snapshot")
        assert shot["ok"], shot
        return shot["snapshot"]

    def evaluate(self, script: str) -> dict:
        return self("browser_evaluate", function=script)


@pytest.fixture
def search(b20):
    made: list[Search] = []

    def start(url: str) -> Search:
        one = Search(b20, url)
        made.append(one)
        opened = one("browser_navigate", url=url)
        assert opened["ok"], opened
        return one

    yield start
    for one in made:
        one.lease.end()


def test_a_board_rendered_by_javascript_behind_a_banner_scrolled_back_and_in_two_tabs(b20, search):
    board = search(BOARD)
    page = board.page()
    assert 'link "Robot Engineer 2"' in page  # rendered by the page's own script
    assert "Drone Pilot 13" not in page
    covered = board("browser_click", ref=named(page, "link", "Robot Engineer 2"))
    assert covered["error"] == "element_covered", covered  # the banner over the page
    accepted = board("browser_click", ref=ref(page, "button", "Accept all"))
    assert accepted["ok"], accepted  # a consent form's POST leaves
    assert board("browser_wait_for", textGone="We use cookies")["ok"]
    assert board("browser_wait_for", text="Robot Engineer 2")["ok"]
    page = board.page()
    assert 'button "Accept all"' not in page
    # More results as the list scrolls to its end.
    for _ in range(3):
        assert board("browser_scroll", dy=4000)["ok"]
    assert board("browser_wait_for", text="Drone Pilot 13")["ok"]
    page = board.page()
    # A posting in the same tab, and Back to the list, Forward to it again.
    opened = board("browser_click", ref=named(page, "link", "Robot Engineer 3"))
    assert opened["ok"], opened
    assert opened["url"].endswith("/jobs/3"), opened
    back = board("browser_navigate_back")
    assert back["ok"], back
    assert back["url"] == BOARD, back
    assert board("browser_wait_for", text="Robot Engineer 3")["ok"]
    assert 'link "Robot Engineer 3"' in board.page()
    forward = board("browser_navigate_forward")
    assert forward["ok"], forward
    assert forward["url"].endswith("/jobs/3"), forward
    assert board("browser_navigate_back")["ok"]
    assert board("browser_wait_for", text="Robot Engineer 3")["ok"]
    # A posting in a second tab: listed, selected, read, closed; at most three tabs.
    page = board.page()
    assert board("browser_click", ref=ref(page, "link", "in a new tab"))["ok"]
    assert board("browser_tabs", action="select", index=1)["ok"]
    assert board("browser_wait_for", text="Employer: Example Robotics")["ok"]
    assert 'heading "Robot Engineer 7"' in board.page()
    tabs = board("browser_tabs", action="list")["tabs"]
    assert [t["url"].endswith("/jobs/7") for t in tabs] == [False, True], tabs
    assert [t["current"] for t in tabs] == [False, True], tabs
    assert board("browser_tabs", action="new")["ok"]
    full = board("browser_tabs", action="new")
    assert full["error"] == "too_many_tabs", full
    assert len(board("browser_tabs", action="close", index=2)["tabs"]) == 2
    assert board("browser_tabs", action="select", index=0)["ok"]
    # The first posting is on an ATS host: any public host opens.
    page = board.page()
    ats = board("browser_click", ref=named(page, "link", "Robot Engineer"))
    assert ats["ok"], ats
    assert ats["url"] == ATS, ats
    shot = board("browser_take_screenshot")
    assert shot["ok"], shot.get("error")
    assert shot["type"] == "png", shot.get("error")
    assert len(shot["data"]) > 1000, shot.get("error")
    log = seen(b20)
    assert {"method": "POST", "host": "www.b20-board.test", "path": "/consent"} in [
        {k: s[k] for k in ("method", "host", "path")} for s in log
    ]
    assert any(s["path"] == "/api/jobs?page=2" for s in log)  # loaded on scroll


def test_a_search_on_another_host_of_the_company_by_its_get_and_post_forms(b20, search):
    careers = search(CAREERS)
    page = careers.page()
    moved = careers("browser_click", ref=ref(page, "link", "Search our jobs"))
    assert moved["ok"], moved
    assert moved["url"].startswith("http://search.b20-robotics.test"), moved
    page = careers.page()
    typed = careers(
        "browser_type", ref=ref(page, "searchbox", "Keywords"), text="robot", submit=True
    )
    assert typed["ok"], typed
    page = careers.page()
    assert 'link "Robot Engineer"' in page, page
    assert careers("browser_navigate_back")["ok"]
    page = careers.page()
    box = ref(page, "textbox", "Advanced keywords")
    assert careers("browser_type", ref=box, text="robot")["ok"]
    found = careers("browser_click", ref=ref(page, "button", "Find"))
    assert found["ok"], found  # a search form posted to /search
    assert 'link "Robot Engineer"' in careers.page()
    paths = [(s["method"], s["path"]) for s in seen(b20) if s["host"] == "search.b20-robotics.test"]
    assert ("GET", "/results?q=robot") in paths
    assert ("POST", "/search") in paths


# The agent's own scripts on the ATS posting, and what each did (`browser_evaluate`).
APPLY = "document.querySelector('#apply')"
FORM = "document.querySelector('#application')"
REFUSED_SCRIPTS = {
    "click": f"() => {APPLY}.click()",
    "submit": f"() => {FORM}.submit()",
    "requestSubmit": f"() => {FORM}.requestSubmit()",
    "requestSubmit_with": f"() => {FORM}.requestSubmit({APPLY})",
    "easy_click": "() => document.querySelector('#easy').click()",
    "message_click": "() => document.querySelector('#message').click()",
    "made_form": (
        "() => { const f = document.createElement('form'); f.method = 'post';"
        " f.action = '/example-robotics/jobs/42'; document.body.append(f); f.submit(); }"
    ),
}
FETCH = (
    "() => fetch({path!r}, {{method: 'POST', body: 'x'}})"
    ".then(r => 'sent ' + r.status, e => 'refused ' + e.name)"
)


def test_the_line_on_real_chromium_every_path_on_an_ats_posting(b20, search):
    before = len(seen(b20))
    ats = search(ATS)
    page = ats.page()
    for name in ("Apply for this job", "Easy Apply", "Send message"):
        refused = ats("browser_click", ref=ref(page, "button", name))
        assert refused["error"] in ("submit", "line"), (name, refused)
    entered = ats("browser_type", ref=ref(page, "textbox", "Full name"), text="x", submit=True)
    assert entered["error"] == "submit", entered  # Enter in the application form
    for name, said in (("Save job", "Saved"), ("Follow", "Following")):
        assert ats("browser_click", ref=ref(page, "button", name))["ok"], name
        assert ats("browser_wait_for", text=said)["ok"], name
    # The agent's own script: refused where code can see it.
    for what, script in REFUSED_SCRIPTS.items():
        answer = ats.evaluate(script)
        assert answer["error"] == "line", (what, answer)
    for path in ("/api/messages", "/api/easyapply", "/example-robotics/jobs/42/apply"):
        answer = ats.evaluate(FETCH.format(path=path))
        assert answer["ok"], (path, answer)
        assert json.loads(answer["result"]) == "refused TypeError", (path, answer)
    saved = ats.evaluate(FETCH.format(path="/api/saved-jobs"))
    assert json.loads(saved["result"]) == "sent 200", saved
    assert json.loads(ats.evaluate("() => document.title")["result"]).startswith("Robot")
    long = ats.evaluate("() => 'y'.repeat(50000)")
    assert long["ok"], long.get("error")
    assert long["cut"], long.get("error")
    assert len(long["result"]) == 20_000, long.get("error")
    log = seen(b20)[before:]
    posted = [s["path"] for s in log if s["method"] == "POST" and s["host"] == "ats.b20-hire.test"]
    assert posted == ["/api/saved-jobs", "/api/follows", "/api/saved-jobs"], posted


# Every name the control tables refuse or pass (test_b20_line), as a button or a link the
# agent's script makes and clicks: the script guard judges them as a click does (review of B20,
# round 1: it read no phrase, so "Sign out" and "Candidature simplifiée" passed a script).
CLICKED = """rows => rows.filter(([tag, name]) => {
  const made = document.createElement(tag);
  if (tag === "a") made.href = "#made"; else made.type = "button";
  made.textContent = name;
  document.body.append(made);
  try { made.click(); return false; } catch (e) { return true; } finally { made.remove(); }
}).map(([tag, name]) => tag + ":" + name)"""
# A form with no method attribute (a React application form: `form.method` is "get") that holds
# a field a person writes to someone or applies with: submit(), requestSubmit() and its button's
# click() are refused, as search_form refuses its submission; a GET search form's pass.
FORMS = """() => {
  const sink = document.createElement("iframe");
  sink.name = "sink";
  document.body.append(sink);
  const fields = {email: '<input type="email" name="e">', tel: '<input type="tel" name="t">',
    file: '<input type="file" name="f">', password: '<input type="password" name="p">',
    textarea: '<textarea name="m"></textarea>', editable: '<div contenteditable="true"></div>',
    search: '<input type="search" name="q" value="robot">'};
  const ways = {submit: f => f.submit(), requestSubmit: f => f.requestSubmit(),
    requestSubmit_with: f => f.requestSubmit(f.querySelector("button")),
    click: f => f.querySelector("button").click()};
  const refused = [];
  for (const [kind, html] of Object.entries(fields)) {
    for (const [way, act] of Object.entries(ways)) {
      const form = document.createElement("form");
      form.action = "/results";
      form.target = "sink";
      form.innerHTML = html + "<button>Go</button>";
      document.body.append(form);
      try { act(form); } catch (e) { refused.push(kind + ":" + way); }
      form.remove();
    }
  }
  return refused;
}"""


def test_a_script_s_click_refuses_what_a_click_refuses_on_real_chromium(b20, search):
    from tests.browser.test_b20_line import LEAVING_LINKS, PASSED, POSTING_LINKS, REFUSED

    ats = search(ATS)
    rows = [
        *(["button", n] for n in [*REFUSED, *PASSED]),
        *(["a", n] for n in [*LEAVING_LINKS, *POSTING_LINKS]),
    ]
    answer = ats.evaluate(f"() => ({CLICKED})({json.dumps(rows)})")
    assert answer["ok"], answer
    refused = json.loads(answer["result"])
    assert refused == [*(f"button:{n}" for n in REFUSED), *(f"a:{n}" for n in LEAVING_LINKS)]
    forms = ats.evaluate(FORMS)
    assert forms["ok"], forms
    expected = [
        f"{kind}:{way}"
        for kind in ("email", "tel", "file", "password", "textarea", "editable")
        for way in ("submit", "requestSubmit", "requestSubmit_with", "click")
    ]
    assert json.loads(forms["result"]) == expected  # the search form's four ways passed
    # Uncaught, a refusal is the tool's answer, counted by the api as the line's.
    made = "() => { const b = document.createElement('button'); b.textContent = 'Log out';"
    assert ats.evaluate(made + " document.body.append(b); b.click(); }")["error"] == "line"


FETCH_GET = "() => fetch({path!r}).then(r => 'sent ' + r.status, e => 'refused ' + e.name)"


def test_signing_out_never_happens_on_real_chromium_whatever_opens_it(b20, search):
    """LinkedIn's "Sign Out" is a link to /m/logout/ and Indeed's a link too (review of B20,
    round 1): a click on a sign out link, Enter on it focused, a typed address and a script's GET
    are refused in English, French and German; a posting link still opens."""
    before = len(seen(b20))
    ats = search(ATS)
    page = ats.page()
    for name in ("Sign Out", "Se déconnecter", "Abmelden"):
        refused = ats("browser_click", ref=named(page, "link", name))
        assert refused["error"] == "line", (name, refused)
    assert ats.evaluate("() => document.querySelector('#signout').focus()")["ok"]
    entered = ats("browser_press_key", key="Enter")
    assert entered["error"] == "line", entered
    for path in ("/m/logout/", "/auth/logout?continue=x", "/fr/deconnexion"):
        typed = ats("browser_navigate", url="http://ats.b20-hire.test:8080" + path)
        assert typed["error"] == "line", (path, typed)
        answer = ats.evaluate(FETCH_GET.format(path=path))
        assert json.loads(answer["result"]) == "refused TypeError", (path, answer)
    assert 'heading "Robot Engineer"' in ats.page()
    opened = ats("browser_click", ref=named(ats.page(), "link", "Logistics Engineer"))
    assert opened["ok"], opened
    assert opened["url"].endswith("/jobs/7"), opened
    paths = [s["path"] for s in seen(b20)[before:] if s["host"] == "ats.b20-hire.test"]
    assert "/jobs/7" in paths
    assert not [p for p in paths if "logout" in p or "deconnexion" in p or "abmelden" in p], paths


# What code cannot see inside a script, each run as the agent's script: the request floor, not
# the script guard, is what holds what it sends.
UNSEEN = {
    # The original submit of another realm (a new frame's prototype), never patched.
    "other_realm_submit": (
        "() => { const f = document.createElement('iframe'); document.body.append(f);"
        f" f.contentWindow.HTMLFormElement.prototype.submit.call({FORM}); return 'called'; }}"
    ),
    # A click the script builds and dispatches itself: the submit button's activation.
    "dispatched_click": (
        f"() => {{ {APPLY}.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));"
        " return 'dispatched'; }"
    ),
    # A worker the script starts from a blob: its own fetch POST.
    "worker_fetch": """() => new Promise(done => {
  const src = "fetch('http://ats.b20-hire.test:8080/api/messages?from=worker', " +
    "{method: 'POST', body: 'x'}).then(r => postMessage('sent ' + r.status), " +
    "e => postMessage('refused ' + e.name));";
  const worker = new Worker(URL.createObjectURL(new Blob([src])));
  worker.onmessage = m => done(m.data);
  setTimeout(() => done('no answer'), 3000);
})""",
    # A socket: its handshake is a GET, what it then sends is never seen.
    "socket": (
        "() => new Promise(done => { const s = new WebSocket('ws://ats.b20-hire.test:8080/b20-ws');"
        " s.onerror = () => done('error'); s.onopen = () => done('open');"
        " setTimeout(() => done('no answer'), 3000); })"
    ),
    # A beacon to a message address.
    "beacon": "() => navigator.sendBeacon('/api/messages?from=beacon', 'x')",
}


def test_what_code_cannot_see_inside_a_script_and_the_floor_that_holds(b20, search):
    before = len(seen(b20))
    ats = search(ATS)
    answers = {}
    for what, script in UNSEEN.items():
        answers[what] = ats.evaluate(script)
        # A refused form's POST leaves the tab where it was (never Chromium's error page).
        assert 'heading "Robot Engineer"' in ats.page(), (what, answers[what])
    log = seen(b20)[before:]
    ats_log = [s for s in log if s["host"] == "ats.b20-hire.test"]
    posted = [s["path"] for s in ats_log if s["method"] == "POST"]
    # Measured on Chrome for Testing 153 with Playwright 1.55.0 (part B of B20, 2026-11-04):
    print(json.dumps({"answers": answers, "posted": posted}, indent=1))
    # The form's POST to the posting's own address never reached the ATS, by either way past
    # the script guard: the request floor refused it as a document POST that is no search.
    assert "/example-robotics/jobs/42" not in posted
    assert answers["other_realm_submit"]["error"] == "line", answers["other_realm_submit"]
    assert answers["dispatched_click"]["error"] == "line", answers["dispatched_click"]
    # A worker's fetch POST to a message address: the context's route sees a dedicated worker's
    # requests in this Playwright, so it never left.
    assert "/api/messages?from=worker" not in posted, answers["worker_fetch"]
    assert "/api/messages?from=beacon" not in posted
    # The socket's handshake reached the host (a GET): what a socket sends is not seen (declared).
    sockets = [s for s in ats_log if s["path"] == "/b20-ws"]
    assert [s["upgrade"].lower() for s in sockets] == ["websocket"], answers["socket"]


def test_the_jungle_s_algolia_search_leaves_now_anonymous(b20, search):
    """B19's stand in for Welcome to the Jungle: its search service's client says "connect" in
    `x-algolia-agent` and "application" in `x-algolia-application-id`; read as metadata, never
    as a step of the line, the search runs and lists the posting."""
    jungle = search(JUNGLE)
    page = jungle.page()
    box = ref(page, "combobox", "Search for a job")
    assert jungle("browser_type", ref=box, text="GenAI Engineer", submit=True)["ok"]
    assert jungle("browser_wait_for", text="GenAI Engineer")["ok"]
    assert 'link "GenAI Engineer"' in jungle.page()
    refused = jungle("browser_click", ref=ref(page, "button", "Apply"))
    assert refused["error"] in ("submit", "line"), refused
    posts = [
        (s["host"], s["path"].split("?")[0]) for s in seen(b20, "b19") if s["method"] == "POST"
    ]
    assert posts == [("abc-dsn.algolia.test", "/1/indexes/*/queries")]
