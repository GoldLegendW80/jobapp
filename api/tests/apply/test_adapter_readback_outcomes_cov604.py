"""An adapter reports failure when the live page refuses or loses the field it just filled."""

import asyncio

import pytest

from app.apply.answers import Answer
from app.apply.ats import base
from tests.apply.a4_support import FakeForm, Spec


class OneField(base.Adapter):
    submit_names = frozenset({"submit application"})


KINDS = [
    ("text", "type", "Alex", "profile.identity.name"),
    ("select", "select_option", "France", "profile.identity.country"),
    ("yesno", "click", "Yes", "profile.answers.authorised"),
    ("checkbox", "check", True, "person"),
    ("file", "upload", "cv.pdf", "documents.cv"),
]


async def fill_one(adapter, form, answer):
    found, code = await adapter.read(form)
    assert found is not None
    assert not code
    assert len(found.fields) == 1
    return await adapter.fill(form, found.fields[0], answer)


@pytest.mark.parametrize(("kind", "action", "value", "source"), KINDS)
@pytest.mark.parametrize("failure", ["refused", "disappeared", "captcha", "unreadable"])
def test_field_failure_stops_at_the_live_boundary(
    monkeypatch, kind, action, value, source, failure
):
    form = FakeForm([Spec("Question", kind, options=("France", "Germany"))])
    original = getattr(form, action)

    async def disturbed(*args, **kwargs):
        if failure == "refused":
            return {"ok": False, "error": "host_not_allowed"}
        result = await original(*args, **kwargs)
        if failure == "disappeared":
            form.fields.clear()
        elif failure == "captcha":
            form.captcha = "instead"
        else:

            async def unavailable():
                return {"ok": False, "error": "unreadable"}

            monkeypatch.setattr(form, "snapshot", unavailable)
        return result

    monkeypatch.setattr(form, action, disturbed)
    result = asyncio.run(fill_one(OneField(), form, Answer(value, source)))
    expected = {
        "refused": "host_not_allowed",
        "disappeared": "form_changed",
        "captcha": "captcha",
        "unreadable": "unreadable",
    }[failure]
    assert (result.status, result.code) == ("failed", expected)
    assert form.submit_clicks == 0
    if failure == "refused":
        assert form.fields[0].value == ""
        assert not form.fields[0].checked


@pytest.mark.parametrize(("kind", "action", "value", "source"), KINDS)
def test_successful_tool_call_without_matching_readback_is_unverified(
    monkeypatch, kind, action, value, source
):
    spec = Spec("Question", kind, options=("France", "Germany"))
    form = FakeForm([spec])
    original = getattr(form, action)

    async def ignored(*args, **kwargs):
        result = await original(*args, **kwargs)
        spec.value = "No" if kind == "yesno" else "Different value"
        spec.checked = False
        return result

    monkeypatch.setattr(form, action, ignored)
    result = asyncio.run(fill_one(OneField(), form, Answer(value, source)))
    expected = {"checkbox": "not_checked", "file": "no_file"}.get(kind, "value_differs")
    assert (result.status, result.code) == ("unverified", expected)
    assert form.submit_clicks == 0


@pytest.mark.parametrize(
    ("spec", "answer", "expected"),
    [
        (Spec("Name", "text"), Answer("", "person"), "empty_answer"),
        (Spec("City", "react"), Answer("", "person"), "empty_answer"),
        (
            Spec("Country", "select", options=("France",)),
            Answer("Germany", "person"),
            "option_missing",
        ),
        (Spec("Allowed?", "yesno"), Answer("Maybe", "person"), "option_missing"),
        (Spec("Resume", "file"), Answer("cv.pdf", "person"), "not_a_document"),
        (Spec("Resume", "file"), Answer(("cv.pdf",), "documents.cv"), "not_a_document"),
    ],
)
def test_unusable_answers_leave_the_page_untouched(spec, answer, expected):
    form = FakeForm([spec])
    result = asyncio.run(fill_one(OneField(), form, answer))
    assert (result.status, result.code) == ("left", expected)
    assert form.acted() == []
    assert not spec.value
    assert not spec.typed
    assert not spec.checked


@pytest.mark.parametrize("failure", ["refused", "disappeared", "menu_open"])
def test_combobox_option_is_not_verified_when_the_click_or_readback_fails(monkeypatch, failure):
    spec = Spec("Country", "react", options=("France",))
    form = FakeForm([spec])
    original = form.click

    async def disturbed(*args, **kwargs):
        if failure == "refused":
            return {"ok": False, "error": "host_not_allowed"}
        result = await original(*args, **kwargs)
        if failure == "disappeared":
            form.fields.clear()
        else:
            spec.open = True
        return result

    monkeypatch.setattr(form, "click", disturbed)
    result = asyncio.run(fill_one(OneField(), form, Answer("France", "person", option="France")))
    expected = {
        "refused": ("failed", "host_not_allowed"),
        "disappeared": ("failed", "form_changed"),
        "menu_open": ("unverified", "menu_open"),
    }[failure]
    assert (result.status, result.code) == expected
    assert form.submit_clicks == 0


@pytest.mark.parametrize("failure", ["refused", "disappeared", "not_cleared"])
def test_combobox_missing_option_requires_a_verified_clear(monkeypatch, failure):
    spec = Spec("Country", "react", options=())
    form = FakeForm([spec])
    original = form.type

    async def disturbed(ref, text, *args, **kwargs):
        if text:
            return await original(ref, text, *args, **kwargs)
        if failure == "refused":
            return {"ok": False, "error": "host_not_allowed"}
        if failure == "disappeared":
            form.fields.clear()
        return {"ok": True}

    monkeypatch.setattr(form, "type", disturbed)
    result = asyncio.run(fill_one(OneField(), form, Answer("France", "person")))
    expected = {
        "refused": ("failed", "host_not_allowed"),
        "disappeared": ("failed", "form_changed"),
        "not_cleared": ("unverified", "not_cleared"),
    }[failure]
    assert (result.status, result.code) == expected
    assert not spec.value
    assert form.submit_clicks == 0


def test_file_rejection_is_reported_even_when_the_upload_tool_succeeded():
    spec = Spec("Resume", "file", reject_upload=True)
    form = FakeForm([spec])
    result = asyncio.run(fill_one(OneField(), form, Answer("cv.pdf", "documents.cv")))
    assert (result.status, result.code) == ("failed", "upload_rejected")
    assert not spec.value
    assert form.submit_clicks == 0


@pytest.mark.parametrize(
    ("read_number", "failure", "expected"),
    [
        (2, "captcha", "needs_person"),
        (2, "disappeared", "form_changed"),
        (3, "captcha", "needs_person"),
        (3, "disappeared", "form_changed"),
        (3, "unreadable", "unreadable"),
        (4, "captcha", "needs_person"),
        (4, "unreadable", "unreadable"),
    ],
)
def test_combobox_discovery_stops_before_filling_when_page_changes_between_reads(
    monkeypatch, read_number, failure, expected
):
    form = FakeForm([Spec("Country", "react", options=("France",))])
    original = form.snapshot
    reads = 0

    async def disturbed():
        nonlocal reads
        reads += 1
        if reads == read_number:
            if failure == "unreadable":
                return {"ok": False, "error": "unreadable"}
            if failure == "captcha":
                form.captcha = "instead"
            else:
                form.fields.clear()
        return await original()

    monkeypatch.setattr(form, "snapshot", disturbed)
    found, code = asyncio.run(OneField().discover(form))
    assert code == expected
    if expected == "needs_person":
        assert found.pending == "captcha"
    assert not [name for name, _ in form.calls if name in ("type", "upload", "check")]
    assert form.submit_clicks == 0
    assert not [
        args for name, args in form.calls if name == "click" and args.get("element") != "Country"
    ]
