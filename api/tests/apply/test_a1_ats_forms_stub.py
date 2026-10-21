"""A1: the fake ATS forms server. What it records, how its invisible check decides, its required
fields and their errors, the variants (slow, closed, captcha, layout, assistants, contradict,
hidden, morph, code), files by hash, reset, and that it refuses to run in production. A real
server on a loopback port, spoken to with urllib; no browser (the journey apply-a1.spec.ts
drives the pages)."""

from __future__ import annotations

import hashlib
import re
import urllib.request

import pytest

from tests.apply import a1_support as s
from tests.apply.a1_support import ASHBY_FILES, ASHBY_GOOD, GH_FILES, GH_GOOD, Forms

ASHBY = "/ashby/example-robotics/applied-ai-engineer"
GH = "/greenhouse/northwind-labs/jobs/4012345"


def ashby(variant: str = "") -> str:
    return f"{ASHBY}{'--' + variant if variant else ''}/application"


def gh(variant: str = "") -> str:
    return f"{GH}{'--' + variant if variant else ''}"


# --- the invisible check of Ashby -----------------------------------------------------------------
def test_the_invisible_check_rejects_a_fill_with_no_dwell_and_accepts_a_paced_one(
    forms: Forms,
) -> None:
    quick = forms.post(ashby(), {**ASHBY_GOOD, "_signal_dwell_ms": "400"}, ASHBY_FILES)
    assert quick.json()["ok"] is False
    assert "flagged as possible spam" in quick.json()["message"]
    paced = forms.post(ashby(), ASHBY_GOOD, ASHBY_FILES)
    assert paced.json() == {
        "ok": True,
        "redirect": f"{ASHBY}/application/confirmation",
    }
    first, second = forms.submissions()
    assert (first["accepted"], first["reason"]) == (False, "invisible_check")
    assert (second["accepted"], second["reason"]) == (True, "")


def test_the_invisible_check_rejects_a_webdriver_browser_whatever_its_dwell(forms: Forms) -> None:
    sent = forms.post(ashby(), {**ASHBY_GOOD, "_signal_webdriver": "1"}, ASHBY_FILES)
    assert sent.json()["ok"] is False
    assert forms.submissions()[0]["reason"] == "invisible_check"
    assert forms.submissions()[0]["signals"] == {"dwell_ms": 5000, "webdriver": True}


def test_a_post_with_no_signal_at_all_is_not_a_paced_one(forms: Forms) -> None:
    bare = {k: v for k, v in ASHBY_GOOD.items() if not k.startswith("_signal")}
    assert forms.post(ashby(), bare, ASHBY_FILES).json()["ok"] is False


def test_the_threshold_is_the_servers_own_setting() -> None:
    with s.running(min_dwell_ms=50) as forms:
        quick = {**ASHBY_GOOD, "_signal_dwell_ms": "60"}
        assert forms.post(ashby(), quick, ASHBY_FILES).json()["ok"] is True


# --- what is recorded ----------------------------------------------------------------------------
def test_every_submission_is_recorded_with_its_fields_and_files_and_two_of_one_form_both_count(
    forms: Forms,
) -> None:
    for _ in range(2):
        assert forms.post(ashby(), ASHBY_GOOD, ASHBY_FILES).json()["ok"] is True
    one, two = forms.submissions()
    assert [one["seq"], two["seq"]] == [1, 2]
    assert one["id"] != two["id"]
    assert one["fields"]["_systemfield_name"] == "Alex Example"
    assert one["fields"]["q_sponsor"] == "No"
    assert one["files"][0]["field"] == "_systemfield_resume"
    assert one["files"][0]["filename"] == "cv.pdf"
    assert one["ats"] == "ashby"
    assert one["job"] == "applied-ai-engineer"
    assert "_signal_dwell_ms" not in one["fields"]


def test_files_are_stored_by_hash_once_and_can_be_read_back(forms: Forms) -> None:
    other = s.PDF + b"different"
    forms.post(ashby(), ASHBY_GOOD, ASHBY_FILES)
    forms.post(ashby(), ASHBY_GOOD, ASHBY_FILES)
    forms.post(ashby(), ASHBY_GOOD, {"_systemfield_resume": ("other.pdf", other)})
    sha = hashlib.sha256(s.PDF).hexdigest()
    assert [sub["files"][0]["sha256"] for sub in forms.submissions()] == [
        sha,
        sha,
        hashlib.sha256(other).hexdigest(),
    ]
    assert len(forms.stub.files) == 2
    assert forms.get(f"/_stub/files/{sha}").body == s.PDF
    assert forms.get("/_stub/files/" + "0" * 64).status == 404


def test_reset_forgets_submissions_files_and_mail(forms: Forms) -> None:
    forms.post(gh("code"), GH_GOOD, GH_FILES)
    assert forms.submissions()
    assert forms.get("/_stub/mailbox").json()
    forms.reset()
    assert forms.submissions() == []
    assert forms.get("/_stub/mailbox").json() == []
    assert forms.stub.files == {}


def test_a_file_input_left_empty_is_not_a_file(forms: Forms) -> None:
    body, kind = s.multipart({**ASHBY_GOOD})
    body = body.replace(
        b"--" + kind.split("boundary=")[1].encode() + b"--\r\n",
        b"--"
        + kind.split("boundary=")[1].encode()
        + b'\r\nContent-Disposition: form-data; name="cover_letter"; filename=""\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n\r\n--"
        + kind.split("boundary=")[1].encode()
        + b"--\r\n",
    )
    forms._call(
        urllib.request.Request(  # noqa: S310
            forms.url(ashby()), data=body, headers={"Content-Type": kind}
        )
    )
    sub = forms.submissions()[0]
    assert sub["files"] == []
    assert sub["reason"] == "missing:_systemfield_resume"


# --- required fields -----------------------------------------------------------------------------
def test_a_missing_required_field_is_an_error_under_that_field_and_the_post_is_recorded(
    forms: Forms,
) -> None:
    bad = {k: v for k, v in ASHBY_GOOD.items() if k not in ("q_sponsor", "consent")}
    answer = forms.post(ashby(), bad, ASHBY_FILES)
    assert answer.status == 422
    errors = answer.json()["errors"]
    assert set(errors) == {"q_sponsor", "consent"}
    assert errors["q_sponsor"].startswith("Missing entry for required field")
    sub = forms.submissions()[0]
    assert (sub["accepted"], sub["reason"]) == (False, "missing:consent,q_sponsor")


def test_a_location_that_is_not_one_of_the_options_is_refused(forms: Forms) -> None:
    typed = forms.post(ashby(), {**ASHBY_GOOD, "_systemfield_location": "Paris"}, ASHBY_FILES)
    assert typed.status == 422
    assert "Select a location" in typed.json()["errors"]["_systemfield_location"]
    assert forms.get("/ashby/_locations?q=ber").json() == ["Berlin, Germany"]


def test_greenhouse_needs_the_privacy_box_and_records_the_voluntary_ones_only_when_given(
    forms: Forms,
) -> None:
    no_privacy = {k: v for k, v in GH_GOOD.items() if k != "privacy"}
    refused = forms.post(gh(), no_privacy, GH_FILES)
    assert refused.status == 422
    assert set(refused.json()["errors"]) == {"privacy"}
    accepted = forms.post(
        gh(),
        {**GH_GOOD, "gender": "Decline To Self Identify", "marketing": "on"},
        GH_FILES,
    )
    assert accepted.json() == {"ok": True, "redirect": f"{GH}/confirmation"}
    fields = forms.submissions()[1]["fields"]
    assert fields["gender"] == "Decline To Self Identify"
    assert fields["marketing"] == "on"
    assert "arbitration" not in fields


# --- the variants --------------------------------------------------------------------------------
@pytest.mark.parametrize("ats", ["ashby", "greenhouse"])
def test_the_plain_variant_asks_only_what_any_profile_answers(forms: Forms, ats: str) -> None:
    names = {f.name for f in forms.module.fields_for(ats, "plain")}
    if ats == "ashby":
        assert names == {
            "_systemfield_name",
            "_systemfield_email",
            "_systemfield_resume",
            "cover_letter",
            "consent",
        }
    else:
        assert names == {"first_name", "last_name", "email", "resume", "cover_letter", "privacy"}
    page = forms.get(forms.posting_path(ats, "plain")).text
    for left_out in ("arbitration", "marketing", "q_authorised", "q_eu_work", "q_why", "phone"):
        assert f'name="{left_out}"' not in page


def test_the_plain_greenhouse_form_takes_its_fields_only(forms: Forms) -> None:
    plain = {k: GH_GOOD[k] for k in ("first_name", "last_name", "email", "privacy")}
    assert forms.post(gh("plain"), plain, GH_FILES).json()["ok"] is True
    no_privacy = {k: v for k, v in plain.items() if k != "privacy"}
    assert set(forms.post(gh("plain"), no_privacy, GH_FILES).json()["errors"]) == {"privacy"}


def test_a_closed_posting_answers_not_found_or_a_closed_page_and_takes_no_application(
    forms: Forms,
) -> None:
    page = forms.get(ashby("closed"))
    assert page.status == 404
    assert "was not found" in page.text
    board = forms.get(gh("closed"))
    assert board.status == 200
    assert "no longer open" in board.text
    refused = forms.post(ashby("closed"), ASHBY_GOOD, ASHBY_FILES)
    assert refused.status == 410
    assert forms.submissions()[0]["reason"] == "closed"
    assert forms.get("/ashby/example-robotics/nothing/application").status == 404


def test_the_slow_variant_waits_through_the_injected_sleep_not_the_clock() -> None:
    waits: list[float] = []
    with s.running(sleep=waits.append) as forms:
        assert forms.get(ashby("slow")).status == 200
        assert forms.get(gh("slow")).status == 200
        assert forms.get(ashby()).status == 200
    assert waits == [10.0, 10.0]


def test_the_captcha_variant_needs_the_challenge_solved(forms: Forms) -> None:
    page = forms.get(ashby("captcha")).text
    assert 'class="captcha-challenge"' in page
    unsolved = forms.post(ashby("captcha"), ASHBY_GOOD, ASHBY_FILES)
    assert unsolved.status == 422
    assert forms.submissions()[0]["reason"] == "captcha"
    solved = forms.post(ashby("captcha"), {**ASHBY_GOOD, "captcha_token": "solved"}, ASHBY_FILES)
    assert solved.json()["ok"] is True


def test_the_changed_layout_has_other_names_labels_order_and_widgets(forms: Forms) -> None:
    normal = forms.get(ashby()).text
    changed = forms.get(ashby("layout")).text
    assert 'name="f2__systemfield_name"' in changed
    assert 'name="_systemfield_name"' not in changed
    assert "ashby-application-form-input-yesno" not in changed
    assert "<select" in changed
    assert "Send application" in changed
    assert normal.index("Full name") < normal.index("Why Example Robotics")
    assert changed.index("Why Example Robotics") < changed.index("Full name")
    renamed = {("f2_" + k if not k.startswith("_signal") else k): v for k, v in ASHBY_GOOD.items()}
    files = {"f2__systemfield_resume": ("cv.pdf", s.PDF)}
    assert forms.post(ashby("layout"), renamed, files).json()["ok"] is True
    assert "f2_q_why" in forms.submissions()[0]["fields"]


def test_the_assistants_variant_addresses_ai_assistants_and_the_others_do_not(
    forms: Forms,
) -> None:
    page = forms.get(ashby("assistants")).text
    assert "Note to AI assistants" in page
    assert "Note to AI assistants" not in forms.get(ashby()).text


def test_the_contradict_variant_has_questions_whose_options_contradict_them(
    forms: Forms,
) -> None:
    page = forms.get(ashby("contradict")).text
    assert "Do you not require visa sponsorship?" in page
    assert "Remote work is not possible" in page
    assert "Are you available to work remotely?" in page
    board = forms.get(gh("contradict")).text
    assert "Yes, no sponsorship needed" in board
    assert "No, sponsorship needed" in board


def test_the_hidden_variant_has_hidden_fields_and_a_honeypot_that_must_stay_empty(
    forms: Forms,
) -> None:
    page = forms.get(ashby("hidden")).text
    assert 'name="source" value="careers-page"' in page
    assert 'class="offscreen"' in page
    trapped = forms.post(ashby("hidden"), {**ASHBY_GOOD, "website": "http://x"}, ASHBY_FILES)
    assert trapped.json()["ok"] is False
    assert forms.submissions()[0]["reason"] == "honeypot"
    kept = forms.post(
        ashby("hidden"), {**ASHBY_GOOD, "source": "careers-page", "website": ""}, ASHBY_FILES
    )
    assert kept.json()["ok"] is True
    assert forms.submissions()[1]["fields"]["source"] == "careers-page"


def test_the_morph_variant_adds_a_required_question_after_the_first_input(
    forms: Forms,
) -> None:
    assert "start_date" not in forms.get(ashby("morph")).text
    assert 'data-morph="1"' in forms.get(ashby("morph")).text
    refused = forms.post(ashby("morph"), ASHBY_GOOD, ASHBY_FILES)
    assert set(refused.json()["errors"]) == {"start_date"}
    done = forms.post(ashby("morph"), {**ASHBY_GOOD, "start_date": "2026-12-01"}, ASHBY_FILES)
    assert done.json()["ok"] is True


def test_the_code_variant_asks_for_the_code_that_the_fake_mailbox_holds(forms: Forms) -> None:
    first = forms.post(gh("code"), GH_GOOD, GH_FILES).json()
    assert first["step"] == "code"
    (mail,) = forms.get("/_stub/mailbox").json()
    assert mail["to"] == "e2e-alex@example.org"
    assert re.fullmatch(r"[0-9A-F]{8}", mail["code"])
    wrong = forms.post(
        gh("code"), {"_step": "code", "_submission": first["submission"], "security_code": "NOPE"}
    )
    assert wrong.status == 422
    right = forms.post(
        gh("code"),
        {"_step": "code", "_submission": first["submission"], "security_code": mail["code"]},
    )
    assert right.json()["ok"] is True
    reasons = [(x["step"], x["accepted"], x["reason"]) for x in forms.submissions()]
    assert reasons == [
        ("application", False, "code_required"),
        ("code", False, "bad_code"),
        ("code", True, ""),
    ]


# --- the pages ------------------------------------------------------------------------------------
def test_the_ashby_page_has_yes_no_button_groups_a_typeahead_and_file_inputs(
    forms: Forms,
) -> None:
    page = forms.get(ashby()).text
    assert page.count('class="ashby-application-form-input-yesno"') == 2
    assert 'data-option="yes"' in page
    assert 'data-option="no"' in page
    assert 'data-typeahead="/ashby/_locations"' in page
    assert page.count('type="file"') == 2
    assert "Submit Application" in page


def test_the_greenhouse_page_has_react_select_comboboxes_an_eeo_block_and_three_boxes(
    forms: Forms,
) -> None:
    page = forms.get(gh()).text
    assert page.count('class="select__control"') == 7
    assert 'role="combobox"' in page
    for label in ("Gender", "Hispanic ethnicity", "Veteran status", "Disability status"):
        assert label in page
    for name in ("arbitration", "marketing", "privacy"):
        assert f'name="{name}" type="checkbox"' in page


def test_the_company_site_frames_the_embed_page_which_has_the_same_form(forms: Forms) -> None:
    site = forms.get("/sites/northwind-labs/careers/4012345").text
    src = re.search(r'<iframe[^>]* src="([^"]+)"', site)
    assert src is not None
    embed = forms.get(src.group(1).replace("&amp;", "&"))
    assert 'class="select__control"' in embed.text
    sent = forms.post(
        "/greenhouse/embed/job_app?for=northwind-labs&token=4012345", GH_GOOD, GH_FILES
    )
    assert sent.json()["redirect"] == "/greenhouse/northwind-labs/jobs/4012345/confirmation"
    assert "Thank you for applying." in forms.get(sent.json()["redirect"]).text


def test_a_confirmation_page_per_ats(forms: Forms) -> None:
    assert "successfully submitted" in forms.get(f"{ASHBY}/application/confirmation").text
    assert "Thank you for applying." in forms.get(f"{GH}/confirmation").text


def test_no_page_or_script_names_a_host_outside_the_stub() -> None:
    with s.running(sleep=lambda _: None) as forms:
        pages = [forms.get(row["path"]).text for row in forms.get("/_stub/postings").json()]
        pages += [forms.get("/forms/a1/forms.js").text, forms.get("/forms/a1/forms.css").text]
    assert len(pages) > 15
    for text in pages:
        assert not re.search(r"https?://(?!x\b)", text)
    # The one stand-in for a captcha never loads a script from anyone.
    assert "recaptcha" not in "".join(pages).lower().replace("recaptcha stand-in", "")


def test_the_catalogue_lists_every_variant_of_both_forms(forms: Forms) -> None:
    rows = forms.get("/_stub/postings").json()
    assert {(r["ats"], r["variant"]) for r in rows} >= {
        (ats, v) for ats in ("ashby", "greenhouse") for v in ("", *forms.module.VARIANTS[:-1])
    }
    assert {r["company"] for r in rows} == {"Example Robotics", "Northwind Labs"}


def test_pages_of_another_folder_are_served_and_their_posts_recorded(forms: Forms) -> None:
    assert forms.get("/forms/a1/forms.js").status == 200
    assert forms.get("/forms/nothing/page.html").status == 404
    assert forms.get("/forms/%2e%2e/ats_forms_stub.py").status == 404
    forms.post("/_stub/submit/lever-like", {"name": "Alex"}, {"resume": ("cv.pdf", s.PDF)})
    (sub,) = forms.submissions()
    assert (sub["ats"], sub["org"], sub["fields"]["name"]) == ("custom", "lever-like", "Alex")


# --- production -----------------------------------------------------------------------------------
def test_the_server_refuses_to_start_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = s.load_stub()
    with pytest.raises(RuntimeError, match="APP_ENV is production"):
        module.make_server("127.0.0.1", 0, env={"APP_ENV": "production"})
    monkeypatch.setenv("APP_ENV", "production")
    assert module.main(["--host", "127.0.0.1", "--port", "0"]) == 2
    assert "production" in capsys.readouterr().err
