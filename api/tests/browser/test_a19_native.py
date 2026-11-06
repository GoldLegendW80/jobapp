"""A19 on real Chromium in the browser container: One click Apply's lease (purpose `apply_agent`)
driven by a scripted agent the way the apply agent drives it, on an invented company's job site
(tests/ats_forms/a19/, served by the fake ATS forms server, tests/ats_forms_stub.py, under
invented `.test` hosts and under `atsforms`, the name the apply journeys' stack gives it).

- The posting's Apply link, a two step form (Next, a button of the page's script, then the final
  Submit application), the CV uploaded from the documents placed on the lease, a free text
  answer: the agent's click on Submit leaves only with the lease's capability, and the fake site
  records exactly one submission with what was typed and the PDF's bytes.
- The emergency stop: the api sends the capability only while the stop is off; without it the
  click on Submit, Enter in a field and a script are refused `submit_permit`, and nothing leaves.
- A password box and a one time code box are never typed into, capability or not.
- A sweep lease on the same browserd (B20's line) still refuses the same Submit.
- The recorded stream of the apply journey (app/agents/fake_streams/apply_agent.json) replayed
  call by call, as the apply agent's session sends it: its refs name the elements it means.

The server's log is GET /_stub/submissions. Needs an existing BROWSER_TEST_IMAGE; never builds
or pulls one.
"""

import hashlib
import json
import os
import uuid
from typing import Any

import pytest

from tests.browser.test_b5_container import Lease, ref
from tests.browser.test_browser_container import ROOT, Stack

pytestmark = pytest.mark.docker
NAMES = ["careers.a19-robotics.test", "jobs.a19-hire.test"]
POSTING = "http://careers.a19-robotics.test:8080/forms/a19/posting.html"
FORM = "http://careers.a19-robotics.test:8080/forms/a19/apply.html"
ACCOUNT = "http://jobs.a19-hire.test:8080/forms/a19/account.html"
CONFIRMATION = "http://careers.a19-robotics.test:8080/forms/a19/confirmation.html"
WHY = (
    "I have built robot arms for warehouses for five years, and Example Robotics ships the arms "
    "I would want to work on: ROS 2, C++ and field trials in Bordeaux."
)


def pdf() -> str:
    """A small real PDF of one page (offsets right), as text: the lease's PUT sends a string."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
    ]
    out, offsets = "%PDF-1.7\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n"
    return out


CV = pdf()
STUB = ["python", "-I", "/fake/ats_forms_stub.py"]


@pytest.fixture(scope="module")
def a19(tmp_path_factory):
    image = os.environ.get("BROWSER_TEST_IMAGE")
    assert image, "Provide an existing BROWSER_TEST_IMAGE, this test never builds or pulls."
    override = tmp_path_factory.mktemp("a19") / "a19.yml"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "browser": {
                        "pull_policy": "never",
                        "volumes": [f"{ROOT}/browser/browserd:/opt/browserd/browserd:ro"],
                        "environment": {
                            "BROWSER_PROXY_ALLOW_PRIVATE": ",".join(["pages", "atsforms", *NAMES])
                        },
                    },
                    "client": {"pull_policy": "never"},
                    "pages": {"pull_policy": "never"},
                    # The fake ATS forms server, as tests/ats-forms-stub.compose.yml runs it.
                    "atsforms": {
                        "image": image,
                        "pull_policy": "never",
                        "user": "1000:1000",
                        # The container's address, on the egress network the browser reaches.
                        "command": [*STUB, "--host", "0.0.0.0", "--port", "8080"],  # noqa: S104
                        "read_only": True,
                        "cap_drop": ["ALL"],
                        "volumes": [f"{ROOT}/tests:/fake:ro"],
                        "networks": {"egress": {"aliases": ["atsforms", *NAMES]}},
                    },
                }
            }
        )
    )
    stack = Stack("a19-native-" + uuid.uuid4().hex[:8], image)
    stack.redactions = [stack.secret]
    stack.compose[8:8] = ["-f", str(override)]
    try:
        stack.run("up", "-d", "--wait", "--wait-timeout", "120", "--no-build", "--pull", "never")
        yield stack
    finally:
        stack.run("down", "-v", "--remove-orphans", "--timeout", "0", check=False)


def fetched(stack: Stack, url: str, method: str = "GET") -> Any:
    """`url` of the fake site as the browser container reads it with plain Python."""
    code = (
        "import sys, urllib.request; request = urllib.request.Request(sys.argv[1], method="
        "sys.argv[2], data=b'' if sys.argv[2] == 'POST' else None);"
        " print(urllib.request.urlopen(request).read().decode())"
    )
    return json.loads(stack.execute("browser", "python", "-I", "-c", code, url, method))


def sent(stack: Stack, name: str = "a19") -> list[dict]:
    return [s for s in fetched(stack, "http://atsforms:8080/_stub/submissions") if s["org"] == name]


@pytest.fixture
def agent(a19):
    fetched(a19, "http://atsforms:8080/_stub/reset", "POST")
    lease = Lease(a19, "apply_agent")  # the lease's hosts are ["pages"]: no host list applies
    a19.redactions.append(lease.token)
    assert lease.capability, "the agent's lease has the capability, held by the api"
    a19.redactions.append(lease.capability)
    yield lease
    lease.end()


def act(lease: Lease, name: str, capability: str | None = None, **args: Any) -> dict:
    """One act and the snapshot after it, as the apply agent's session takes it."""
    done = lease.tool(name, capability, **args)
    if done.get("ok"):
        done["page"] = lease.snapshot()
    return done


def test_the_agent_applies_on_a_two_step_form_and_clicks_submit_itself(a19, agent):
    assert agent.put("cv.pdf", CV)["status"] == 200
    opened = act(agent, "browser_navigate", url=POSTING)
    assert opened["ok"], opened  # a host off the lease's list: any public address opens
    page = opened["page"]
    print("POSTING\n" + page)
    applied = act(agent, "browser_click", ref=ref(page, "link", "Apply for this job"))
    assert applied["ok"], applied  # a link, not a submit: no capability needed
    assert "submit_clicked" not in applied, applied
    page = applied["page"]
    print("STEP 1\n" + page)
    for label, text in (
        ("First name", "Adaline"),
        ("Last name", "Example"),
        ("Email", "e2e-a19@example.org"),
        ("Phone", "+33 5 00 00 00 19"),
    ):
        typed = act(agent, "browser_type", ref=ref(page, "textbox", label), text=text)
        assert typed["ok"], (label, typed)
        page = typed["page"]
    uploaded = act(
        agent, "browser_file_upload", ref=ref(page, "button", "Resume"), paths=["cv.pdf"]
    )
    assert uploaded["ok"], uploaded
    assert uploaded["uploaded"] == ["cv.pdf"], uploaded
    moved = act(agent, "browser_click", ref=ref(uploaded["page"], "button", "Next"))
    assert moved["ok"], moved  # the page's own button, not a submit of the form
    assert "submit_clicked" not in moved, moved
    page = moved["page"]
    print("STEP 2\n" + page)
    assert "Step 2 of 2" in page, page
    why = act(agent, "browser_type", ref=ref(page, "textbox", "Why do you want"), text=WHY)
    assert why["ok"], why
    notice = ref(why["page"], "textbox", "notice period")
    assert act(agent, "browser_type", ref=notice, text="Six weeks")["ok"]
    page = agent.snapshot()
    submit = ref(page, "button", "Submit application")

    # The emergency stop on: the api sends no capability, and nothing of the form leaves.
    refused = agent.tool("browser_click", ref=submit)
    assert refused["error"] == "submit_permit", refused
    entered = agent.tool("browser_press_key", ref=notice, key="Enter")
    assert entered["error"] == "submit_permit", entered
    typed = agent.tool("browser_type", ref=notice, text=" ", submit=True)
    assert typed["error"] == "submit_permit", typed
    script = agent.tool(
        "browser_evaluate", function="() => document.querySelector('form').requestSubmit()"
    )
    assert script["error"] == "submit_permit", script
    assert sent(a19) == []

    # The stop off: the api sends the act again with the capability; the agent's own click.
    clicked = agent.tool(
        "browser_click", agent.capability, ref=ref(agent.snapshot(), "button", "Submit application")
    )
    assert clicked["ok"], clicked
    assert clicked["submit_clicked"] is True, clicked
    assert agent.tool("browser_wait_for", text="Thank you for applying")["ok"]
    confirmed = agent.tool("browser_snapshot")
    assert confirmed["url"] == CONFIRMATION, confirmed.get("url")
    [one] = sent(a19)
    assert one["fields"] == {
        "first_name": "Adaline",
        "last_name": "Example",
        "email": "e2e-a19@example.org",
        "phone": "+33 5 00 00 00 19",
        "motivation": WHY,
        "notice": "Six weeks",
    }, one["fields"]
    [file] = one["files"]
    assert file["field"] == "resume", file
    assert file["filename"] == "cv.pdf", file
    assert file["sha256"] == hashlib.sha256(CV.encode()).hexdigest(), file


def test_a_password_box_and_a_one_time_code_box_are_never_typed_into(a19, agent):
    opened = act(agent, "browser_navigate", url=ACCOUNT)
    assert opened["ok"], opened
    page = opened["page"]
    print("ACCOUNT\n" + page)
    assert act(
        agent, "browser_type", ref=ref(page, "textbox", "Email"), text="e2e-a19@example.org"
    )["ok"]
    page = agent.snapshot()
    # A box whose facts a page script breaks ("Your PIN") is refused too (round 1 of the review).
    for label in ("Password", "Code sent to your email", "Your PIN"):
        box = ref(page, "textbox", label)
        for capability in (None, agent.capability):
            typed = agent.tool("browser_type", capability, ref=box, text="Invented42!")
            assert typed["error"] == "credential", (label, capability is None, typed)
    values = agent.tool(
        "browser_evaluate",
        agent.capability,
        function="() => [password.value, code.value, pin.value, email.value]",
    )
    assert json.loads(values["result"]) == ["", "", "", "e2e-a19@example.org"], values
    assert sent(a19, "a19-account") == []


def test_a_sweep_lease_on_the_same_browserd_still_refuses_the_submit(a19):
    fetched(a19, "http://atsforms:8080/_stub/reset", "POST")
    sweep = Lease(a19, "sweep", hosts=["a19-robotics.test"])
    a19.redactions.append(sweep.token)
    assert sweep.capability is None
    marker = {"_discovery": "native_get", "_discovery_scope": {"start": FORM}}
    try:
        assert sweep.tool("browser_navigate", url=FORM, **marker)["ok"]
        page = sweep.tool("browser_snapshot", **marker)["snapshot"]
        moved = sweep.tool("browser_click", ref=ref(page, "button", "Next"), **marker)
        print("SWEEP NEXT", moved)
        shown = sweep.tool(
            "browser_evaluate",
            function="() => { step1.hidden = true; step2.hidden = false; return 'shown'; }",
            **marker,
        )
        assert shown["ok"], shown
        page = sweep.tool("browser_snapshot", **marker)["snapshot"]
        refused = sweep.tool(
            "browser_click", ref=ref(page, "button", "Submit application"), **marker
        )
        assert refused["error"] in ("submit", "line"), refused
        script = sweep.tool(
            "browser_evaluate",
            function="() => document.querySelector('form').requestSubmit()",
            **marker,
        )
        assert script["error"] == "line", script
        assert sent(a19) == []
    finally:
        sweep.end()


def calls_of(prompt: str) -> list[tuple[str, dict]]:
    """The tool calls the recorded stream makes for a run of this first message."""
    from app.agents import fake_backend

    made = []
    for step in fake_backend.load_script("apply_agent"):
        if "message" not in step or not fake_backend.kept(step, prompt):
            continue
        for block in step["message"].get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                made.append((block["name"].removeprefix("mcp__app__"), block["input"]))
    return made


def replay(
    lease: Lease, calls: list[tuple[str, dict]], review: bool = False
) -> tuple[str, dict | None]:
    """The calls as the apply agent's session sends them (applier.ApplySession): an act refused
    `submit_permit` again with the capability, a snapshot after each act; the ask and the report
    end the replay. Returns the last page's address and the report or the ask."""
    url = ""
    for name, args in calls:
        if name in ("browser_ask_person", "apply_done", "apply_ready"):
            return url, {"tool": name, **args}
        done = lease.tool(name, **args)
        if done.get("error") == "submit_permit" and not review:
            done = lease.tool(name, lease.capability, **args)
        assert done.get("ok"), (name, args, done)
        if name != "browser_wait_for":
            url = lease.tool("browser_snapshot")["url"]
    return url, None


def test_the_journey_s_recorded_stream_on_real_chromium(a19, agent):
    """The apply journeys' stream (apply_agent.json) on the same pages under `atsforms`: a run
    with nothing saved never asks (A20): it types its own notice period, clicks Submit itself
    and reports it as chosen; a run with the saved answer types it; one submission each."""
    assert agent.put("cv.pdf", CV)["status"] == 200
    url, report = replay(agent, calls_of('{"answers_to_your_questions": [], "saved_answers": {}}'))
    assert report is not None, url
    assert report["tool"] == "apply_done", report
    assert report["outcome"] == "submitted", report
    assert report["chosen"][0]["field"] == "What is your notice period?", report
    assert report["confirmation_url"] == url == "http://atsforms:8080/forms/a19/confirmation.html"
    [one] = sent(a19)
    assert one["fields"]["notice"] == "One month", one["fields"]
    url, report = replay(agent, calls_of('{"saved_answers": {"notice": "Six weeks"}}'))
    assert report is not None, report
    assert report["outcome"] == "submitted", report
    assert [s["fields"]["notice"] for s in sent(a19)] == ["One month", "Six weeks"]
    assert [f["sha256"] for f in sent(a19)[0]["files"]] == [hashlib.sha256(CV.encode()).hexdigest()]


def test_the_review_journey_s_recorded_stream_on_the_review_lease(a19, review):
    """The Review first journey's runs of the same stream on an `apply_review` lease: the fill
    ends with apply_ready naming Submit application, nothing is sent; the gate's click sends
    once; the reading run reports the confirmation it read."""
    assert review.put("cv.pdf", CV)["status"] == 200
    filled = '{"review_first": true, "saved_answers": {"notice": "Six weeks"}}'
    url, ready = replay(review, calls_of(filled), review=True)
    assert ready is not None, ready
    assert ready["tool"] == "apply_ready", ready
    page = review.snapshot()
    assert f'button "Submit application" [ref={ready["submit_ref"]}]' in page, page
    assert sent(a19) == []
    clicked = review.tool(
        "browser_click", review.capability, ref=ready["submit_ref"], element="Submit application"
    )
    assert clicked["ok"], clicked
    url, report = replay(review, calls_of('{"gate_clicked": true, "review_first": true}'), True)
    assert report is not None, report
    assert report["outcome"] == "submitted", report
    assert report["confirmation_url"] == url == "http://atsforms:8080/forms/a19/confirmation.html"
    [one] = sent(a19)
    assert one["fields"]["notice"] == "Six weeks", one["fields"]


# --- A20: the resume widgets, and Review first's lease (purpose `apply_review`) ----------------

WIDGETS = "http://careers.a19-robotics.test:8080/forms/a20/widgets.html"
REVIEW = "http://careers.a19-robotics.test:8080/forms/a20/review.html"
LETTER = pdf().replace("595 842", "612 792")  # another real PDF: its own bytes


def test_the_cv_lands_in_every_resume_widget(a19, agent):
    """A20 point 3: a file input hidden behind a styled button, a drop zone that opens the file
    chooser, and a "Choose one" list of files with an option that reveals a new upload."""
    assert agent.put("cv.pdf", CV)["status"] == 200
    assert agent.put("letter.pdf", LETTER)["status"] == 200
    opened = act(agent, "browser_navigate", url=WIDGETS)
    assert opened["ok"], opened
    page = opened["page"]
    print("WIDGETS\n" + page)
    typed = act(
        agent, "browser_type", ref=ref(page, "textbox", "Email"), text="e2e-a20@example.org"
    )
    assert typed["ok"], typed
    behind = act(
        agent, "browser_file_upload", ref=ref(page, "button", "Upload your CV"), paths=["cv.pdf"]
    )
    assert behind["ok"], behind
    zone = act(
        agent, "browser_file_upload", ref=ref(page, "button", "drop a file here"),
        paths=["letter.pdf"],
    )  # fmt: skip
    assert zone["ok"], zone
    chosen = act(
        agent, "browser_select_option", ref=ref(page, "combobox", "Resume"),
        values=["Upload a new file"],
    )  # fmt: skip
    assert chosen["ok"], chosen
    page = chosen["page"]
    print("NEW UPLOAD\n" + page)
    fresh = act(
        agent, "browser_file_upload", ref=ref(page, "button", "New resume"), paths=["cv.pdf"]
    )
    assert fresh["ok"], fresh
    send = ref(fresh["page"], "button", "Submit application")
    assert agent.tool("browser_click", ref=send)["error"] == "submit_permit"
    clicked = agent.tool("browser_click", agent.capability, ref=send)
    assert clicked["ok"], clicked
    assert agent.tool("browser_wait_for", text="Thank you for applying")["ok"]
    [one] = sent(a19, "a20-widgets")
    assert one["fields"]["existing_resume"] == "new", one["fields"]
    files = {f["field"]: (f["filename"], f["sha256"]) for f in one["files"]}
    cv, letter = (
        hashlib.sha256(CV.encode()).hexdigest(),
        hashlib.sha256(LETTER.encode()).hexdigest(),
    )
    assert files == {
        "cv_button": ("cv.pdf", cv),
        "letter_drop": ("letter.pdf", letter),
        "resume_new": ("cv.pdf", cv),
    }, files


@pytest.fixture
def review(a19):
    fetched(a19, "http://atsforms:8080/_stub/reset", "POST")
    lease = Lease(a19, "apply_review")
    a19.redactions.append(lease.token)
    assert lease.capability, "the review lease's capability, held by the api for the gate"
    a19.redactions.append(lease.capability)
    yield lease
    lease.end()


def test_on_the_review_lease_the_agent_never_sends_and_the_gate_s_click_sends_once(a19, review):
    """A20 point 4: the agent fills both steps (the step's Next is a submit button and passes),
    every act of its own on the final send is refused, a call with the capability is a click
    only, and the gate's one click after the person's Confirm sends one application."""
    assert review.put("cv.pdf", CV)["status"] == 200
    opened = act(review, "browser_navigate", url=REVIEW)
    assert opened["ok"], opened
    page = opened["page"]
    print("REVIEW STEP 1\n" + page)
    for label, text in (("Full name", "Adaline Example"), ("Email", "e2e-a20@example.org")):
        typed = act(review, "browser_type", ref=ref(page, "textbox", label), text=text)
        assert typed["ok"], (label, typed)
        page = typed["page"]
    uploaded = act(
        review, "browser_file_upload", ref=ref(page, "button", "Resume"), paths=["cv.pdf"]
    )
    assert uploaded["ok"], uploaded
    moved = act(review, "browser_click", ref=ref(uploaded["page"], "button", "Next"))
    assert moved["ok"], moved  # a submit button of the step, named Next: a wizard's step
    page = moved["page"]
    print("REVIEW STEP 2\n" + page)
    notice = ref(page, "textbox", "notice period")
    assert act(review, "browser_type", ref=notice, text="One month")["ok"]
    page = review.snapshot()
    send = ref(page, "button", "Submit application")
    # The agent's own acts on the send: a click, Enter in a field, a script, all refused.
    assert review.tool("browser_click", ref=send)["error"] == "submit_permit"
    assert review.tool("browser_press_key", ref=notice, key="Enter")["error"] == "submit_permit"
    script = review.tool(
        "browser_evaluate", function="() => document.querySelector('form').requestSubmit()"
    )
    assert script["error"] == "submit_permit", script
    # With the capability, only the gate's click passes on this lease (403 for any other act).
    for name, args in (
        ("browser_press_key", {"ref": notice, "key": "Enter"}),
        ("browser_type", {"ref": notice, "text": " ", "submit": True}),
        ("browser_evaluate", {"function": "() => 1"}),
    ):
        answer = review.raw(
            "POST", f"/leases/{review.id}/tools/{name}", json.dumps(args),
            **{"Content-Type": "application/json", "X-Gate-Capability": review.capability},
        )  # fmt: skip
        assert answer["status"] == 403, (name, answer)
    assert sent(a19, "a20-review") == []
    # The person's Confirm: the gate clicks the control the agent named, once.
    clicked = review.tool(
        "browser_click", review.capability, ref=send, element="Submit application"
    )
    assert clicked["ok"], clicked
    assert review.tool("browser_wait_for", text="Thank you for applying")["ok"]
    [one] = sent(a19, "a20-review")
    assert one["fields"] == {
        "full_name": "Adaline Example",
        "email": "e2e-a20@example.org",
        "notice": "One month",
    }, one["fields"]
    assert [f["filename"] for f in one["files"]] == ["cv.pdf"]
