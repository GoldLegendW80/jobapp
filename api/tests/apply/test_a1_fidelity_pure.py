"""A1 fidelity contracts, with an injected server clock and no database/browser."""

import base64
import json
import re

import pytest

from tests.apply import a1_support as s

STUB = s.load_stub()
KEY = ("ashby", "example-robotics", "applied-ai-engineer")


def test_ashby_groups_have_only_buttons_and_the_upload_is_button_backed() -> None:
    page = STUB.form_page(*KEY, "")
    groups = re.findall(
        r'<div[^>]*class="ashby-application-form-input-yesno"[^>]*>(.*?)</div>', page
    )
    assert len(groups) == 2
    assert all("<input" not in group for group in groups)
    assert 'role="group"' in page
    assert "Autofill from resume" in page
    assert 'data-upload-for="_systemfield_resume"' in page
    assert 'type="file" hidden' in page


def test_greenhouse_custom_questions_have_numeric_ids() -> None:
    fields = STUB.fields_for("greenhouse", "")
    custom = [
        f.name
        for f in fields
        if f.label in ("Country", "How did you hear about this job?")
        or f.label.startswith("Are you legally")
    ]
    assert len(custom) == 3
    assert all(re.fullmatch(r"question_[0-9]+", name) for name in custom)


def test_json_board_payload_preserves_answers_and_file_bytes() -> None:
    payload = {
        "fields": {"question_1001": "Yes", "privacy": "on"},
        "files": [
            {
                "field": "resume",
                "filename": "cv.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(s.PDF).decode(),
            }
        ],
    }
    fields, files = STUB.parse_body("application/json", json.dumps(payload).encode())
    assert fields == {"question_1001": ["Yes"], "privacy": ["on"]}
    assert len(files) == 1
    assert files[0].data == s.PDF


def test_invisible_check_uses_server_input_receipt_and_sticky_environment() -> None:
    clock = [100.0]
    stub = STUB.Stub(clock=lambda: clock[0])
    token = stub.begin(*KEY)
    assert stub.observe(token, {"event": "environment", "webdriver": False})
    assert stub.observe(token, {"event": "input"})
    assert stub.signals(token, *KEY)["dwell_ms"] == 0
    clock[0] += 3
    assert stub.signals(token, *KEY) == {"dwell_ms": 3000, "webdriver": False}
    assert stub.observe(token, {"event": "environment", "webdriver": True})
    assert stub.observe(token, {"event": "environment", "webdriver": False})
    assert stub.signals(token, *KEY)["webdriver"] is True
    assert stub.observe(token, {"event": "input"})
    assert stub.signals(token, *KEY)["dwell_ms"] == 0


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"event": "environment"},
        {"event": "environment", "webdriver": "0"},
        {"event": "environment", "webdriver": 0},
        {"event": "input", "dwell_ms": 999999},
        {"event": "unknown"},
    ],
)
def test_missing_or_invalid_behaviour_does_not_create_a_paced_session(report: dict) -> None:
    clock = [100.0]
    stub = STUB.Stub(clock=lambda: clock[0])
    token = stub.begin(*KEY)
    stub.observe(token, report)
    clock[0] += 20
    assert stub.signals(token, *KEY)["dwell_ms"] is None
    assert stub.signals("unknown", *KEY)["dwell_ms"] is None
    assert stub.signals(token, "ashby", KEY[1], "another-job")["dwell_ms"] is None


def test_forged_submit_flags_without_behaviour_are_rejected_and_recorded() -> None:
    stub = STUB.Stub()
    status, result = stub.evaluate(
        ats=KEY[0],
        org=KEY[1],
        job=KEY[2],
        path="/ashby/example-robotics/applied-ai-engineer/application",
        fields={
            k: [v]
            for k, v in {
                **s.ASHBY_GOOD,
                "_signal_dwell_ms": "999999",
                "_signal_webdriver": "0",
            }.items()
        },
        files=[STUB.Upload("_systemfield_resume", "cv.pdf", "application/pdf", s.PDF)],
    )
    assert status == 200
    assert result["ok"] is False
    assert stub.submissions[0]["reason"] == "invisible_check"
    assert stub.submissions[0]["signals"]["dwell_ms"] is None


def test_a_consumed_session_cannot_authorise_a_second_submission() -> None:
    clock = [100.0]
    stub = STUB.Stub(clock=lambda: clock[0])
    token = stub.begin(*KEY)
    stub.observe(token, {"event": "environment", "webdriver": False})
    stub.observe(token, {"event": "input"})
    clock[0] += 5

    def submit():
        return stub.evaluate(
            ats=KEY[0],
            org=KEY[1],
            job=KEY[2],
            path="/application",
            fields={**{k: [v] for k, v in s.ASHBY_GOOD.items()}, "_session": [token]},
            files=[STUB.Upload("_systemfield_resume", "cv.pdf", "application/pdf", s.PDF)],
        )[1]

    assert submit()["ok"] is True
    assert submit()["ok"] is False
    assert [(x["accepted"], x["reason"]) for x in stub.submissions] == [
        (True, ""),
        (False, "invisible_check"),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"fields": []},
        {"fields": {"q": 1}},
        {"fields": {"q": []}},
        {"fields": {}, "files": {}},
        {"fields": {}, "files": [{}]},
        {
            "fields": {},
            "files": [
                {
                    "field": "resume",
                    "filename": "cv.pdf",
                    "content_type": "application/pdf",
                    "data": "%%%",
                }
            ],
        },
    ],
)
def test_invalid_board_json_is_refused(payload: object) -> None:
    with pytest.raises(ValueError, match=r"invalid application|Only base64 data"):
        STUB.parse_body("application/json", json.dumps(payload).encode())


def test_invalid_behaviour_after_valid_reports_invalidates_the_session() -> None:
    clock = [100.0]
    stub = STUB.Stub(clock=lambda: clock[0])
    token = stub.begin(*KEY)
    assert stub.observe(token, {"event": "environment", "webdriver": False})
    assert stub.observe(token, {"event": "input"})
    assert not stub.observe(token, {"event": "environment", "webdriver": "0"})
    clock[0] += 5
    assert stub.signals(token, *KEY)["dwell_ms"] is None


def test_dwell_does_not_round_up_before_the_threshold_and_reset_forgets_sessions() -> None:
    clock = [100.0]
    stub = STUB.Stub(clock=lambda: clock[0])
    token = stub.begin(*KEY)
    stub.observe(token, {"event": "environment", "webdriver": False})
    stub.observe(token, {"event": "input"})
    clock[0] += 2.9999
    assert stub.signals(token, *KEY)["dwell_ms"] == 2999
    stub.reset()
    assert not stub.observe(token, {"event": "input"})
    assert stub.signals(token, *KEY)["dwell_ms"] is None
