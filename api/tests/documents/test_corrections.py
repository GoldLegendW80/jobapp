"""The automatic corrections (D2): what code changes, what it records, and that it never
leaves a document that cannot render."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import checks, corrections, rules
from app.documents import content as c
from app.documents.corrections import Fallbacks
from app.documents.settings import DocumentSettings
from app.documents.sources import PostingView, sources_for
from app.mail.factcheck_rules import Sources
from tests.documents.conftest import base_cv, posting, view_of, written

OFF = DocumentSettings()
NUMBER = corrections.NUMBER_NOT_STATED
DASH_ON = DocumentSettings(no_dash_separators=True)
EM = "—"


def _sources(kind: c.Kind = "cv") -> Sources:
    base = base_cv("alex")
    return sources_for(view_of("alex", base), base, None, posting("lumen"), kind=kind)


def _fallbacks(language: str = "en") -> Fallbacks:
    base = base_cv("alex")
    view = view_of("alex", base)
    return corrections.fallbacks_for(view, base, posting("lumen"), language)  # type: ignore[arg-type]


def _correct(
    kind: c.Kind, data: dict[str, Any], settings: DocumentSettings = OFF, **fallbacks: Any
) -> tuple[Any, list[corrections.Correction]]:
    result = checks.run(kind, data, _sources(kind), settings)
    assert result.content is not None
    return corrections.remove_remaining(
        result.content, result, fallbacks.get("fallbacks", _fallbacks())
    )


def _cv(**fields: Any) -> dict[str, Any]:
    data = copy.deepcopy(written("cv_short"))
    data.update(fields)
    return data


# --- the dash rule ------------------------------------------------------------------------------


def test_dashes_are_fixed_only_with_the_setting_on() -> None:
    model = c.parse("cv", _cv(headline=f"Platform Engineer {EM} Kubernetes, full-stack"))
    same, records = corrections.fix_separators("cv", model, OFF)
    assert same is model
    assert records == []
    fixed, records = corrections.fix_separators("cv", model, DASH_ON)
    assert fixed.headline == "Platform Engineer, Kubernetes, full-stack"
    assert [r.as_dict() for r in records] == [
        {
            "field_path": "headline",
            "before": f"Platform Engineer {EM} Kubernetes, full-stack",
            "after": "Platform Engineer, Kubernetes, full-stack",
            "code": rules.DASH_SEPARATOR,
            "by": "code",
            "reason": corrections.DASH,
        }
    ]


def test_full_stack_is_never_changed() -> None:
    model = c.parse("cv", _cv(headline="A full-stack engineer"))
    assert corrections.fix_separators("cv", model, DASH_ON) == (model, [])


def test_a_bullet_that_is_only_a_dash_is_dropped() -> None:
    model = c.parse("cv", _cv(highlights=["Moved 40 services to Kubernetes", EM]))
    fixed, records = corrections.fix_separators("cv", model, DASH_ON)
    assert fixed.highlights == ["Moved 40 services to Kubernetes"]
    assert [(r.field_path, r.after) for r in records] == [("highlights.1", "")]


def test_a_field_at_its_limit_is_fixed_without_growing() -> None:
    headline = ("ab" + EM) * 66 + "ab"  # 200 characters, 66 bare dashes
    model = c.parse("cv", _cv(headline=headline))
    fixed, _ = corrections.fix_separators("cv", model, DASH_ON)
    assert len(fixed.headline) <= c.HEADLINE_MAX
    assert EM not in fixed.headline
    assert fixed.headline.startswith("ab,ab,")


# --- step 5: removal by code ------------------------------------------------------------------


def test_only_the_sentence_holding_the_claim_goes_offsets_kept_with_combining_accents() -> None:
    # "q" and "x" with combining marks have no precomposed form: they stay two code points.
    bullet = "Built the q̃ and x́ tools. Led a team of 14 engineers. Wrote the runbooks."
    fixed, records = _correct(
        "cv", _cv(experience=[{"employer_key": "example-corp", "bullets": [bullet]}])
    )
    assert fixed.experience[0].bullets == ["Built the q̃ and x́ tools. Wrote the runbooks."]
    assert [r.as_dict() for r in records] == [
        {
            "field_path": "experience.0.bullets.0",
            "before": "Led a team of 14 engineers.",
            "after": "",
            "code": "number_not_in_sources",
            "by": "code",
            "reason": corrections.NUMBER_NOT_STATED,
        }
    ]


def test_every_highlight_flagged_drops_them_all() -> None:
    data = _cv(
        highlights=["Led a team of 14 people", "Saved 45% of costs", "Worked at Globex Industries"]
    )
    fixed, records = _correct("cv", data)
    assert fixed.highlights == []
    assert [r.field_path for r in records] == ["highlights.0", "highlights.1", "highlights.2"]
    assert {r.reason for r in records} == {
        corrections.NUMBER_NOT_STATED,
        corrections.NOT_IN_PROFILE,
    }


def test_an_emptied_headline_and_summary_take_the_base_cvs() -> None:
    data = _cv(
        headline="Led a team of 14 engineers", summary="Worked at Globex Industries for years."
    )
    fixed, records = _correct("cv", data)
    base = base_cv("alex")
    assert fixed.headline == base.headline
    assert fixed.summary == base.summary
    assert [(r.field_path, r.code, r.after) for r in records if r.code == "fallback"] == [
        ("headline", "fallback", base.headline),
        ("summary", "fallback", base.summary),
    ]
    assert {r.reason for r in records if r.code == "fallback"} == {corrections.FILLED_IN}


def test_an_emptied_letter_subject_names_the_postings_title() -> None:
    data = {**written("letter_typical"), "subject": "Twelve years at Globex Industries"}
    fixed, records = _correct("letter", data)
    assert fixed.subject == "Application for Senior Platform Engineer"
    assert records[-1].field_path == "subject"


def test_a_letter_whose_every_paragraph_is_flagged_takes_the_motivation() -> None:
    data = {
        **written("letter_typical"),
        "paragraphs": ["I led a team of 14 engineers.", "I worked at Globex Industries."],
    }
    fixed, records = _correct("letter", data)
    assert fixed.paragraphs == [view_of("alex").motivation]
    assert (records[-1].field_path, records[-1].code) == ("paragraphs.0", "fallback")


@pytest.mark.parametrize(
    ("language", "posting_", "sentence"),
    [
        (
            "en",
            PostingView("Lumen Example", "Platform Engineer"),
            "I am applying for the Platform Engineer role at Lumen Example.",
        ),
        (
            "en",
            PostingView("", "Platform Engineer"),
            "I am applying for the Platform Engineer role.",
        ),
        ("en", PostingView("Lumen Example", ""), "I am applying for this role at Lumen Example."),
        ("en", None, "I am applying for this role."),
        (
            "fr",
            PostingView("Brise Exemple", "Ingénieure plateforme"),
            "Je vous adresse ma candidature au poste de Ingénieure plateforme chez Brise Exemple.",
        ),
        ("fr", None, "Je vous adresse ma candidature pour ce poste."),
    ],
    ids=["en", "en-no-company", "en-no-title", "en-nothing", "fr", "fr-nothing"],
)
def test_without_a_motivation_the_letter_takes_one_sentence(
    language: str, posting_: PostingView | None, sentence: str
) -> None:
    view = view_of("blake")
    fallbacks = corrections.fallbacks_for(view, None, posting_, language)  # type: ignore[arg-type]
    assert fallbacks.paragraph == sentence
    data = {**written("letter_typical"), "paragraphs": ["I led a team of 14 engineers."]}
    fixed, _ = _correct("letter", data, fallbacks=fallbacks)
    assert fixed.paragraphs == [sentence]


def test_a_skills_group_whose_label_goes_is_dropped_and_recorded() -> None:
    data = _cv(
        skills=[
            {"label": "Globex Industries tools", "items": ["Kubernetes"]},
            {"label": "Cloud", "items": ["Terraform"]},
        ]
    )
    data["skills"][0]["label"] = "Tools of 2015"
    fixed, records = _correct("cv", data)
    assert [g.label for g in fixed.skills] == ["Cloud"]
    assert [(r.field_path, r.before) for r in records] == [
        ("skills.0.label", "Tools of 2015"),
        ("skills.0", "Kubernetes"),
    ]


def test_a_skills_group_whose_items_all_go_is_dropped() -> None:
    data = _cv(
        skills=[{"label": "Cloud", "items": ["Kubernetes since 2015", "Terraform since 2014"]}]
    )
    fixed, records = _correct("cv", data)
    assert fixed.skills == []
    assert [r.field_path for r in records] == ["skills.0.items.0", "skills.0.items.1"]


def test_content_without_claims_is_left_as_it_is() -> None:
    model = c.parse("cv", written("cv_short"))
    result = checks.run("cv", model, _sources(), OFF)
    fixed, records = corrections.remove_remaining(model, result, _fallbacks())
    assert fixed == model
    assert records == []


def test_an_empty_required_field_without_fallback_stays_empty_and_valid() -> None:
    model = c.parse("cv", {"highlights": ["Led a team of 14 engineers"]})
    result = checks.run("cv", model, _sources(), OFF)
    fixed, records = corrections.remove_remaining(model, result, Fallbacks())
    assert (fixed.headline, fixed.summary, fixed.highlights) == ("", "", [])
    assert [r.field_path for r in records] == ["highlights.0"]


def test_a_long_motivation_is_clipped_to_a_paragraph_at_a_sentence() -> None:
    long = "I like platforms. " * 200
    clipped = corrections.clip(long, c.PARAGRAPH_MAX)
    assert len(clipped) <= c.PARAGRAPH_MAX
    assert clipped.endswith("platforms.")
    assert corrections.clip("word " * 1000, 50).endswith("word")
    assert len(corrections.clip("x" * 500, 50)) == 50


@pytest.mark.parametrize(
    ("name", "kind"),
    [("cv_short", "cv"), ("cv_typical", "cv"), ("cv_long", "cv"), ("letter_typical", "letter")],
)
def test_after_the_correction_no_claim_remains_and_it_is_deterministic(
    name: str, kind: c.Kind
) -> None:
    data = copy.deepcopy(written(name))
    noisy = "Led 14 engineers at Globex Industries — for 9 years."
    if kind == "cv":
        data["experience"][0]["bullets"].append(noisy)
    else:
        data["paragraphs"].insert(1, noisy)
    model = c.parse(kind, data)
    model, dash_records = corrections.fix_separators(kind, model, DASH_ON)
    result = checks.run(kind, model, _sources(), DASH_ON)
    first = corrections.remove_remaining(model, result, _fallbacks())
    assert corrections.remove_remaining(model, result, _fallbacks()) == first
    fixed, records = first
    again = checks.run(kind, fixed, _sources(), DASH_ON)
    assert again.claims == ()
    assert again.separators == ()
    assert dash_records
    assert records
    assert {r.reason for r in [*dash_records, *records]} <= set(corrections.REASONS)


def test_20_kb_of_content_is_corrected() -> None:
    sentence = "Led 14 engineers at Globex Industries. Moved 40 services to Kubernetes. "
    roles = [{"employer_key": f"e{i}", "bullets": [(sentence * 4)[:185]] * 8} for i in range(12)]
    model = c.parse("cv", {"summary": (sentence * 10)[:600], "experience": roles})
    result = checks.run("cv", model, _sources(), DASH_ON)
    fixed, records = corrections.remove_remaining(model, result, _fallbacks())
    assert all("Globex" not in b for role in fixed.experience for b in role.bullets)
    assert len(records) >= 12 * 8


# --- step 4: what the writer changed ----------------------------------------------------------


def test_diff_records_what_the_writer_rewrote_or_removed() -> None:
    before = c.parse(
        "cv",
        _cv(
            headline="Led 14 engineers",
            highlights=["Moved 40 services to Kubernetes", "Saved 45% of costs", "Python tools"],
        ),
    )
    issues = checks.run("cv", before, _sources(), OFF).claims
    after = c.parse(
        "cv",
        _cv(
            headline="Platform engineer",
            highlights=["Moved 40 services to Kubernetes", "Python tools"],
            summary="Changed without being asked.",
        ),
    )
    records = corrections.diff_corrections(before, after, issues)
    assert [(r.field_path, r.before, r.after, r.by) for r in records] == [
        ("headline", "Led 14 engineers", "Platform engineer", "writer"),
        ("highlights.1", "Saved 45% of costs", "", "writer"),
    ]
    assert {r.reason for r in records} == {corrections.NUMBER_NOT_STATED}


def test_diff_follows_an_employer_by_its_key_and_skips_what_did_not_change() -> None:
    data = copy.deepcopy(written("cv_typical"))
    data["experience"][1]["bullets"][0] = "Built tools at Globex Industries."
    before = c.parse("cv", data)
    issues = checks.run("cv", before, _sources(), OFF).claims
    moved = copy.deepcopy(data)
    moved["experience"].reverse()
    moved["experience"][0]["bullets"][0] = "Built internal tools for the support team."
    records = corrections.diff_corrections(before, c.parse("cv", moved), issues)
    assert [(r.field_path, r.after) for r in records] == [
        ("experience.1.bullets.0", "Built internal tools for the support team.")
    ]
    # Left as it was: no record, code removes it at step 5.
    assert corrections.diff_corrections(before, before, issues) == []


def test_diff_on_skills_and_paragraphs() -> None:
    letter = {
        **written("letter_typical"),
        "paragraphs": ["I led a team of 14 engineers.", "Thanks."],
    }
    before = c.parse("letter", letter)
    issues = checks.run("letter", before, _sources(), OFF).claims
    after = c.parse("letter", {**letter, "paragraphs": ["Thanks."]})
    assert [
        (r.field_path, r.after) for r in corrections.diff_corrections(before, after, issues)
    ] == [("paragraphs.0", "")]
    cv = _cv(skills=[{"label": "Cloud", "items": ["Kubernetes 2015", "Terraform"]}])
    before = c.parse("cv", cv)
    issues = checks.run("cv", before, _sources(), OFF).claims
    after = c.parse("cv", _cv(skills=[{"label": "Cloud", "items": ["Kubernetes", "Terraform"]}]))
    assert [
        (r.field_path, r.after) for r in corrections.diff_corrections(before, after, issues)
    ] == [("skills.0.items.0", "Kubernetes")]


def test_records_serialise_for_the_version_row() -> None:
    record = corrections.Correction(
        "headline", "a", "", "banned_phrase", "code", corrections.BANNED
    )
    assert corrections.records_json([record]) == [
        {
            "field_path": "headline",
            "before": "a",
            "after": "",
            "code": "banned_phrase",
            "by": "code",
            "reason": "A phrase you banned",
        }
    ]
    assert corrections.reason_for("employer_not_in_profile") == corrections.NOT_IN_PROFILE
    assert corrections.reason_for("year_not_in_sources") == corrections.NUMBER_NOT_STATED


def test_every_single_field_of_a_letter_may_be_emptied() -> None:
    data = {
        **written("letter_typical"),
        "headline": "Engineer since 2015",
        "recipient": "Hiring team at Globex Industries",
        "salutation": "Dear team of 14,",
        "closing": "Twelve years of regards, 2015",
    }
    fixed, records = _correct("letter", data)
    assert (fixed.headline, fixed.recipient, fixed.salutation, fixed.closing) == ("", "", "", "")
    assert [r.field_path for r in records] == ["headline", "recipient", "salutation", "closing"]


def test_a_headline_that_is_only_a_dash_becomes_empty_not_broken() -> None:
    model = c.parse("cv", _cv(headline=EM))
    fixed, records = corrections.fix_separators("cv", model, DASH_ON)
    assert fixed.headline == ""
    assert [(r.field_path, r.after) for r in records] == [("headline", "")]


HOSTILE = [
    ("full width digits", "Led １４ engineers. Wrote runbooks."),
    ("a zero width space inside a number", "Led 1​4 engineers. Wrote runbooks."),
    ("a right to left override", "Led ‮41 engineers. Wrote runbooks."),
    ("a Cyrillic look-alike in an employer", "Worked at Glоbex Industries. Wrote runbooks."),
    ("French", "J'ai dirigé 14 ingénieurs chez Globex Industries. J'écris."),
    ("a very long word", "x" * 299),
    ("only punctuation", "...!!!???"),
    ("many short sentences", "Led 14 people. " * 19 + "Done."),
    ("a dash in every gap", "Led — 14 — people — at — Globex Industries"),
    ("an email and a link", "Write to e2e-x@example.org or see example.org/cv. Done."),
]


@pytest.mark.parametrize("bullet", [h[1] for h in HOSTILE], ids=[h[0] for h in HOSTILE])
def test_hostile_text_always_ends_valid_with_no_claim_left(bullet: str) -> None:
    data = _cv(experience=[{"employer_key": "example-corp", "bullets": [bullet, "Kept."]}])
    data["summary"] = bullet[:600]
    model = c.parse("cv", data)
    model, _ = corrections.fix_separators("cv", model, DASH_ON)
    result = checks.run("cv", model, _sources(), DASH_ON)
    fixed, _ = corrections.remove_remaining(model, result, _fallbacks())
    assert c.parse("cv", c.dump(fixed)) == fixed
    assert checks.run("cv", fixed, _sources(), DASH_ON).claims == ()
    assert "Kept." in fixed.experience[0].bullets


def test_a_version_after_a_profile_term_loses_only_the_number() -> None:
    data = _cv(skills=[{"label": "Cloud", "items": ["Kubernetes 2015", "Terraform 1.5"]}])
    fixed, records = _correct("cv", data)
    assert fixed.skills[0].items == ["Kubernetes", "Terraform"]
    assert [(r.field_path, r.before, r.after, r.code, r.reason) for r in records] == [
        ("skills.0.items.0", "Kubernetes 2015", "Kubernetes", "version_removed", NUMBER),
        ("skills.0.items.1", "Terraform 1.5", "Terraform", "version_removed", NUMBER),
    ]
